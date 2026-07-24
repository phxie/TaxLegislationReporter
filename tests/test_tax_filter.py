from app.ingestion.tax_filter import (
    is_tax_relevant_ca,
    is_tax_relevant_federal,
    is_tax_relevant_ny,
    matching_keywords,
)


def test_matching_keywords_case_insensitive():
    assert matching_keywords("An Act Relating To INCOME TAX") == ["income tax", "tax"]


def test_matching_keywords_no_match():
    assert matching_keywords("An act relating to school lunches") == []


def test_federal_policy_area_taxation_is_relevant_regardless_of_text():
    relevant, matched = is_tax_relevant_federal("Taxation", [], "Some unrelated title", None)
    assert relevant is True
    assert matched == ["policy_area:taxation"]


def test_federal_keyword_fallback():
    relevant, matched = is_tax_relevant_federal(
        "Health", ["Health facilities"], "A bill to modify the estate tax", None
    )
    assert relevant is True
    assert "estate tax" in matched


def test_federal_not_relevant():
    relevant, matched = is_tax_relevant_federal(
        "Health", ["Health facilities"], "A bill about hospitals", None
    )
    assert relevant is False
    assert matched == []


def test_ca_taxlevy_flag_short_circuits():
    relevant, matched = is_tax_relevant_ca("Y", None, "AB 1")
    assert relevant is True
    assert matched == ["taxlevy_flag"]


def test_ca_subject_keyword_match():
    relevant, matched = is_tax_relevant_ca("N", "Property taxation: exemption.", "AB 1")
    assert relevant is True
    assert "property tax" in matched


def test_ny_committee_keyword_match():
    relevant, matched = is_tax_relevant_ny("An act relating to schools", None, "Ways and Means Taxation")
    assert relevant is True
    assert "taxation" in matched
