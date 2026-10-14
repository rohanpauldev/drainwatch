"""Plotly map of ranked hotspots on OpenStreetMap tiles (no API token needed)."""

from __future__ import annotations

import math
from html import escape
from typing import Optional

import plotly.graph_objects as go

from .dashboard import BAND_COLORS
from .scoring import BAND_UNSCORED
from .workflow import assets_line

RESOLVED_COLOR = "#B0B7BD"
HIGHLIGHT_COLOR = "#1F6F8B"


def _zoom_for(lats, lons) -> float:
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.9, 0.004)
    return max(4.0, min(16.0, math.log2(360 / span) - 1.2))


def build_map(ranked: list, selected_id: Optional[str] = None, height: int = 430) -> go.Figure:
    """One marker per hotspot, coloured by priority band, labelled with its rank.

    Resolved sites are grey. The selected site gets a halo drawn beneath it.
    Each marker carries its hotspot id in customdata so a click can select it.
    """
    fig = go.Figure()
    if not ranked:
        fig.update_layout(height=height, margin=dict(l=0, r=0, t=0, b=0))
        return fig

    lats = [r.hotspot.lat for r in ranked]
    lons = [r.hotspot.lon for r in ranked]

    chosen = next((r for r in ranked if r.hotspot.id == selected_id), None)
    if chosen is not None:
        fig.add_trace(
            go.Scattermap(
                lat=[chosen.hotspot.lat],
                lon=[chosen.hotspot.lon],
                mode="markers",
                marker=dict(size=40, color=HIGHLIGHT_COLOR, opacity=0.3),
                hoverinfo="skip",
                showlegend=False,
            )
        )

    colors, sizes, texts, hovers, ids = [], [], [], [], []
    for r in ranked:
        h, res = r.hotspot, r.result
        resolved = h.status == "resolved"
        colors.append(RESOLVED_COLOR if resolved else BAND_COLORS.get(res.band, BAND_COLORS[BAND_UNSCORED]))
        sizes.append(14 if res.total is None else 14 + res.total / 100 * 14)
        texts.append(str(r.rank))
        score = "not scored" if res.total is None else f"{res.total:.1f} ({res.band})"
        hovers.append(
            f"<b>#{r.rank} {escape(h.name)}</b><br>Score: {score}<br>Status: {escape(h.status)}"
            f"<br>Affects: {escape(assets_line(h))}"
        )
        ids.append(h.id)

    fig.add_trace(
        go.Scattermap(
            lat=lats,
            lon=lons,
            mode="markers+text",
            marker=dict(size=sizes, color=colors, opacity=0.95),
            text=texts,
            textposition="top right",
            textfont=dict(size=13, color="#1b1f23"),
            customdata=ids,
            hovertext=hovers,
            hoverinfo="text",
            showlegend=False,
            name="Hotspots",
        )
    )
    fig.update_layout(
        map=dict(
            style="open-street-map",
            center=dict(lat=sum(lats) / len(lats), lon=sum(lons) / len(lons)),
            zoom=_zoom_for(lats, lons),
        ),
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        clickmode="event+select",
    )
    return fig


def selected_id_from_event(event) -> Optional[str]:
    """Pull the clicked hotspot id out of a Streamlit plotly selection event."""
    try:
        points = event["selection"]["points"]
    except (KeyError, TypeError):
        return None
    for p in points or []:
        cd = p.get("customdata")
        if isinstance(cd, (list, tuple)):
            cd = cd[0] if cd else None
        if cd:
            return str(cd)
    return None


__all__ = ["build_map", "selected_id_from_event"]
