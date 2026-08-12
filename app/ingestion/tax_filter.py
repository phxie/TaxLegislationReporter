import re

TAX_KEYWORDS = [
    "tax",
    "taxation",
    "taxpayer",
    "income tax",
    "sales tax",
    "property tax",
    "excise",
    "levy",
    "tariff",
    "internal revenue",
    "revenue code",
    "tax credit",
    "tax deduction",
    "tax exemption",
    "tax rate",
    "estate tax",
    "capital gains",
    "payroll tax",
    "franchise tax",
]


def matching_keywords(*texts: str | None) -> list[str]:
    haystack = " ".join(t.lower() for t in texts if t)
    return sorted({kw for kw in TAX_KEYWORDS if kw in haystack})


def is_tax_relevant_federal(
    policy_area: str | None,
    subjects: list[str],
    title: str,
    summary: str | None,
) -> tuple[bool, list[str]]:
    if policy_area and policy_area.strip().lower() == "taxation":
        return True, ["policy_area:taxation"]
    matched = matching_keywords(title, summary, *subjects)
    return bool(matched), matched


def is_tax_relevant_ca(
    taxlevy_flag: str | None,
    subject: str | None,
    title: str,
) -> tuple[bool, list[str]]:
    if (taxlevy_flag or "").strip().upper() == "Y":
        return True, ["taxlevy_flag"]
    matched = matching_keywords(subject, title)
    return bool(matched), matched


def is_tax_relevant_ny(
    title: str,
    summary: str | None,
    committee: str | None,
) -> tuple[bool, list[str]]:
    matched = matching_keywords(title, summary, committee)
    return bool(matched), matched


def is_tax_relevant_canada(title: str, summary: str | None) -> tuple[bool, list[str]]:
    # LEGISinfo has no policy-area/subject tag like Congress.gov, so this
    # relies entirely on keyword matching -- against the full legislative
    # summary as well as the title, since Canadian tax bills are often titled
    # generically (e.g. "Budget Implementation Act, 2026, No. 1") with the
    # tax content only evident in the summary text.
    matched = matching_keywords(title, summary)
    return bool(matched), matched


# `TAX_KEYWORDS` is English-only, so it can't be reused for Spanish-language
# titles (Congreso de los Diputados; see app/ingestion/spain_congreso.py). A
# separate keyword list, rather than a translation layer, keeps each list
# tuned to its own source's phrasing.
SPANISH_TAX_KEYWORDS = [
    "impuesto",
    "impuestos",
    "tributario",
    "tributaria",
    "tributarios",
    "tributarias",
    "fiscal",
    "fiscalidad",
    "iva",
    "irpf",
    "hacienda",
    "arancel",
    "aranceles",
    "gravamen",
    "gravámenes",
    "tasa",
]


def matching_keywords_es(*texts: str | None) -> list[str]:
    haystack = " ".join(t.lower() for t in texts if t)
    return sorted({kw for kw in SPANISH_TAX_KEYWORDS if kw in haystack})


def is_tax_relevant_spain(title: str) -> tuple[bool, list[str]]:
    matched = matching_keywords_es(title)
    return bool(matched), matched


# Both the UK's and India's main annual tax bill is literally titled
# "Finance Bill" (India: "The Finance Bill, 2026"; UK: "Finance (No. 2)
# Bill"), with no "tax" wording at all -- the same kind of gap
# Congress.gov's policy_area="Taxation" flag exists to cover -- so it's
# special-cased the same way rather than relying on keyword match alone.
_FINANCE_BILL_RE = re.compile(r"\bfinance\s*(\(no\.?\s*\d+\))?\s*bill\b", re.IGNORECASE)


def is_tax_relevant_uk(title: str, summary: str | None) -> tuple[bool, list[str]]:
    if _FINANCE_BILL_RE.search(title):
        return True, ["finance_bill"]
    matched = matching_keywords(title, summary)
    return bool(matched), matched


def is_tax_relevant_india(title: str, summary: str | None) -> tuple[bool, list[str]]:
    # PRS's own "Finance Industry and Labour" category is too broad to use as
    # a short-circuit (it also covers banking/insurance/labour bills with no
    # tax content), and the "Ministry: Finance" field on a bill's detail page
    # has the same problem -- so, like Canada, this relies mainly on keyword
    # matching against the title and PRS's own bill summary, plus the same
    # "Finance Bill" special case as the UK.
    if _FINANCE_BILL_RE.search(title):
        return True, ["finance_bill"]
    matched = matching_keywords(title, summary)
    return bool(matched), matched


# `TAX_KEYWORDS` is English-only, so it can't be reused for French-language
# titles (Assemblée Nationale; see app/ingestion/france_assemblee.py).
FRENCH_TAX_KEYWORDS = [
    "impôt",
    "impôts",
    "imposition",
    "fiscal",
    "fiscale",
    "fiscaux",
    "fiscalité",
    "défiscalisation",
    "taxe",
    "taxes",
    "tva",
    "contribuable",
    "contribuables",
    "exonération fiscale",
    "crédit d'impôt",
    "réduction d'impôt",
    "niche fiscale",
]


def matching_keywords_fr(*texts: str | None) -> list[str]:
    haystack = " ".join(t.lower() for t in texts if t)
    return sorted({kw for kw in FRENCH_TAX_KEYWORDS if kw in haystack})


# France's main annual tax bill is titled "Projet de loi de finances" (the
# Finance Bill) -- the same generically-named-annual-tax-bill gap as the UK
# and India, just in French, so it gets the same special case.
_FRENCH_FINANCE_BILL_RE = re.compile(r"\bloi de finances\b", re.IGNORECASE)


def is_tax_relevant_france(title: str) -> tuple[bool, list[str]]:
    if _FRENCH_FINANCE_BILL_RE.search(title):
        return True, ["finance_bill"]
    matched = matching_keywords_fr(title)
    return bool(matched), matched
