from app.ingestion.jurisdiction_detect import detect_relevant_jurisdiction


def test_single_state_match():
    assert (
        detect_relevant_jurisdiction("Massachusetts requires tax modifications", None)
        == "Massachusetts"
    )


def test_multiple_states_match_multistate():
    result = detect_relevant_jurisdiction("California and New York align on new SaaS tax rules", None)
    assert result == "Multistate"


def test_federal_keyword_match():
    assert (
        detect_relevant_jurisdiction(
            "House passes budget resolution for third reconciliation bill", None
        )
        == "Federal"
    )


def test_irs_keyword_match():
    assert detect_relevant_jurisdiction("IRS provides transitional guidance", None) == "Federal"


def test_us_abbreviation_is_case_sensitive():
    assert detect_relevant_jurisdiction("US imposes new Section 338 tariffs", None) == "Federal"
    # lowercase "us" should not trigger a false match via the pronoun sense
    assert detect_relevant_jurisdiction("New guidance helps us understand tips", None) is None


def test_country_name_does_not_leak_from_adjective_form():
    # "Canadian" should not match the "canada" keyword (word-boundary check)
    assert detect_relevant_jurisdiction("US imposes tariffs on certain Canadian imports", None) == "Federal"


def test_international_keyword_match():
    assert detect_relevant_jurisdiction("Japan Tax Update: Tea, Tariffs and Top-up Taxes", None) == (
        "International"
    )


def test_washington_dc_disambiguated_from_washington_state():
    assert (
        detect_relevant_jurisdiction("Washington DC's statutory residency rules", None)
        == "District of Columbia"
    )
    assert (
        detect_relevant_jurisdiction("Washington enacts a new tax on millionaires", None)
        == "Washington"
    )


def test_no_match_returns_none():
    assert detect_relevant_jurisdiction("Accounting Methods Spotlight Q2 2026", None) is None


def test_falls_back_to_summary():
    assert (
        detect_relevant_jurisdiction("Quarterly Newsletter", "Covers new Texas franchise tax rules")
        == "Texas"
    )
