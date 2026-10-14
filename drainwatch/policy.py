"""Policy insight: plain-language patterns drawn from the current ranking.

Every sentence is generated from counts in the data on screen, so each claim
can be checked against the queue. It describes reported sites only; it does not
predict where flooding will occur.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Optional

TOP_N = 5
MIN_PATTERN_COUNT = 2
REPEAT_COMPLAINTS = 4  # complaints in 12 months that suggest a recurring cause

_LABELS = {
    "school": ("school access roads", "Clear these first before monsoon."),
    "clinic": ("clinic approach roads", "Clear these first so patients can reach care."),
    "hospital": ("hospital access routes", "Keep these open first so ambulances can get through."),
    "market": ("market roads", "Schedule clearing before peak trading days."),
    "main_road": ("main-road sites", "Clear these first to keep the main route moving."),
}


@dataclass(frozen=True)
class PolicyInsight:
    headline: str
    bullets: tuple
    caveat: str


def _open(ranked: list) -> list:
    return [r for r in ranked if r.hotspot.status != "resolved" and r.result.total is not None]


def policy_insight(ranked: list, rain_24h_mm: Optional[float] = None, top_n: int = TOP_N) -> Optional[PolicyInsight]:
    """Build the insight, or None when there is nothing open to act on."""
    open_items = _open(ranked)
    if not open_items:
        return None
    top = open_items[:top_n]
    n = len(top)

    counts = Counter(
        (r.hotspot.critical_access or "").strip().lower()
        for r in top
        if (r.hotspot.critical_access or "").strip().lower() in _LABELS
    )
    bullets = []
    if counts:
        key, k = counts.most_common(1)[0]
        label, action = _LABELS[key]
        if k >= MIN_PATTERN_COUNT:
            headline = f"Pattern: {k} of the top {n} open sites are {label}. {action}"
        else:
            headline = f"Top priority: {top[0].hotspot.name} ({top[0].result.explain()})"
    else:
        headline = f"Top priority: {top[0].hotspot.name} ({top[0].result.explain()})"

    names = ", ".join(r.hotspot.name for r in top[:3])
    bullets.append(f"Inspect first: {names}.")

    high = sum(1 for r in open_items if r.result.band == "High")
    bullets.append(f"{high} of {len(open_items)} open sites are High priority under the current weights.")

    repeat = [r for r in open_items if (r.hotspot.complaints_12m or 0) >= REPEAT_COMPLAINTS]
    if repeat:
        bullets.append(
            f"{len(repeat)} open site(s) have {REPEAT_COMPLAINTS}+ complaints in 12 months: "
            "check for a structural cause, not just routine clearing."
        )

    unsurveyed = sum(1 for r in open_items if not (r.hotspot.critical_access or "").strip())
    missing_counts = sum(1 for r in open_items if r.hotspot.complaints_12m is None)
    if unsurveyed or missing_counts:
        gaps = []
        if unsurveyed:
            gaps.append(f"{unsurveyed} without an access survey")
        if missing_counts:
            gaps.append(f"{missing_counts} without a complaint count")
        bullets.append("Data gaps to close: " + " and ".join(gaps) + " (these scores are partial).")

    if rain_24h_mm is None:
        bullets.append("Rainfall forecast was unavailable, so scores exclude the rainfall factor.")
    else:
        bullets.append(f"Forecast for the next 24 hours: {rain_24h_mm:.1f} mm, applied to every site equally.")

    caveat = "Based on reported sites and adjustable weights; this ranks sites for inspection and does not predict flooding."
    if all(r.hotspot.is_demo for r in open_items):
        caveat = "Illustration on demo data. " + caveat
    return PolicyInsight(headline, tuple(bullets), caveat)


__all__ = ["PolicyInsight", "policy_insight"]
