"""Best-effort "which jurisdiction is this article about" heuristic.

PwC's own tags only encode service-line/topic categories (e.g.
"state-local-tax" vs "international-tax"), not specific states or
countries, so this infers a jurisdiction label from the article's title and
summary text instead. It's informational, not authoritative -- same trust
level as `tax_filter.matching_keywords`.
"""

from __future__ import annotations

import re

US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
)  # fmt: skip

RELEVANT_JURISDICTIONS = US_STATES + ("Federal", "International", "Multistate")

# Case-insensitive; word-boundary matched against the title+summary text.
FEDERAL_PHRASES = (
    "irs",
    "congress",
    "senate",
    "treasury",
    "tax court",
    "ustr",
    "white house",
    "reconciliation bill",
    "ways and means",
    "supreme court",
    "customs and border protection",
    "obbba",
    "united states",
    "house of representatives",
)

# Common non-US countries that show up in PwC's international tax coverage
# (cross-border podcasts, treaty updates, etc.). Case-insensitive.
COUNTRY_KEYWORDS = (
    "japan", "taiwan", "canada", "mexico", "china", "united kingdom",
    "germany", "france", "india", "australia", "singapore", "ireland",
    "brazil", "korea", "switzerland", "netherlands", "italy", "spain",
)  # fmt: skip

# "US"/"U.S." as a country abbreviation is checked case-sensitively --
# lowercase "us" is too often the pronoun to match safely either way.
_US_ABBREVIATION_RE = re.compile(r"\bU\.S\.|\bUS\b")

# "Washington, D.C." / "Washington DC" would otherwise match the state of
# Washington -- normalize it to "district of columbia" before state matching
# so it resolves to the right entry in US_STATES instead.
_DC_RE = re.compile(r"washington,?\s*d\.?c\.?", re.IGNORECASE)


def _any_word_match(keywords: tuple[str, ...], text_lower: str) -> bool:
    return any(re.search(rf"\b{re.escape(kw)}\b", text_lower) for kw in keywords)


def detect_relevant_jurisdiction(title: str, summary: str | None) -> str | None:
    text = f"{title} {summary or ''}"
    text_lower = _DC_RE.sub("district of columbia", text.lower())

    matched_states = [
        state for state in US_STATES if re.search(rf"\b{re.escape(state.lower())}\b", text_lower)
    ]
    if len(matched_states) == 1:
        return matched_states[0]
    if len(matched_states) > 1:
        return "Multistate"

    if _any_word_match(FEDERAL_PHRASES, text_lower) or _US_ABBREVIATION_RE.search(text):
        return "Federal"

    if _any_word_match(COUNTRY_KEYWORDS, text_lower):
        return "International"

    return None
