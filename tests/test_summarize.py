from dataclasses import dataclass

from app.ingestion import summarize
from app.ingestion.summarize import (
    SummaryCandidate,
    build_batch_requests,
    collect_batch_summaries,
    wait_for_batch,
)


def test_build_batch_requests_includes_title_and_content():
    candidates = [
        SummaryCandidate(id=1, title="France: New VAT rule", content="A summary about VAT."),
        SummaryCandidate(id=2, title="No summary case", content=None),
    ]

    requests = build_batch_requests(candidates)

    assert requests[0]["custom_id"] == "1"
    assert requests[0]["params"]["model"] == summarize.MODEL
    prompt = requests[0]["params"]["messages"][0]["content"]
    assert "France: New VAT rule" in prompt
    assert "A summary about VAT." in prompt

    # Missing content falls back to a placeholder rather than "None"
    prompt_2 = requests[1]["params"]["messages"][0]["content"]
    assert "None" not in prompt_2
    assert "no summary/description available" in prompt_2


def test_build_batch_requests_uses_bill_prompt_template_when_given():
    candidates = [SummaryCandidate(id=1, title="Income Tax (Amendment) Bill", content="Raises the rate.")]

    requests = build_batch_requests(candidates, prompt_template=summarize.BILL_PROMPT_TEMPLATE)

    prompt = requests[0]["params"]["messages"][0]["content"]
    assert "piece of tax legislation" in prompt
    assert "Income Tax (Amendment) Bill" in prompt
    assert "Raises the rate." in prompt
    # The publication-flavored wording shouldn't leak into the bill prompt.
    assert "article" not in prompt


@dataclass
class _FakeBatch:
    processing_status: str


class _FakeBatches:
    def __init__(self, statuses):
        self._statuses = iter(statuses)

    def retrieve(self, batch_id):
        return _FakeBatch(processing_status=next(self._statuses))


class _FakeClient:
    def __init__(self, statuses):
        self.messages = type("_M", (), {"batches": _FakeBatches(statuses)})()


def test_wait_for_batch_returns_true_once_ended(monkeypatch):
    monkeypatch.setattr(summarize.time, "sleep", lambda _seconds: None)
    client = _FakeClient(["in_progress", "in_progress", "ended"])

    result = wait_for_batch(client, "batch_1", poll_interval_seconds=0, max_wait_seconds=100)

    assert result is True


def test_wait_for_batch_returns_false_on_timeout(monkeypatch):
    monkeypatch.setattr(summarize.time, "sleep", lambda _seconds: None)
    # Simulate the clock advancing past the deadline on the second check.
    clock = iter([0.0, 0.0, 200.0])
    monkeypatch.setattr(summarize.time, "monotonic", lambda: next(clock))
    client = _FakeClient(["in_progress", "in_progress", "in_progress"])

    result = wait_for_batch(client, "batch_1", poll_interval_seconds=0, max_wait_seconds=100)

    assert result is False


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeMessage:
    content: list


@dataclass
class _FakeSucceeded:
    message: _FakeMessage
    type: str = "succeeded"


@dataclass
class _FakeErrored:
    type: str = "errored"


@dataclass
class _FakeResult:
    custom_id: str
    result: object


class _FakeResultsBatches:
    def __init__(self, results):
        self._results = results

    def results(self, batch_id):
        return iter(self._results)


class _FakeResultsClient:
    def __init__(self, results):
        self.messages = type("_M", (), {"batches": _FakeResultsBatches(results)})()


def test_collect_batch_summaries_extracts_text_and_skips_failures():
    results = [
        _FakeResult(
            custom_id="1",
            result=_FakeSucceeded(message=_FakeMessage(content=[_TextBlock(text="  Summary one.  ")])),
        ),
        _FakeResult(custom_id="2", result=_FakeErrored()),
    ]
    client = _FakeResultsClient(results)

    summaries = collect_batch_summaries(client, "batch_1")

    assert summaries == {1: "Summary one."}
