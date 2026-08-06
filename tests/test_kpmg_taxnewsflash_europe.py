import pytest

from app.ingestion.kpmg_taxnewsflash_europe import KpmgTaxNewsFlashEuropeAdapter
from app.ingestion.publications_base import PublicationScrapeError

LANDING_HTML = """
<div data-fetch="/kpmg-us/.../gridlist_aug.gridlist.json" data-current-page="1"></div>
<div data-fetch="/kpmg-us/.../gridlist_jul.gridlist.json" data-current-page="1"></div>
"""


def _item(
    url="https://kpmg.com/us/en/taxnewsflash/news/2026/08/example.html",
    title="France: Example ruling on withholding tax",
    description="A summary about withholding tax.",
    sort_time="2026-08-05T07:00:00.000-04:00",
    date_time="05 August 2026",
    category="News",
    tags="topic:geo/international-regions/europe,content-type:news",
):
    return {
        "ctaLink": url,
        "title": title,
        "description": description,
        "sortTime": sort_time,
        "dateTime": date_time,
        "category": category,
        "allTags": tags,
    }


def test_fetch_updates_discovers_months_paginates_and_normalizes():
    adapter = KpmgTaxNewsFlashEuropeAdapter()
    adapter._fetch_landing_page = lambda: LANDING_HTML

    pages = {
        "/kpmg-us/.../gridlist_aug.gridlist.json": {
            1: ([_item()], 1),
        },
        "/kpmg-us/.../gridlist_jul.gridlist.json": {
            1: ([_item(url="https://kpmg.com/.../uk-one.html", title="UK: Windfall levy update")], 2),
            2: ([_item(url="https://kpmg.com/.../uk-two.html", title="Serbia: E-invoicing amended")], 2),
        },
    }
    adapter._fetch_page = lambda fetch_path, page: pages[fetch_path][page]

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 3
    titles = {r.title for r in results}
    assert titles == {
        "France: Example ruling on withholding tax",
        "UK: Windfall levy update",
        "Serbia: E-invoicing amended",
    }

    france = next(r for r in results if r.title.startswith("France"))
    assert france.source == "KPMG_TAXNEWSFLASH_EUROPE"
    assert france.source_label == "KPMG TaxNewsFlash Europe"
    assert france.relevant_jurisdiction == "France"
    assert france.published_date.isoformat() == "2026-08-05"
    assert france.content_type == "News"
    assert france.topic_tags == ["topic:geo/international-regions/europe", "content-type:news"]
    assert "tax" in france.tax_keywords_matched

    uk = next(r for r in results if r.title.startswith("UK"))
    assert uk.relevant_jurisdiction == "United Kingdom"


def test_jurisdiction_falls_back_to_none_for_non_country_prefix():
    adapter = KpmgTaxNewsFlashEuropeAdapter()
    adapter._fetch_landing_page = lambda: LANDING_HTML
    item = _item(title="EU-Mercosur free trade agreement: Provisional application begins")
    adapter._fetch_page = lambda fetch_path, page: ([item], 1) if page == 1 else ([], 1)

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 2  # yielded once per month bucket in this fixture
    assert all(r.relevant_jurisdiction is None for r in results)


def test_fetch_updates_raises_when_landing_page_has_no_gridlists():
    adapter = KpmgTaxNewsFlashEuropeAdapter()
    adapter._fetch_landing_page = lambda: "<html>no gridlists here</html>"

    with pytest.raises(PublicationScrapeError):
        list(adapter.fetch_updates(since=None))


def test_fetch_updates_raises_when_all_pages_empty():
    adapter = KpmgTaxNewsFlashEuropeAdapter()
    adapter._fetch_landing_page = lambda: LANDING_HTML
    adapter._fetch_page = lambda fetch_path, page: ([], 1)

    with pytest.raises(PublicationScrapeError):
        list(adapter.fetch_updates(since=None))
