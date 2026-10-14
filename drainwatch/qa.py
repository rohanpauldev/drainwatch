"""Pre-submission QA: python -m drainwatch.qa

Runs the checks that matter before filming, on a machine with internet:
data file is clean, place search and the live forecast work for the demo place
and for places that were NOT prepared in advance, and ranking still works for
each. Exit code 0 means every check passed.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .data import centroid, load_hotspots
from .scoring import rank_hotspots
from .weather import WeatherError, geocode, get_rain_summary

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data" / "hotspots.csv"
PLACES = ("Dhaka", "Rajshahi", "Cox's Bazar", "Sylhet")


def run_checks(data_path=DEFAULT_DATA, places=PLACES, geocode_fn=geocode, rain_fn=get_rain_summary) -> list:
    """Return a list of (name, passed, detail)."""
    results = []

    def record(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    loaded = load_hotspots(data_path)
    record("hotspot CSV loads without warnings", not loaded.warnings and loaded.hotspots,
           f"{len(loaded.hotspots)} rows" + (f", warnings: {loaded.warnings}" if loaded.warnings else ""))
    record("every row has a source label", all(h.source for h in loaded.hotspots))
    record("demo rows are labelled demo (or data is real)", all(h.source in ("demo", "field", "citizen") for h in loaded.hotspots))

    for place in places:
        try:
            found = geocode_fn(place, country_code="BD")
            record(f"place search: {place}", bool(found), found[0].label if found else "no match")
            if not found:
                continue
            summary = rain_fn(found[0].latitude, found[0].longitude)
            record(f"live forecast: {place}", summary.total_24h_mm is not None,
                   f"24h {summary.total_24h_mm} mm, {summary.hours_available} hours of data")
            ranked = rank_hotspots(loaded.hotspots, summary.total_24h_mm)
            top = ranked[0].result.total
            record(f"ranking with {place} rainfall", top is not None, f"top score {top}")
        except WeatherError as exc:
            record(f"live call: {place}", False, str(exc))

    c = centroid(loaded.hotspots)
    record("hotspot cluster is inside Bangladesh", c is not None and 20.5 <= c[0] <= 26.7 and 88.0 <= c[1] <= 92.8,
           f"centroid {c}")
    return results


def main() -> int:
    results = run_checks()
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print("If the failures are live-call ones, check your internet connection and retry; the app falls back to a cached label.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
