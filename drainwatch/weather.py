"""Live rainfall and place search via the Open-Meteo APIs (no API key needed).

Two calls are used:
  * Geocoding API  - place name -> coordinates
  * Forecast API   - hourly precipitation (mm) and precipitation probability

Everything returns plain dataclasses so the dashboard and tests stay simple.
Failures raise WeatherError with a message that is safe to show to a user; the
app can then fall back to a clearly labelled cached/offline mode instead of
silently inventing numbers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

USER_AGENT = "DrainWatchBD/0.1 (student prototype for BIP Apps4Solutions 2026)"


class WeatherError(RuntimeError):
    """A failure talking to Open-Meteo. The message is safe to show to users."""


# ---------------------------------------------------------------- data types

@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    admin1: str = ""
    country: str = ""
    country_code: str = ""

    @property
    def label(self) -> str:
        parts = [p for p in (self.name, self.admin1, self.country) if p]
        return ", ".join(parts)


@dataclass(frozen=True)
class HourlyPoint:
    time: str  # local time, "YYYY-MM-DDTHH:MM"
    precipitation_mm: Optional[float]
    probability_pct: Optional[float]


@dataclass(frozen=True)
class Forecast:
    latitude: float
    longitude: float
    timezone: str
    utc_offset_seconds: int
    hours: tuple
    fetched_at: datetime  # UTC
    source_url: str

    def local_now(self, now_utc: Optional[datetime] = None) -> datetime:
        now_utc = now_utc or datetime.now(timezone.utc)
        return (now_utc + timedelta(seconds=self.utc_offset_seconds)).replace(tzinfo=None)


@dataclass(frozen=True)
class RainSummary:
    """Rainfall figures for the window starting at the current local hour."""

    total_24h_mm: Optional[float]
    total_48h_mm: Optional[float]
    peak_hourly_mm: Optional[float]
    peak_hour: Optional[str]
    max_probability_pct: Optional[float]
    hours_available: int
    fetched_at: datetime
    source_url: str
    window: tuple  # the HourlyPoints in the next-24-hour window

    @property
    def complete(self) -> bool:
        """True when a full 24 hours of precipitation values were available."""
        return self.hours_available >= 24


# ------------------------------------------------------------------ requests

def _get_json(url: str, params: dict, session=None, timeout: float = 10.0, retries: int = 2):
    """GET with a short timeout and a small retry on transient failures."""
    http = session or requests
    last_error = "unknown error"
    for attempt in range(retries + 1):
        try:
            resp = http.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as exc:  # network down, DNS, timeout
            last_error = f"network problem ({type(exc).__name__})"
        else:
            if resp.status_code == 200:
                try:
                    return resp.json(), resp.url
                except ValueError:
                    last_error = "the service returned an unreadable response"
                    break
            if resp.status_code == 429:
                last_error = "the weather service is rate limiting requests, try again shortly"
            elif 500 <= resp.status_code < 600:
                last_error = f"the weather service had a server error ({resp.status_code})"
            else:
                last_error = f"the weather service rejected the request ({resp.status_code})"
                break  # client errors will not improve on retry
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))
    raise WeatherError(f"Could not reach Open-Meteo: {last_error}.")


# ----------------------------------------------------------------- geocoding

def geocode(
    query: str,
    count: int = 5,
    country_code: Optional[str] = None,
    session=None,
) -> list:
    """Search for a place by name. Returns a list of Place (possibly empty)."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    data, _ = _get_json(
        GEOCODING_URL,
        {"name": query, "count": max(1, min(count, 20)), "language": "en", "format": "json"},
        session=session,
    )
    places = []
    for item in data.get("results") or []:
        try:
            place = Place(
                name=str(item["name"]),
                latitude=float(item["latitude"]),
                longitude=float(item["longitude"]),
                admin1=str(item.get("admin1") or ""),
                country=str(item.get("country") or ""),
                country_code=str(item.get("country_code") or ""),
            )
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed entries rather than failing the search
        if country_code and place.country_code.upper() != country_code.upper():
            continue
        places.append(place)
    return places


# ------------------------------------------------------------------ forecast

def fetch_forecast(
    latitude: float,
    longitude: float,
    session=None,
    now_utc: Optional[datetime] = None,
) -> Forecast:
    """Fetch the hourly precipitation forecast for a point (next ~3 days)."""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise WeatherError("Coordinates are outside the valid range.")
    data, url = _get_json(
        FORECAST_URL,
        {
            "latitude": round(latitude, 4),
            "longitude": round(longitude, 4),
            "hourly": "precipitation,precipitation_probability",
            "forecast_days": 3,
            "timezone": "auto",
        },
        session=session,
    )
    return parse_forecast(data, url, now_utc)


def parse_forecast(data: dict, source_url: str = "", now_utc: Optional[datetime] = None) -> Forecast:
    """Turn an Open-Meteo forecast payload into a Forecast (separate for tests)."""
    try:
        hourly = data["hourly"]
        times = hourly["time"]
        precip = hourly["precipitation"]
        prob = hourly.get("precipitation_probability") or [None] * len(times)
        offset = int(data.get("utc_offset_seconds", 0))
        tz_name = str(data.get("timezone", "UTC"))
        lat = float(data["latitude"])
        lon = float(data["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("Open-Meteo returned data in an unexpected format.") from exc
    if not (len(times) == len(precip) == len(prob)):
        raise WeatherError("Open-Meteo returned hourly series of different lengths.")

    points = tuple(
        HourlyPoint(
            time=str(t),
            precipitation_mm=None if p is None else float(p),
            probability_pct=None if q is None else float(q),
        )
        for t, p, q in zip(times, precip, prob)
    )
    return Forecast(
        latitude=lat,
        longitude=lon,
        timezone=tz_name,
        utc_offset_seconds=offset,
        hours=points,
        fetched_at=now_utc or datetime.now(timezone.utc),
        source_url=source_url,
    )


def summarise(forecast: Forecast, now_utc: Optional[datetime] = None) -> RainSummary:
    """Summarise rainfall from the current local hour onward (24h and 48h)."""
    local_now = forecast.local_now(now_utc).replace(minute=0, second=0, microsecond=0)
    start = None
    for i, point in enumerate(forecast.hours):
        if datetime.fromisoformat(point.time) >= local_now:
            start = i
            break
    if start is None:
        window48: tuple = ()
    else:
        window48 = forecast.hours[start : start + 48]
    window24 = window48[:24]

    def total(points) -> Optional[float]:
        values = [p.precipitation_mm for p in points if p.precipitation_mm is not None]
        return round(sum(values), 1) if values else None

    rain_points = [p for p in window24 if p.precipitation_mm is not None]
    peak = max(rain_points, key=lambda p: p.precipitation_mm) if rain_points else None
    probs = [p.probability_pct for p in window24 if p.probability_pct is not None]

    return RainSummary(
        total_24h_mm=total(window24),
        total_48h_mm=total(window48),
        peak_hourly_mm=peak.precipitation_mm if peak else None,
        peak_hour=peak.time if peak else None,
        max_probability_pct=max(probs) if probs else None,
        hours_available=len(rain_points),
        fetched_at=forecast.fetched_at,
        source_url=forecast.source_url,
        window=tuple(window24),
    )


def get_rain_summary(latitude: float, longitude: float, session=None) -> RainSummary:
    """Convenience: live fetch + summary in one call."""
    now = datetime.now(timezone.utc)
    forecast = fetch_forecast(latitude, longitude, session=session, now_utc=now)
    return summarise(forecast, now)
