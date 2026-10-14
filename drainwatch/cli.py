"""Command-line check of the engine: python -m drainwatch.cli --place Dhaka

Prints the live rainfall summary and the ranked hotspot queue with each
score's breakdown. Useful for verifying the live API call before using the
dashboard, and as a quick demo of the scoring logic.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .data import centroid, load_hotspots
from .scoring import FACTOR_LABELS, FACTORS, rank_hotspots
from .weather import WeatherError, geocode, get_rain_summary

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "hotspots.csv"


def _fmt(value, spec="{:.0f}"):
    return "missing" if value is None else spec.format(value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DrainWatch BD engine check")
    parser.add_argument("--place", help="Place name to search for the forecast location")
    parser.add_argument("--country", default="BD", help="Country code filter for place search (default BD)")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="Hotspot CSV path")
    args = parser.parse_args(argv)

    loaded = load_hotspots(args.data)
    for warning in loaded.warnings:
        print("WARNING:", warning)
    if not loaded.hotspots:
        print("No hotspots to rank.")
        return 1
    if loaded.any_demo:
        print("NOTE: dataset contains DEMO records, not real field reports.\n")

    lat, lon = centroid(loaded.hotspots)
    where = "hotspot centroid"
    if args.place:
        try:
            places = geocode(args.place, country_code=args.country)
        except WeatherError as exc:
            print("Place search failed:", exc)
            places = []
        if places:
            lat, lon, where = places[0].latitude, places[0].longitude, places[0].label
        else:
            print(f"No place found for '{args.place}'; using the hotspot centroid.")

    rain_mm = None
    try:
        summary = get_rain_summary(lat, lon)
        rain_mm = summary.total_24h_mm
        print(f"Forecast for {where} ({lat:.4f}, {lon:.4f})")
        print(f"  next 24h rainfall : {_fmt(summary.total_24h_mm, '{:.1f} mm')}")
        print(f"  next 48h rainfall : {_fmt(summary.total_48h_mm, '{:.1f} mm')}")
        print(f"  peak hourly       : {_fmt(summary.peak_hourly_mm, '{:.1f} mm')} at {summary.peak_hour or 'n/a'}")
        print(f"  max probability   : {_fmt(summary.max_probability_pct, '{:.0f}%')}")
        print(f"  fetched (UTC)     : {summary.fetched_at:%Y-%m-%d %H:%M}")
        if not summary.complete:
            print("  (fewer than 24 hours of data available)")
    except WeatherError as exc:
        print("Live rainfall unavailable:", exc)
        print("Ranking continues WITHOUT the rainfall factor (scores marked partial).")
    print()

    ranked = rank_hotspots(loaded.hotspots, rain_mm)
    header = f"{'#':>2}  {'Score':>6}  {'Band':<8} {'Status':<9} Name"
    print(header)
    print("-" * len(header))
    for item in ranked:
        h, res = item.hotspot, item.result
        score = "n/a" if res.total is None else f"{res.total:.1f}"
        flag = " *partial" if res.partial else ""
        print(f"{item.rank:>2}  {score:>6}  {res.band:<8} {h.status:<9} {h.name}{flag}")
        parts = ", ".join(
            f"{FACTOR_LABELS[f].lower()} {_fmt(res.factor_scores[f])}" for f in FACTORS
        )
        print(f"{'':>4}{parts}")
    print("\nThis ranks reported locations for inspection. It does not predict flooding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
