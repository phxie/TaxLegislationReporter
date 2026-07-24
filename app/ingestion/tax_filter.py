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
