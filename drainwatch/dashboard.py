"""Presentation helpers for the Streamlit dashboard.

Kept free of Streamlit imports so they can be unit tested. Anything that
renders HTML escapes user-supplied text (hotspot names and notes come from
CSV files and, later, citizen reports).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Callable, Mapping, MutableMapping, Optional

from .scoring import (
    BAND_HIGH,
    BAND_MEDIUM,
    BAND_MONITOR,
    BAND_UNSCORED,
    FACTOR_LABELS,
    FACTORS,
    RankedHotspot,
    ScoreResult,
)
from .weather import RainSummary, WeatherError
from .workflow import assets_line

# Colour-blind-safe categorical colours, one per scoring factor.
FACTOR_COLORS: Mapping[str, str] = {
    "severity": "#4C78A8",
    "recurrence": "#F58518",
    "rainfall": "#54A24B",
    "access": "#B279A2",
}

BAND_COLORS: Mapping[str, str] = {
    BAND_HIGH: "#C0392B",
    BAND_MEDIUM: "#D68910",
    BAND_MONITOR: "#5D6D7E",
    BAND_UNSCORED: "#95A5A6",
}

# Fallback when the geocoding service is unreachable (plan: 3 preset places).
PRESET_PLACES: Mapping[str, tuple] = {
    "Dhaka": (23.8103, 90.4125),
    "Chattogram": (22.3569, 91.7832),
    "Sylhet": (24.8949, 91.8687),
}

LIMITS_LINE = (
    "DrainWatch BD ranks reported locations for inspection. "
    "It does not predict which street will flood."
)


# ------------------------------------------------------------- rainfall state

@dataclass(frozen=True)
class RainState:
    """Rainfall data plus how trustworthy it is right now."""

    summary: Optional[RainSummary]
    mode: str  # "live", "cached" or "unavailable"
    message: str = ""

    @property
    def rain_24h_mm(self) -> Optional[float]:
        return self.summary.total_24h_mm if self.summary else None


def resolve_rain(
    fetch: Callable[[], RainSummary],
    key,
    store: MutableMapping,
) -> RainState:
    """Try a live fetch; fall back to the last good result, labelled cached.

    If neither exists the state is "unavailable" and scores are computed
    without the rainfall factor (marked partial), never with an invented number.
    """
    try:
        summary = fetch()
    except WeatherError as exc:
        previous = store.get(key)
        if previous is not None:
            return RainState(previous, "cached", f"Live call failed ({exc}) Showing the last good response.")
        return RainState(None, "unavailable", str(exc))
    store[key] = summary
    return RainState(summary, "live")


# ----------------------------------------------------------------- formatting

def fmt_mm(value: Optional[float]) -> str:
    return "missing" if value is None else f"{value:.1f} mm"


def weights_from_percent(percent: Mapping[str, float]) -> dict:
    """Slider values (any non-negative scale) -> weights for the engine."""
    return {f: float(percent.get(f, 0)) for f in FACTORS}


def weight_shares(percent: Mapping[str, float]) -> dict:
    """Slider values -> share of 100% each factor effectively gets."""
    total = sum(float(percent.get(f, 0)) for f in FACTORS)
    if total <= 0:
        return {f: 0.0 for f in FACTORS}
    return {f: float(percent.get(f, 0)) / total * 100 for f in FACTORS}


def raw_input_text(hotspot, factor: str, rain_mm: Optional[float]) -> str:
    """The raw value behind a factor, or 'missing'."""
    if factor == "severity":
        return "missing" if hotspot.severity is None else f"{hotspot.severity} of 5"
    if factor == "recurrence":
        if hotspot.complaints_12m is None:
            return "missing"
        n = hotspot.complaints_12m
        return f"{n} complaint{'s' if n != 1 else ''} in 12 months"
    if factor == "rainfall":
        return "missing" if rain_mm is None else f"{rain_mm:.1f} mm forecast (24h)"
    if factor == "access":
        if not hotspot.critical_access:
            return "missing"
        return hotspot.critical_access.replace("_", " ")
    raise KeyError(factor)


def detail_rows(hotspot, result: ScoreResult, rain_mm: Optional[float]) -> list:
    """One row per factor: input, 0-100 score, weight used and points added."""
    rows = []
    for f in FACTORS:
        score = result.factor_scores[f]
        contribution = result.contributions[f]
        rows.append(
            {
                "Factor": FACTOR_LABELS[f],
                "Input": raw_input_text(hotspot, f, rain_mm),
                "Factor score (0-100)": None if score is None else round(score, 1),
                "Weight used": f"{result.effective_weights[f] * 100:.0f}%" if score is not None else "dropped",
                "Points added": None if contribution is None else round(contribution, 1),
            }
        )
    return rows


# ----------------------------------------------------------------------- HTML

def legend_html() -> str:
    chips = "".join(
        f'<span class="dw-chip"><i style="background:{FACTOR_COLORS[f]}"></i>{escape(FACTOR_LABELS[f])}</span>'
        for f in FACTORS
    )
    return f'<div class="dw-legend">{chips}</div>'


def stacked_bar_html(result: ScoreResult) -> str:
    """A bar whose filled length equals the score; one segment per factor."""
    if result.total is None:
        return '<div class="dw-bar dw-bar-empty" title="Not scored"></div>'
    segments = []
    for f in FACTORS:
        points = result.contributions[f]
        if not points:
            continue
        title = f"{FACTOR_LABELS[f]}: +{points:.1f} points"
        segments.append(
            f'<span style="width:{points:.2f}%;background:{FACTOR_COLORS[f]}" title="{escape(title)}"></span>'
        )
    return f'<div class="dw-bar">{"".join(segments)}</div>'


def band_pill_html(band: str) -> str:
    color = BAND_COLORS.get(band, BAND_COLORS[BAND_UNSCORED])
    return f'<span class="dw-pill" style="background:{color}">{escape(band)}</span>'


def queue_html(ranked: list) -> str:
    """The ranked inspection queue as an HTML table with a breakdown bar per row."""
    head = (
        "<tr><th>#</th><th>Hotspot</th><th>Score</th><th>Band</th>"
        "<th>Why (bar length = score)</th><th>Status</th></tr>"
    )
    body = []
    for item in ranked:
        h, res = item.hotspot, item.result
        score = "n/a" if res.total is None else f"{res.total:.1f}"
        tags = []
        if res.partial:
            tags.append('<span class="dw-tag" title="Some inputs are missing; remaining weights re-normalised">partial score</span>')
        if getattr(h, "is_demo", False):
            tags.append('<span class="dw-tag dw-tag-demo">demo</span>')
        sub = f'<div class="dw-sub">Affects: {escape(assets_line(h))}</div>'
        resolved = " dw-done" if h.status == "resolved" else ""
        body.append(
            f'<tr class="{resolved.strip()}">'
            f"<td>{item.rank}</td>"
            f'<td><div class="dw-name">{escape(h.name)} {"".join(tags)}</div>{sub}</td>'
            f'<td class="dw-score">{score}</td>'
            f"<td>{band_pill_html(res.band)}</td>"
            f"<td>{stacked_bar_html(res)}</td>"
            f'<td class="dw-status">{escape(h.status)}</td>'
            "</tr>"
        )
    return f'<table class="dw-table"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


CSS = """
<style>
.dw-legend{display:flex;flex-wrap:wrap;gap:14px;margin:4px 0 10px;font-size:.85rem}
.dw-chip i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.dw-table{width:100%;border-collapse:collapse;font-size:.92rem}
.dw-table th{text-align:left;font-weight:600;padding:6px 8px;border-bottom:2px solid rgba(128,128,128,.35);font-size:.8rem;opacity:.8}
.dw-table td{padding:8px;border-bottom:1px solid rgba(128,128,128,.22);vertical-align:middle}
.dw-name{font-weight:600}
.dw-sub{font-size:.78rem;opacity:.7;margin-top:2px}
.dw-score{font-weight:700;font-size:1.05rem;white-space:nowrap}
.dw-pill{color:#fff;padding:2px 9px;border-radius:999px;font-size:.78rem;font-weight:600}
.dw-bar{display:flex;height:14px;min-width:120px;border-radius:7px;background:rgba(128,128,128,.18);overflow:hidden}
.dw-bar span{display:block;height:100%}
.dw-tag{font-size:.68rem;font-weight:600;padding:1px 6px;border-radius:4px;border:1px solid rgba(128,128,128,.5);margin-left:4px;opacity:.85;white-space:nowrap}
.dw-tag-demo{border-style:dashed}
.dw-status{text-transform:capitalize;white-space:nowrap}
.dw-done{opacity:.5}
.dw-loop{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 12px}
.dw-step{flex:1 1 150px;border:1px solid rgba(128,128,128,.35);border-radius:8px;padding:8px 10px;font-size:.85rem}
.dw-step b{display:block}.dw-step span{opacity:.75;font-size:.78rem}
@media (max-width:640px){.dw-table th:nth-child(5),.dw-table td:nth-child(5){display:none}}
</style>
"""

__all__ = [
    "FACTOR_COLORS",
    "BAND_COLORS",
    "PRESET_PLACES",
    "LIMITS_LINE",
    "CSS",
    "RainState",
    "resolve_rain",
    "fmt_mm",
    "weights_from_percent",
    "weight_shares",
    "raw_input_text",
    "detail_rows",
    "legend_html",
    "stacked_bar_html",
    "band_pill_html",
    "queue_html",
]
