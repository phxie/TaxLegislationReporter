import hashlib

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

# Status text and publication content-type are free text straight from each
# source (e.g. "Baixa comissão distribuição", "Verkündet", "En tramitación"),
# not a normalized enum, so we can't reliably color a pill by what it *means*.
# Instead we hash the string to a stable pill color so the same value always
# renders the same way -- distinguishable at a glance without claiming a
# meaning ("passed", "enacted") we can't actually verify across languages.
_PILL_COLORS = ("pill-blue", "pill-violet", "pill-teal", "pill-green", "pill-amber", "pill-red")


def pill_class(value: str | None) -> str:
    if not value:
        return "pill-neutral"
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()
    return _PILL_COLORS[int(digest, 16) % len(_PILL_COLORS)]


# Unlike pill_class above, risk_score has an actual meaning we can color by:
# low scores are reassuring (green), high scores are a warning (red).
def risk_class(value: int | None) -> str:
    if value is None:
        return "pill-neutral"
    if value >= 67:
        return "pill-red"
    if value >= 34:
        return "pill-amber"
    return "pill-green"


def risk_label(value: int | None) -> str:
    if value is None:
        return "-"
    if value >= 67:
        return "High"
    if value >= 34:
        return "Medium"
    return "Low"


templates.env.filters["pill_class"] = pill_class
templates.env.filters["risk_class"] = risk_class
templates.env.filters["risk_label"] = risk_label
