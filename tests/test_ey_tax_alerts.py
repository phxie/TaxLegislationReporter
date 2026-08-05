import pytest

from app.ingestion.ey_tax_alerts import EyTaxAlertsAdapter
from app.ingestion.publications_base import PublicationScrapeError


def _item(
    url="https://www.ey.com/en_gl/technical/tax-alerts/example",
    title="Example corporate tax update",
    category="Corporate Tax",
    jurisdiction="Canada",
    date="2026-07-30T00:00:00+00:00",
    summary="A summary about corporate tax.",
):
    return {
        "url": {"raw": url},
        "pagetitle": {"raw": title},
        "pagedescription": {"raw": summary},
        "dateuser": {"raw": date},
        "category_label": {"raw": category},
        "jurisdiction_label": {"raw": jurisdiction},
    }


PAGE_1 = [
    _item(url="https://www.ey.com/en_gl/technical/tax-alerts/tax-one", title="Tax alert one"),
    _item(
        url="https://www.ey.com/en_gl/technical/tax-alerts/immigration-one",
        title="Visa restrictions update",
        category="Immigration",
        jurisdiction="Guinea",
    ),
    _item(
        url="https://www.ey.com/en_gl/technical/tax-alerts/federal-one",
        title="US federal tax update",
        jurisdiction="United States",
    ),
]

PAGE_2 = [
    _item(url="https://www.ey.com/en_gl/technical/tax-alerts/tax-two", title="Tax alert two"),
]


def test_fetch_updates_paginates_normalizes_and_excludes_immigration():
    adapter = EyTaxAlertsAdapter()
    pages = {1: (PAGE_1, 4), 2: (PAGE_2, 4), 3: ([], 4)}
    adapter._fetch_page = lambda page: pages[page]

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 3
    assert results[0].title == "Tax alert one"
    assert results[0].source == "EY_TAX_ALERTS"
    assert results[0].source_label == "EY Tax Alerts"
    assert results[0].published_date.isoformat() == "2026-07-30"
    assert results[0].relevant_jurisdiction == "Canada"
    assert results[0].content_type == "Corporate Tax"
    assert "tax" in results[0].tax_keywords_matched

    titles = [r.title for r in results]
    assert "Visa restrictions update" not in titles

    federal_item = next(r for r in results if r.title == "US federal tax update")
    assert federal_item.relevant_jurisdiction == "Federal"

    assert results[2].title == "Tax alert two"


def test_fetch_updates_raises_when_no_pages_return_items():
    adapter = EyTaxAlertsAdapter()
    adapter._fetch_page = lambda page: ([], 0)

    with pytest.raises(PublicationScrapeError):
        list(adapter.fetch_updates(since=None))


def test_all_immigration_page_yields_nothing_without_error():
    # Filtering everything out as non-tax is a legitimate outcome, not a
    # scrape failure -- only zero *raw* items across all pages is an error.
    adapter = EyTaxAlertsAdapter()
    all_immigration = [_item(category="Immigration")]
    adapter._fetch_page = lambda page: (all_immigration, 1) if page == 1 else ([], 1)

    results = list(adapter.fetch_updates(since=None))

    assert results == []
