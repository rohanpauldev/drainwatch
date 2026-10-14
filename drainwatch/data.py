"""Hotspot records: loading, validating and saving the CSV dataset.

One row = one reported waterlogging location. The `source` column is
mandatory so demo rows can never be mistaken for real field reports.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional

COLUMNS = (
    "id",
    "name",
    "ward",
    "lat",
    "lon",
    "severity_1to5",
    "complaints_12m",
    "critical_access",
    "access_note",
    "status",
    "source",
    "note",
    "photo_url",
)
REQUIRED_COLUMNS = ("id", "name", "lat", "lon", "source")

STATUSES = ("pending", "inspected", "resolved")
SOURCES = ("demo", "field", "citizen")


class DataError(ValueError):
    """The dataset cannot be used at all (for example, a missing column)."""


@dataclass(frozen=True)
class Hotspot:
    id: str
    name: str
    lat: float
    lon: float
    source: str
    ward: str = ""
    severity: Optional[int] = None
    complaints_12m: Optional[int] = None
    critical_access: Optional[str] = None
    access_note: str = ""
    status: str = "pending"
    note: str = ""  # free-text description from the reporter
    photo_url: str = ""  # optional http(s) link to a photo

    @property
    def is_demo(self) -> bool:
        return self.source == "demo"

    def with_status(self, status: str) -> "Hotspot":
        if status not in STATUSES:
            raise ValueError(f"Status must be one of {STATUSES}")
        return replace(self, status=status)


@dataclass(frozen=True)
class LoadResult:
    hotspots: tuple
    warnings: tuple

    @property
    def all_demo(self) -> bool:
        return bool(self.hotspots) and all(h.is_demo for h in self.hotspots)

    @property
    def any_demo(self) -> bool:
        return any(h.is_demo for h in self.hotspots)


# ------------------------------------------------------------------- parsing

def is_safe_url(url: str) -> bool:
    """Only plain web links are kept (blocks javascript:, data: and similar)."""
    lowered = url.strip().lower()
    return lowered.startswith(("http://", "https://")) and " " not in lowered


def _clean(value) -> str:
    return "" if value is None else str(value).strip()


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(text: str) -> Optional[int]:
    if text == "":
        return None
    number = _to_float(text)
    if number is None or number != int(number):
        return None
    return int(number)


def parse_rows(rows: Iterable[dict], fieldnames: Iterable[str]) -> LoadResult:
    """Validate CSV rows. Bad optional values become None plus a warning."""
    names = {n.strip() for n in fieldnames if n}
    missing = [c for c in REQUIRED_COLUMNS if c not in names]
    if missing:
        raise DataError("Dataset is missing required column(s): " + ", ".join(missing))

    hotspots = []
    warnings = []
    seen_ids = set()

    for line_no, raw in enumerate(rows, start=2):  # header is line 1
        row = {(k or "").strip(): _clean(v) for k, v in raw.items()}
        if not any(row.values()):
            continue  # blank line
        label = f"Row {line_no}"

        hid = row.get("id", "")
        name = row.get("name", "")
        if not hid or not name:
            warnings.append(f"{label}: skipped, id and name are required.")
            continue
        if hid in seen_ids:
            warnings.append(f"{label}: skipped, duplicate id '{hid}'.")
            continue

        lat, lon = _to_float(row.get("lat", "")), _to_float(row.get("lon", ""))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            warnings.append(f"{label} ({hid}): skipped, latitude/longitude invalid.")
            continue

        severity = _to_int(row.get("severity_1to5", ""))
        if row.get("severity_1to5", "") and (severity is None or not 1 <= severity <= 5):
            warnings.append(f"{label} ({hid}): severity must be a whole number 1 to 5; treated as missing.")
            severity = None

        complaints = _to_int(row.get("complaints_12m", ""))
        if row.get("complaints_12m", "") and (complaints is None or complaints < 0):
            warnings.append(f"{label} ({hid}): complaints must be a whole number 0 or more; treated as missing.")
            complaints = None

        status = row.get("status", "").lower() or "pending"
        if status not in STATUSES:
            warnings.append(f"{label} ({hid}): unknown status '{status}'; set to pending.")
            status = "pending"

        source = row.get("source", "").lower()
        if not source:
            warnings.append(f"{label} ({hid}): no source given; marked 'unlabelled'.")
            source = "unlabelled"
        elif source not in SOURCES:
            warnings.append(f"{label} ({hid}): unusual source '{source}' (expected {', '.join(SOURCES)}).")

        photo_url = row.get("photo_url", "")
        if photo_url and not is_safe_url(photo_url):
            warnings.append(f"{label} ({hid}): photo link must start with http:// or https://; ignored.")
            photo_url = ""

        access = row.get("critical_access", "").lower().replace(" ", "_") or None

        seen_ids.add(hid)
        hotspots.append(
            Hotspot(
                id=hid,
                name=name,
                lat=lat,
                lon=lon,
                source=source,
                ward=row.get("ward", ""),
                severity=severity,
                complaints_12m=complaints,
                critical_access=access,
                access_note=row.get("access_note", ""),
                status=status,
                note=row.get("note", ""),
                photo_url=photo_url,
            )
        )

    return LoadResult(tuple(hotspots), tuple(warnings))


def load_hotspots(path) -> LoadResult:
    """Load hotspots from a CSV file path."""
    path = Path(path)
    if not path.exists():
        raise DataError(f"Dataset not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return parse_rows(reader, reader.fieldnames or [])


def load_hotspots_from_text(text: str) -> LoadResult:
    """Load hotspots from CSV text (for uploaded files)."""
    reader = csv.DictReader(io.StringIO(text))
    return parse_rows(reader, reader.fieldnames or [])


# ------------------------------------------------------------------- writing

def _row_for(h: Hotspot) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "ward": h.ward,
        "lat": h.lat,
        "lon": h.lon,
        "severity_1to5": "" if h.severity is None else h.severity,
        "complaints_12m": "" if h.complaints_12m is None else h.complaints_12m,
        "critical_access": h.critical_access or "",
        "access_note": h.access_note,
        "status": h.status,
        "source": h.source,
        "note": h.note,
        "photo_url": h.photo_url,
    }


def hotspots_to_csv_text(hotspots: Iterable[Hotspot]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for h in hotspots:
        writer.writerow(_row_for(h))
    return buffer.getvalue()


def save_hotspots(path, hotspots: Iterable[Hotspot]) -> None:
    Path(path).write_text(hotspots_to_csv_text(hotspots), encoding="utf-8")


# ------------------------------------------------------------------- helpers

def next_hotspot_id(existing: Iterable[Hotspot], prefix: str = "H") -> str:
    """Return an unused id like H11 given existing H1..H10."""
    used = {h.id for h in existing}
    n = len(used) + 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def centroid(hotspots: Iterable[Hotspot]) -> Optional[tuple]:
    """Mean latitude/longitude of the hotspots, or None if there are none."""
    points = [(h.lat, h.lon) for h in hotspots]
    if not points:
        return None
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )
