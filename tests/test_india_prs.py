import datetime as dt

import pytest

from app.ingestion.india_prs import IndiaPrsAdapter, _parse_bill_detail, _parse_bill_list

LIST_HTML = """
<div class="views-row">
<div class="views-field views-field-title-field"> <span class="field-content">
<h3 class="cate"><a
href="/billtrack/the-taxation-and-other-laws-amendment-bill-2026">
The Taxation and Other Laws (Amendment) Bill, 2026</a>
</h3>
</span> </div>
<div class="views-field views-field-field-bill-status"> <span
class="status-pending">Passed</span>
</div>
</div>
<div class="views-row">
<div class="views-field views-field-title-field"> <span class="field-content">
<h3 class="cate"><a
href="/billtrack/the-national-co-operative-development-corporation-amendment-bill-2026">
The National Co-operative Development Corporation (Amendment) Bill, 2026</a>
</h3>
</span> </div>
<div class="views-field views-field-field-bill-status"> <span
class="status-pending">Pending</span>
</div>
</div>
"""

DETAIL_HTML = """
<div class="field field-name-field-ministry field-type-taxonomy-term-reference field-label-inline clearfix">
<div class="field-label">Ministry:&nbsp;</div>
<div class="field-items">
<div class="field-item even">Finance </div>
</div>
</div>
<div class="entity entity-field-collection-item field-collection-item-field-own-status-details clearfix">
<div class="content">
<div class="field field-name-field-own-status field-type-list-text field-label-hidden">
<div class="field-items"><div class="field-item even">Introduced </div></div>
</div>
<div class="field field-name-field-own-status-title field-type-text field-label-hidden">
<div class="field-items"><div class="field-item even">Lok Sabha</div></div>
</div>
<div class="field field-name-field-own-status-date field-type-datestamp field-label-hidden">
<div class="field-items"><div class="field-item even">
<span class="date-display-single">Aug 04, 2026</span></div></div>
</div>
</div>
</div>
<div class="entity entity-field-collection-item field-collection-item-field-own-status-details clearfix">
<div class="content">
<div class="field field-name-field-own-status field-type-list-text field-label-hidden">
<div class="field-items"><div class="field-item even">Passed </div></div>
</div>
<div class="field field-name-field-own-status-title field-type-text field-label-hidden">
<div class="field-items"><div class="field-item even">Rajya Sabha</div></div>
</div>
<div class="field field-name-field-own-status-date field-type-datestamp field-label-hidden">
<div class="field-items"><div class="field-item even">
<span class="date-display-single">Aug 10, 2026</span></div></div>
</div>
</div>
</div>
<div class="body_content">
<div class="field field-name-body field-type-text-with-summary field-label-hidden">
<div class="field-items">
<div class="field-item even">
<p>The Bill amends the Income-tax Act to provide tax exemptions to certain foreign investors.</p>
</div>
</div>
</div>
</div>
"""


def test_parse_bill_list():
    entries = _parse_bill_list(LIST_HTML)

    assert len(entries) == 2
    assert entries[0] == {
        "slug": "the-taxation-and-other-laws-amendment-bill-2026",
        "title": "The Taxation and Other Laws (Amendment) Bill, 2026",
        "status": "Passed",
    }
    assert entries[1]["status"] == "Pending"


def test_parse_bill_detail():
    detail = _parse_bill_detail(DETAIL_HTML)

    assert detail["ministry"] == "Finance"
    assert len(detail["status_events"]) == 2
    assert detail["status_events"][0].event_date == dt.date(2026, 8, 4)
    assert detail["status_events"][0].action_text == "Introduced (Lok Sabha)"
    assert detail["status_events"][1].action_text == "Passed (Rajya Sabha)"
    assert "tax exemptions" in detail["summary"]


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    adapter = IndiaPrsAdapter()
    adapter._fetch_bill_list_html = lambda: LIST_HTML
    adapter._fetch_bill_detail_html = lambda slug: DETAIL_HTML

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "INDIA"
    assert bill.source_bill_id == "the-taxation-and-other-laws-amendment-bill-2026"
    assert bill.session == "2026"
    assert bill.source_label == "PRS Legislative Research"
    assert bill.sponsors == ["Ministry of Finance"]
    assert bill.status_text == "Passed"
    assert bill.introduced_date == dt.date(2026, 8, 4)
    assert bill.last_action_date == dt.date(2026, 8, 10)
    assert bill.source_url == (
        "https://prsindia.org/billtrack/the-taxation-and-other-laws-amendment-bill-2026"
    )
    assert "tax" in bill.tax_keywords_matched


def test_fetch_updates_skips_detail_fetch_for_irrelevant_titles():
    adapter = IndiaPrsAdapter()
    adapter._fetch_bill_list_html = lambda: LIST_HTML

    detail_calls = []

    def _fetch_bill_detail_html(slug):
        detail_calls.append(slug)
        return DETAIL_HTML

    adapter._fetch_bill_detail_html = _fetch_bill_detail_html

    list(adapter.fetch_updates(since=None))

    assert detail_calls == ["the-taxation-and-other-laws-amendment-bill-2026"]


def test_fetch_updates_raises_when_list_page_is_empty():
    adapter = IndiaPrsAdapter()
    adapter._fetch_bill_list_html = lambda: "<html>no bills here</html>"

    with pytest.raises(RuntimeError):
        list(adapter.fetch_updates(since=None))
