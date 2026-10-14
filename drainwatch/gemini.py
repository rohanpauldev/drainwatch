"""Optional plain-language brief from the Gemini API (Google AI Studio key).

Only site names, scores and bands are sent; citizen notes, photo links and
coordinates are not. The key is read from Streamlit secrets by the app, never
stored in the repository.
"""

from __future__ import annotations

from typing import Optional

import requests

DEFAULT_MODEL = "gemini-2.5-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiError(RuntimeError):
    """The brief could not be generated. The message is safe to show."""


def build_prompt(ranked: list, rain_24h_mm: Optional[float], headline: str, top_n: int = 5) -> str:
    rows = []
    for r in [x for x in ranked if x.hotspot.status != "resolved"][:top_n]:
        score = "not scored" if r.result.total is None else f"{r.result.total:.1f}"
        rows.append(f"{r.rank}. {r.hotspot.name} | score {score} | {r.result.band} | {r.result.explain()}")
    rain = "unavailable" if rain_24h_mm is None else f"{rain_24h_mm:.1f} mm"
    return (
        "You are helping a ward drainage inspection officer in Bangladesh. Write a brief of at most "
        "110 words in plain English: what to inspect first and why, using only the facts below. "
        "Do not claim to predict flooding, do not invent numbers, and do not add facts.\n\n"
        f"Forecast rainfall, next 24 hours: {rain}\nPattern: {headline}\nTop open sites:\n" + "\n".join(rows)
    )


def generate_brief(prompt: str, api_key: str, model: str = DEFAULT_MODEL, session=None, timeout: float = 25.0) -> str:
    if not api_key:
        raise GeminiError("No Gemini key configured.")
    http = session or requests
    try:
        resp = http.post(
            ENDPOINT.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise GeminiError(f"Could not reach the Gemini service ({type(exc).__name__}).") from exc
    if resp.status_code != 200:
        raise GeminiError(f"The Gemini service returned an error ({resp.status_code}).")
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeminiError("The Gemini service returned an empty or unexpected response.") from exc
    if not text:
        raise GeminiError("The Gemini service returned an empty response.")
    return text


__all__ = ["GeminiError", "build_prompt", "generate_brief", "DEFAULT_MODEL"]
