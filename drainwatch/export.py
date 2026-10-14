"""CSV export of the ranked queue."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from .scoring import FACTORS
from .workflow import assets_line

_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """Stop spreadsheet formula injection: reporter text may start with = + - @."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_CHARS) else text


def ranked_to_csv(ranked: list, rain_24h_mm: Optional[float], now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    cols = (
        ["rank", "id", "name", "ward", "lat", "lon", "score", "band", "partial", "status", "source",
         "severity_1to5", "complaints_12m", "critical_access", "affects"]
        + [f"{f}_score" for f in FACTORS]
        + [f"{f}_points" for f in FACTORS]
        + ["rain_24h_mm", "generated"]
    )
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in ranked:
        h, res = r.hotspot, r.result
        def num(v):
            return "" if v is None else round(v, 1)
        w.writerow(
            [r.rank, safe_cell(h.id), safe_cell(h.name), safe_cell(h.ward), h.lat, h.lon,
             num(res.total), res.band, "yes" if res.partial else "no", h.status, safe_cell(h.source),
             "" if h.severity is None else h.severity,
             "" if h.complaints_12m is None else h.complaints_12m,
             safe_cell(h.critical_access), safe_cell(assets_line(h))]
            + [num(res.factor_scores[f]) for f in FACTORS]
            + [num(res.contributions[f]) for f in FACTORS]
            + ["" if rain_24h_mm is None else rain_24h_mm, stamp]
        )
    return buf.getvalue()


__all__ = ["ranked_to_csv", "safe_cell"]
