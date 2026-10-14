"""Stakeholder workflow: citizen reports, inspection status, affected assets.

All functions are pure (they return new hotspot tuples) so they are easy to
test and the Streamlit layer only has to hold the current tuple in session
state. Nothing here is persisted: on a hosted prototype the session state is
the record, and the dashboard offers a CSV download instead of pretending to
keep a database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable, Optional

from .data import STATUSES, Hotspot, centroid, is_safe_url, next_hotspot_id

MAX_NAME = 80
MAX_NOTE = 300
# A new report this far from every existing hotspot is probably a typo
# (or a different town): warn, but still allow it.
FAR_WARNING_KM = 25.0
# Within this distance the form suggests adding to an existing hotspot.
NEARBY_METRES = 75.0

ACCESS_CHOICES = (
    ("", "Not sure / not surveyed"),
    ("none", "None nearby"),
    ("school", "School"),
    ("clinic", "Clinic"),
    ("hospital", "Hospital"),
    ("market", "Market"),
    ("main_road", "Main road"),
)


# ------------------------------------------------------------------- geometry

def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (haversine)."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_hotspot(hotspots: Iterable[Hotspot], lat: float, lon: float):
    """(hotspot, metres) of the closest existing hotspot, or None."""
    best = None
    for h in hotspots:
        d = distance_km(lat, lon, h.lat, h.lon) * 1000
        if best is None or d < best[1]:
            best = (h, d)
    return best


# ------------------------------------------------------------ affected assets

@dataclass(frozen=True)
class Asset:
    kind: str
    label: str
    detail: str


_ACCESS_ASSETS = {
    "school": Asset("school", "School access", "Children's route to school may be cut off."),
    "clinic": Asset("clinic", "Clinic access", "Patients and health workers may be unable to reach the clinic."),
    "hospital": Asset("hospital", "Emergency access", "Ambulance and patient access to the hospital may be blocked."),
    "market": Asset("market", "Market and livelihoods", "Trading road: traders and shoppers may be cut off."),
    "main_road": Asset("route", "Mobility route", "Main road traffic and pedestrians may be disrupted."),
}

HEALTH_SEVERITY = 4  # reported severity at or above this flags a public-health concern


def affected_assets(h: Hotspot) -> list:
    """What a blocked drain at this site may affect (systems view).

    These are flags from the report data, not predictions: the access category
    comes from the record, and the public-health flag appears when the reported
    severity is 4 or 5 (standing water is likelier to persist).
    """
    assets = []
    key = (h.critical_access or "").strip().lower()
    if key in _ACCESS_ASSETS:
        assets.append(_ACCESS_ASSETS[key])
    elif key and key != "none":
        name = key.replace("_", " ")
        assets.append(Asset("other", f"Other facility: {name}", f"Access to the {name} may be affected."))
    if h.severity is not None and h.severity >= HEALTH_SEVERITY:
        assets.append(
            Asset(
                "health",
                "Public health",
                "Severe reported waterlogging: standing water may raise mosquito and waterborne-disease risk.",
            )
        )
    return assets


def assets_line(h: Hotspot) -> str:
    """One line for the queue and detail view."""
    if not (h.critical_access or "").strip():
        base = "Access not yet surveyed"
        assets = affected_assets(h)
        return base + (" · " + " · ".join(a.label for a in assets) if assets else "")
    assets = affected_assets(h)
    if not assets:
        return "No critical facility recorded"
    return " · ".join(a.label for a in assets)


# ------------------------------------------------------------ citizen reports

def validate_report(
    name: str,
    lat: Optional[float],
    lon: Optional[float],
    severity: Optional[int],
    note: str = "",
    photo_url: str = "",
) -> list:
    """Return a list of problems (empty if the report is acceptable)."""
    errors = []
    name = (name or "").strip()
    if len(name) < 3:
        errors.append("Describe the location in a few words (at least 3 characters).")
    if len(name) > MAX_NAME:
        errors.append(f"Location description is too long (maximum {MAX_NAME} characters).")
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        errors.append("Latitude must be between -90 and 90 and longitude between -180 and 180.")
    if severity is None or not 1 <= severity <= 5:
        errors.append("Severity must be a rating from 1 to 5.")
    if len(note or "") > MAX_NOTE:
        errors.append(f"Note is too long (maximum {MAX_NOTE} characters).")
    if photo_url and not is_safe_url(photo_url):
        errors.append("Photo link must be a web address starting with http:// or https://.")
    return errors


def far_from_ward_warning(hotspots: Iterable[Hotspot], lat: float, lon: float) -> Optional[str]:
    centre = centroid(list(hotspots))
    if centre is None:
        return None
    km = distance_km(lat, lon, centre[0], centre[1])
    if km > FAR_WARNING_KM:
        return f"This point is {km:.0f} km from the other hotspots. Check the coordinates before submitting."
    return None


@dataclass(frozen=True)
class ReportOutcome:
    hotspots: tuple
    hotspot_id: str
    kind: str  # "new" or "repeat"


def submit_report(
    hotspots: Iterable[Hotspot],
    *,
    name: str,
    lat: float,
    lon: float,
    severity: int,
    note: str = "",
    photo_url: str = "",
    critical_access: str = "",
    repeat_of: Optional[str] = None,
) -> ReportOutcome:
    """Add a citizen report.

    New location: a pending hotspot with source "citizen" and one complaint.
    Repeat complaint (repeat_of given): the existing hotspot gains one
    complaint, its severity becomes the higher of the two ratings, and a
    resolved or inspected site is reopened as pending because residents are
    reporting it again.
    """
    current = tuple(hotspots)
    problems = validate_report(name, lat, lon, severity, note, photo_url)
    if problems:
        raise ValueError(" ".join(problems))

    if repeat_of is not None:
        target = next((h for h in current if h.id == repeat_of), None)
        if target is None:
            raise ValueError(f"No hotspot with id {repeat_of}.")
        updated = replace(
            target,
            complaints_12m=(target.complaints_12m or 0) + 1,
            severity=max(target.severity or 0, severity),
            status="pending",
            note=(note.strip() or target.note),
            photo_url=photo_url.strip() or target.photo_url,
        )
        return ReportOutcome(tuple(updated if h.id == repeat_of else h for h in current), repeat_of, "repeat")

    ward = next((h.ward for h in current if h.ward), "")
    new = Hotspot(
        id=next_hotspot_id(current),
        name=name.strip(),
        lat=float(lat),
        lon=float(lon),
        source="citizen",
        ward=ward,
        severity=int(severity),
        complaints_12m=1,
        critical_access=(critical_access or "").strip().lower() or None,
        access_note="",
        status="pending",
        note=(note or "").strip(),
        photo_url=(photo_url or "").strip(),
    )
    return ReportOutcome(current + (new,), new.id, "new")


# ------------------------------------------------------------------- status

@dataclass(frozen=True)
class StatusEvent:
    time: datetime
    hotspot_id: str
    hotspot_name: str
    old: str
    new: str


def set_status(hotspots: Iterable[Hotspot], hotspot_id: str, status: str, now: Optional[datetime] = None):
    """Return (new hotspots tuple, StatusEvent or None if nothing changed)."""
    if status not in STATUSES:
        raise ValueError(f"Status must be one of {STATUSES}")
    current = tuple(hotspots)
    target = next((h for h in current if h.id == hotspot_id), None)
    if target is None:
        raise ValueError(f"No hotspot with id {hotspot_id}.")
    if target.status == status:
        return current, None
    event = StatusEvent(now or datetime.now(timezone.utc), target.id, target.name, target.status, status)
    return tuple(h.with_status(status) if h.id == hotspot_id else h for h in current), event


# ----------------------------------------------------------------- tracking

def track_report(ranked: list, hotspot_id: str) -> Optional[dict]:
    """What a citizen sees for their report: rank, band and status."""
    key = (hotspot_id or "").strip().lower()
    for item in ranked:
        if item.hotspot.id.lower() == key:
            open_count = sum(1 for r in ranked if r.hotspot.status != "resolved")
            return {
                "id": item.hotspot.id,
                "name": item.hotspot.name,
                "rank": item.rank,
                "total": len(ranked),
                "open": open_count,
                "band": item.result.band,
                "score": item.result.total,
                "status": item.hotspot.status,
                "complaints": item.hotspot.complaints_12m,
            }
    return None


def status_message(info: dict) -> str:
    """Plain-language status for the resident."""
    where = f"rank {info['rank']} of {info['total']} sites"
    if info["status"] == "resolved":
        return f"Report {info['id']} ({info['name']}) has been marked resolved."
    if info["status"] == "inspected":
        return f"Report {info['id']} ({info['name']}) has been inspected by ward staff; currently {where}."
    return (
        f"Report {info['id']} ({info['name']}) is waiting for inspection; "
        f"currently {where}, {info['band']} priority."
    )


__all__ = [
    "ACCESS_CHOICES",
    "Asset",
    "ReportOutcome",
    "StatusEvent",
    "affected_assets",
    "assets_line",
    "distance_km",
    "far_from_ward_warning",
    "nearest_hotspot",
    "set_status",
    "status_message",
    "submit_report",
    "track_report",
    "validate_report",
    "NEARBY_METRES",
]
