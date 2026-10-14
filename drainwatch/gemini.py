"""Optional plain-language brief from the Gemini API (Google AI Studio key).

Only site names, scores and bands are sent; citizen notes, photo links and
coordinates are not. The key is read from Streamlit secrets by the app, never
stored in the repository.
"""

from __future__ import annotations

from typing import Optional

import requests

DEFAULT_MODEL = "gemini-3.5-flash-lite"
# Tried in order after the configured model when the API answers 404
# (model retired or not available to this key).
FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash")
BASE = "https://generativelanguage.googleapis.com/v1beta"
ENDPOINT = BASE + "/models/{model}:generateContent"


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


def _api_message(resp) -> str:
    try:
        return str(resp.json()["error"]["message"])[:160]
    except Exception:
        return ""


def _discover_model(http, headers, timeout):
    """Ask the API which models this key can use; pick a current Flash model."""
    try:
        resp = http.get(BASE + "/models", headers=headers, params={"pageSize": 100}, timeout=timeout)
        if resp.status_code != 200:
            return None
        names = []
        for m in resp.json().get("models", []):
            name = str(m.get("name", "")).removeprefix("models/")
            if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in name and not any(
                bad in name for bad in ("image", "tts", "live", "audio", "preview", "exp", "thinking")
            ):
                names.append(name)
        names.sort(key=lambda n: ("lite" not in n, n))
        return names[0] if names else None
    except Exception:
        return None


def generate_brief(prompt: str, api_key: str, model: str = DEFAULT_MODEL, session=None, timeout: float = 25.0) -> str:
    """Generate the brief. On 404 (unknown/retired model) fall back to other models."""
    if not api_key:
        raise GeminiError("No Gemini key configured.")
    http = session or requests
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    body = {"contents": [{"parts": [{"text": prompt}]}]}

    candidates = [m for m in dict.fromkeys([model or DEFAULT_MODEL, DEFAULT_MODEL, *FALLBACK_MODELS])]
    tried, last_note, discovered = [], "", False
    while candidates:
        name = candidates.pop(0)
        tried.append(name)
        try:
            resp = http.post(ENDPOINT.format(model=name), headers=headers, json=body, timeout=timeout)
        except requests.RequestException as exc:
            raise GeminiError(f"Could not reach the Gemini service ({type(exc).__name__}).") from exc
        if resp.status_code == 200:
            try:
                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise GeminiError("The Gemini service returned an empty or unexpected response.") from exc
            if not text:
                raise GeminiError("The Gemini service returned an empty response.")
            return text
        if resp.status_code == 404:
            last_note = _api_message(resp)
            if not candidates and not discovered:
                discovered = True
                found = _discover_model(http, headers, timeout)
                if found and found not in tried:
                    candidates.append(found)
            continue
        note = _api_message(resp)
        raise GeminiError(f"The Gemini service returned an error ({resp.status_code})" + (f": {note}" if note else "") + ".")
    raise GeminiError(
        "No Gemini model was available for this key (tried " + ", ".join(tried) + ")"
        + (f": {last_note}" if last_note else "")
        + ". Set GEMINI_MODEL in secrets to a model your key can use."
    )


__all__ = ["GeminiError", "build_prompt", "generate_brief", "DEFAULT_MODEL", "FALLBACK_MODELS"]
