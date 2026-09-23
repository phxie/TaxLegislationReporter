from dataclasses import dataclass

from app.ingestion import risk_score
from app.ingestion.risk_score import build_batch_requests, collect_batch_risk_scores


@dataclass
class _FakeBill:
    id: int
    title: str
    ai_summary: str | None = None
    summary: str | None = None


def test_build_batch_requests_prefers_ai_summary_over_summary():
    bills = [
        _FakeBill(
            id=1, title="Income Tax (Amendment) Bill", ai_summary="Raises the top rate.", summary="raw text"
        ),
        _FakeBill(id=2, title="Technical correction bill", ai_summary=None, summary="Fixes a typo."),
        _FakeBill(id=3, title="No content bill", ai_summary=None, summary=None),
    ]

    requests = build_batch_requests(bills)

    assert requests[0]["custom_id"] == "1"
    assert requests[0]["params"]["model"] == risk_score.MODEL
    prompt_1 = requests[0]["params"]["messages"][0]["content"]
    assert "Income Tax (Amendment) Bill" in prompt_1
    assert "Raises the top rate." in prompt_1
    assert "raw text" not in prompt_1

    prompt_2 = requests[1]["params"]["messages"][0]["content"]
    assert "Fixes a typo." in prompt_2

    # Missing content falls back to a placeholder rather than "None"
    prompt_3 = requests[2]["params"]["messages"][0]["content"]
    assert "None" not in prompt_3
    assert "no summary/description available" in prompt_3


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


def test_collect_batch_risk_scores_parses_json_and_skips_failures():
    results = [
        _FakeResult(
            custom_id="1",
            result=_FakeSucceeded(message=_FakeMessage(content=[_TextBlock(text='{"risk_score": 82}')])),
        ),
        _FakeResult(custom_id="2", result=_FakeErrored()),
        # Malformed model output should be skipped, not raise.
        _FakeResult(
            custom_id="3",
            result=_FakeSucceeded(message=_FakeMessage(content=[_TextBlock(text="not json")])),
        ),
        # Out-of-range scores are clamped into [0, 100].
        _FakeResult(
            custom_id="4",
            result=_FakeSucceeded(message=_FakeMessage(content=[_TextBlock(text='{"risk_score": 150}')])),
        ),
    ]
    client = _FakeResultsClient(results)

    scores = collect_batch_risk_scores(client, "batch_1")

    assert scores == {1: 82, 4: 100}
