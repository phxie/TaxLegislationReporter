from app.ingestion.tax_filter import (
    is_tax_relevant_ca,
    is_tax_relevant_federal,
    is_tax_relevant_france,
    is_tax_relevant_germany,
    is_tax_relevant_india,
    is_tax_relevant_ny,
    is_tax_relevant_singapore,
    is_tax_relevant_spain,
    is_tax_relevant_uk,
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


def test_spain_keyword_match():
    relevant, matched = is_tax_relevant_spain(
        "Proyecto de Ley por la que se modifica la Ley 37/1992 del Impuesto sobre el Valor Añadido"
    )
    assert relevant is True
    assert "impuesto" in matched


def test_spain_not_relevant():
    relevant, matched = is_tax_relevant_spain(
        "Proyecto de Ley Orgánica de medidas en materia de violencia vicaria"
    )
    assert relevant is False
    assert matched == []


def test_uk_finance_bill_short_circuits():
    relevant, matched = is_tax_relevant_uk("Finance (No. 2) Bill", None)
    assert relevant is True
    assert matched == ["finance_bill"]


def test_uk_keyword_fallback():
    relevant, matched = is_tax_relevant_uk("Children's Clothing (Value Added Tax) Bill", None)
    assert relevant is True
    assert "tax" in matched


def test_uk_not_relevant():
    relevant, matched = is_tax_relevant_uk("Sporting Events Bill", "A Bill about ticket touting.")
    assert relevant is False
    assert matched == []


def test_india_finance_bill_short_circuits():
    relevant, matched = is_tax_relevant_india("The Finance Bill, 2026", None)
    assert relevant is True
    assert matched == ["finance_bill"]


def test_india_keyword_fallback():
    relevant, matched = is_tax_relevant_india("The Income-tax Bill, 2025", None)
    assert relevant is True
    assert "tax" in matched


def test_india_not_relevant():
    relevant, matched = is_tax_relevant_india(
        "The National Co-operative Development Corporation (Amendment) Bill, 2026", None
    )
    assert relevant is False
    assert matched == []


def test_france_finance_bill_short_circuits():
    relevant, matched = is_tax_relevant_france("Projet de loi de finances pour 2026")
    assert relevant is True
    assert matched == ["finance_bill"]


def test_france_keyword_fallback():
    relevant, matched = is_tax_relevant_france(
        "Proposition de loi visant à baisser la fiscalité de l'électricité"
    )
    assert relevant is True
    assert "fiscalité" in matched


def test_france_not_relevant():
    relevant, matched = is_tax_relevant_france(
        "Proposition de loi visant à favoriser la participation à la vie démocratique"
    )
    assert relevant is False
    assert matched == []


def test_germany_keyword_match_on_title():
    relevant, matched = is_tax_relevant_germany("Gesetz zur Änderung des Einkommensteuergesetzes")
    assert relevant is True
    assert "steuer" in matched


def test_germany_keyword_match_on_abstract():
    relevant, matched = is_tax_relevant_germany(
        "Gesetz über die Feststellung des Bundeshaushaltsplans",
        "Änderung der Abgabenordnung zur Verhinderung von Missbrauch",
    )
    assert relevant is True
    assert "abgabe" in matched


def test_germany_not_relevant():
    relevant, matched = is_tax_relevant_germany(
        "Gesetz über die Feststellung des Bundeshaushaltsplans für das Haushaltsjahr 2026"
    )
    assert relevant is False
    assert matched == []


def test_singapore_keyword_match_on_title():
    relevant, matched = is_tax_relevant_singapore("Goods and Services Tax (Amendment) Bill")
    assert relevant is True
    assert "tax" in matched


def test_singapore_duty_and_customs_keywords_match():
    relevant, matched = is_tax_relevant_singapore("Stamp Duties (Amendment) Bill")
    assert relevant is True
    assert "duties" in matched

    relevant, matched = is_tax_relevant_singapore("Customs (Amendment) Bill")
    assert relevant is True
    assert "customs" in matched


def test_singapore_taxi_is_not_a_false_positive():
    # Real title from the live source -- "Taxi" contains "tax" as a
    # substring, which is exactly why this uses word-boundary matching
    # instead of the shared substring-based matching_keywords().
    relevant, matched = is_tax_relevant_singapore("Third-Party Taxi Booking Service Providers Bill")
    assert relevant is False
    assert matched == []


def test_singapore_not_relevant():
    relevant, matched = is_tax_relevant_singapore("Protection from Online Falsehoods and Manipulation Bill")
    assert relevant is False
    assert matched == []
