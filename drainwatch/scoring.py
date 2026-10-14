"""Explainable priority scoring for reported drainage hotspots.

Design rules (see the project plan):
  * Four factors, each normalised to 0-100, combined with adjustable weights.
  * Weights, caps and thresholds are DESIGN ASSUMPTIONS, not validated
    engineering coefficients. They are exposed as parameters so the dashboard
    can show them as sliders.
  * Missing data is never treated as zero. A missing factor is dropped and the
    remaining weights are re-normalised; the row is flagged as a partial score.
  * The score ranks REPORTED locations for inspection. It does not predict
    where flooding will occur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

# ---------------------------------------------------------------- assumptions

FACTORS = ("severity", "recurrence", "rainfall", "access")

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "severity": 0.40,
    "recurrence": 0.25,
    "rainfall": 0.25,
    "access": 0.10,
}

FACTOR_LABELS: Mapping[str, str] = {
    "severity": "Reported severity",
    "recurrence": "Recurrence",
    "rainfall": "Rainfall exposure",
    "access": "Critical access",
}

# 24 hour forecast rainfall (mm) that maps to a rainfall score of 100.
RAIN_FULL_MM = 80.0
# Complaints in the last 12 months that map to a recurrence score of 100.
RECURRENCE_CAP = 5

HIGH_THRESHOLD = 70.0
MEDIUM_THRESHOLD = 40.0

# Critical access categories -> score. Anything not listed is treated as
# "other facility" (see access_score).
ACCESS_SCORES: Mapping[str, float] = {
    "school": 100.0,
    "clinic": 100.0,
    "hospital": 100.0,
    "market": 50.0,
    "main_road": 50.0,
    "none": 0.0,
}

BAND_HIGH = "High"
BAND_MEDIUM = "Medium"
BAND_MONITOR = "Monitor"
BAND_UNSCORED = "Unscored"


# --------------------------------------------------------------- factor maths

def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def severity_score(severity: Optional[float]) -> Optional[float]:
    """Report rating 1..5 -> 0..100 (linear). None if missing or out of range."""
    if severity is None:
        return None
    if not 1 <= severity <= 5:
        return None
    return (severity - 1) / 4 * 100


def recurrence_score(complaints_12m: Optional[float]) -> Optional[float]:
    """Complaints in 12 months, capped at RECURRENCE_CAP -> 0..100."""
    if complaints_12m is None or complaints_12m < 0:
        return None
    return _clamp(min(complaints_12m, RECURRENCE_CAP) / RECURRENCE_CAP * 100)


def rainfall_score(rain_24h_mm: Optional[float], full_mm: float = RAIN_FULL_MM) -> Optional[float]:
    """Forecast 24 hour rainfall in mm -> 0..100, capped at full_mm."""
    if rain_24h_mm is None or rain_24h_mm < 0:
        return None
    return _clamp(rain_24h_mm / full_mm * 100)


def access_score(category: Optional[str]) -> Optional[float]:
    """Critical access category -> 0, 50 or 100. None/blank means missing."""
    if category is None:
        return None
    key = category.strip().lower().replace(" ", "_")
    if not key:
        return None
    if key in ACCESS_SCORES:
        return ACCESS_SCORES[key]
    # Unknown but explicitly stated facility (e.g. "mosque", "bus_stand"):
    # treat as a moderate access concern rather than silently as zero.
    return 50.0


def band_for(score: Optional[float]) -> str:
    if score is None:
        return BAND_UNSCORED
    if score >= HIGH_THRESHOLD:
        return BAND_HIGH
    if score >= MEDIUM_THRESHOLD:
        return BAND_MEDIUM
    return BAND_MONITOR


# ------------------------------------------------------------------- results

@dataclass(frozen=True)
class ScoreResult:
    """The score for one hotspot, with everything needed to explain it."""

    total: Optional[float]
    band: str
    factor_scores: Mapping[str, Optional[float]]
    # Points each factor contributed to `total` (after re-weighting).
    contributions: Mapping[str, Optional[float]]
    # Weights actually used after dropping missing factors (sum to 1).
    effective_weights: Mapping[str, float]
    missing: tuple = ()

    @property
    def partial(self) -> bool:
        return bool(self.missing) and self.total is not None

    def explain(self) -> str:
        """One plain sentence naming the biggest drivers."""
        if self.total is None:
            return "Not scored: no usable inputs."
        ranked = sorted(
            ((k, v) for k, v in self.contributions.items() if v is not None),
            key=lambda kv: kv[1],
            reverse=True,
        )
        top = [FACTOR_LABELS[k].lower() for k, v in ranked[:2] if v > 0]
        text = f"{self.band} ({self.total:.1f})"
        if top:
            text += " mainly because of " + " and ".join(top)
        if self.partial:
            text += "; partial score, missing " + ", ".join(
                FACTOR_LABELS[m].lower() for m in self.missing
            )
        return text + "."


def normalise_weights(weights: Mapping[str, float]) -> dict:
    """Validate and return weights for the four factors (as given, floats)."""
    out = {}
    for f in FACTORS:
        w = float(weights.get(f, 0.0))
        if w < 0:
            raise ValueError(f"Weight for {f} must not be negative")
        out[f] = w
    if sum(out.values()) <= 0:
        raise ValueError("At least one weight must be above zero")
    return out


def score_inputs(
    severity: Optional[float],
    complaints_12m: Optional[float],
    critical_access: Optional[str],
    rain_24h_mm: Optional[float],
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    rain_full_mm: float = RAIN_FULL_MM,
) -> ScoreResult:
    """Score one location from raw inputs."""
    w = normalise_weights(weights)
    factor_scores = {
        "severity": severity_score(severity),
        "recurrence": recurrence_score(complaints_12m),
        "rainfall": rainfall_score(rain_24h_mm, rain_full_mm),
        "access": access_score(critical_access),
    }
    # A factor is usable only if it has data AND a positive weight.
    available = [f for f in FACTORS if factor_scores[f] is not None and w[f] > 0]
    missing = tuple(f for f in FACTORS if factor_scores[f] is None and w[f] > 0)

    if not available:
        return ScoreResult(
            total=None,
            band=BAND_UNSCORED,
            factor_scores=factor_scores,
            contributions={f: None for f in FACTORS},
            effective_weights={f: 0.0 for f in FACTORS},
            missing=missing,
        )

    weight_sum = sum(w[f] for f in available)
    effective = {f: (w[f] / weight_sum if f in available else 0.0) for f in FACTORS}
    contributions = {
        f: (factor_scores[f] * effective[f] if f in available else None) for f in FACTORS
    }
    total = round(sum(v for v in contributions.values() if v is not None), 1)
    return ScoreResult(
        total=total,
        band=band_for(total),
        factor_scores=factor_scores,
        contributions=contributions,
        effective_weights=effective,
        missing=missing,
    )


@dataclass(frozen=True)
class RankedHotspot:
    rank: int
    hotspot: object  # drainwatch.data.Hotspot (kept loose to avoid a cycle)
    result: ScoreResult


def rank_hotspots(
    hotspots: Iterable,
    rain_24h_mm: Optional[float],
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
    rain_full_mm: float = RAIN_FULL_MM,
) -> list:
    """Score and rank hotspots, highest priority first.

    Resolved hotspots are kept in the list but sorted after open ones, so the
    queue shows what still needs action. Ties break on severity, then
    recurrence, then name, so ordering is stable and explainable.
    """
    scored = []
    for h in hotspots:
        res = score_inputs(
            h.severity,
            h.complaints_12m,
            h.critical_access,
            rain_24h_mm,
            weights,
            rain_full_mm,
        )
        scored.append((h, res))

    def key(item):
        h, res = item
        resolved = 1 if getattr(h, "status", "") == "resolved" else 0
        unscored = 1 if res.total is None else 0
        total = res.total if res.total is not None else 0.0
        sev = res.factor_scores["severity"] or 0.0
        rec = res.factor_scores["recurrence"] or 0.0
        return (resolved, unscored, -total, -sev, -rec, str(h.name))

    scored.sort(key=key)
    return [RankedHotspot(i + 1, h, r) for i, (h, r) in enumerate(scored)]


def with_weights(weights: Mapping[str, float], **changes: float) -> dict:
    """Convenience for tests and sliders: copy weights with changes applied."""
    new = dict(weights)
    new.update(changes)
    return new


__all__ = [
    "FACTORS",
    "FACTOR_LABELS",
    "DEFAULT_WEIGHTS",
    "RAIN_FULL_MM",
    "RECURRENCE_CAP",
    "HIGH_THRESHOLD",
    "MEDIUM_THRESHOLD",
    "BAND_HIGH",
    "BAND_MEDIUM",
    "BAND_MONITOR",
    "BAND_UNSCORED",
    "ScoreResult",
    "RankedHotspot",
    "severity_score",
    "recurrence_score",
    "rainfall_score",
    "access_score",
    "band_for",
    "normalise_weights",
    "score_inputs",
    "rank_hotspots",
    "with_weights",
]
