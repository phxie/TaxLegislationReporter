import pytest

from app.ingestion.pwc_tax_library import PublicationScrapeError, PwcTaxLibraryAdapter

PAGE_1 = [
    {
        "href": "https://www.pwc.com/us/en/services/tax/library/article-one.html",
        "title": "New income tax credit proposed",
        "text": "A summary about tax credits.",
        "publishDate": "July 08, 2026",
        "tags": ["pwc-xx:content-type/publication", "pwc-us:collections/tax"],
        "isVideo": False,
    },
    {
        "href": "https://www.pwc.com/us/en/services/tax/library/podcast-one.html",
        "title": "Cross-border tax talks",
        "text": "A podcast about tax.",
        "publishDate": "July 07, 2026",
        "tags": ["pwc-content-type:podcast"],
        "isVideo": False,
    },
]

PAGE_2 = [
    {
        "href": "https://www.pwc.com/us/en/services/tax/library/article-two.html",
        "title": "Texas enacts franchise tax update",
        "text": "A summary about state tax.",
        "publishDate": "July 01, 2026",
        "tags": ["pwc-xx:content-type/publication"],
        "isVideo": False,
    },
]


def test_fetch_updates_paginates_and_normalizes():
    adapter = PwcTaxLibraryAdapter()
    pages = {0: (PAGE_1, 3), 2: (PAGE_2, 3)}
    adapter._fetch_page = lambda offset: pages[offset]

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 3
    assert results[0].title == "New income tax credit proposed"
    assert results[0].source == "PWC_TAX_LIBRARY"
    assert results[0].source_label == "PwC Tax Library"
    assert results[0].published_date.isoformat() == "2026-07-08"
    assert results[0].content_type == "Publication"
    assert "tax" in results[0].tax_keywords_matched
    assert results[1].content_type == "Podcast"
    assert results[2].title == "Texas enacts franchise tax update"
    assert results[2].relevant_jurisdiction == "Texas"


def test_fetch_updates_stops_when_page_empty():
    adapter = PwcTaxLibraryAdapter()
    adapter._fetch_page = lambda offset: ([], 0)

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_fetch_updates_raises_when_items_reported_but_none_extracted():
    adapter = PwcTaxLibraryAdapter()
    # Elements missing required fields (href/title) get skipped by _normalize,
    # so a nonzero numberHits with nothing usable should be a hard failure.
    adapter._fetch_page = lambda offset: ([{"text": "no title or href"}], 1)

    with pytest.raises(PublicationScrapeError):
        list(adapter.fetch_updates(since=None))
