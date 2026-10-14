"""DrainWatch BD: ward-level decision support for drainage inspection.

Run locally:  streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from drainwatch.dashboard import (
    CSS,
    LIMITS_LINE,
    PRESET_PLACES,
    RainState,
    detail_rows,
    fmt_mm,
    legend_html,
    queue_html,
    resolve_rain,
    weight_shares,
    weights_from_percent,
)
from drainwatch.data import DataError, centroid, hotspots_to_csv_text, load_hotspots
from drainwatch.export import ranked_to_csv
from drainwatch.gemini import DEFAULT_MODEL, GeminiError, build_prompt, generate_brief
from drainwatch.mapview import build_map, selected_id_from_event
from drainwatch.policy import policy_insight
from drainwatch.scoring import (
    DEFAULT_WEIGHTS,
    FACTOR_LABELS,
    FACTORS,
    RAIN_FULL_MM,
    rank_hotspots,
)
from drainwatch.dashboard import stacked_bar_html
from drainwatch.weather import WeatherError, geocode, get_rain_summary
from drainwatch.workflow import (
    ACCESS_CHOICES,
    NEARBY_METRES,
    affected_assets,
    assets_line,
    far_from_ward_warning,
    nearest_hotspot,
    set_status,
    status_message,
    submit_report,
    track_report,
)

DATA_PATH = Path(__file__).parent / "data" / "hotspots.csv"
DEFAULT_PLACE = "Dhaka"

st.set_page_config(page_title="DrainWatch BD", page_icon="💧", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------- data

@st.cache_data(show_spinner=False)
def _load():
    return load_hotspots(DATA_PATH)


@st.cache_data(ttl=3600, show_spinner=False)
def _search(query: str, bd_only: bool):
    return geocode(query, count=5, country_code="BD" if bd_only else None)


@st.cache_data(ttl=600, show_spinner=False)
def _rain(lat: float, lon: float):
    return get_rain_summary(lat, lon)


# ------------------------------------------------------------------ sidebar

def sidebar():
    """Place search and weight sliders. Returns (place_label, lat, lon, percent weights, rain cap)."""
    st.sidebar.header("Forecast location")
    query = st.sidebar.text_input("Search a ward or town", value=DEFAULT_PLACE, help="Press Enter to search")
    bd_only = st.sidebar.checkbox("Bangladesh only", value=True)

    label, lat, lon = None, None, None
    try:
        places = _search(query.strip(), bd_only) if query.strip() else []
    except WeatherError as exc:
        places = []
        st.sidebar.warning(f"{exc} Pick a preset place instead.")
        preset = st.sidebar.selectbox("Preset place", list(PRESET_PLACES))
        lat, lon = PRESET_PLACES[preset]
        label = f"{preset} (preset)"
    else:
        if places:
            idx = st.sidebar.selectbox(
                "Matches", range(len(places)), format_func=lambda i: places[i].label
            ) if len(places) > 1 else 0
            chosen = places[idx]
            label, lat, lon = chosen.label, chosen.latitude, chosen.longitude
        elif query.strip():
            st.sidebar.info(f"No place found for '{query}'. Using the hotspot area instead.")

    st.sidebar.header("Scoring weights")
    st.sidebar.caption(
        "Adjustable design assumptions, not validated engineering values. "
        "Weights are re-scaled to total 100%."
    )
    if st.sidebar.button("Reset to defaults"):
        for f in FACTORS:
            st.session_state[f"w_{f}"] = int(DEFAULT_WEIGHTS[f] * 100)
    percent = {}
    for f in FACTORS:
        st.session_state.setdefault(f"w_{f}", int(DEFAULT_WEIGHTS[f] * 100))
        percent[f] = st.sidebar.slider(FACTOR_LABELS[f], 0, 100, step=5, key=f"w_{f}")
    shares = weight_shares(percent)
    if sum(percent.values()) == 0:
        st.sidebar.error("Set at least one weight above zero.")
    else:
        st.sidebar.caption(
            "Effective split: " + ", ".join(f"{FACTOR_LABELS[f].split()[0]} {shares[f]:.0f}%" for f in FACTORS)
        )

    with st.sidebar.expander("Rainfall assumption"):
        rain_cap = st.slider(
            "Forecast 24h rain that scores 100 (mm)", 20, 200, int(RAIN_FULL_MM), 5
        )
    return label, lat, lon, percent, float(rain_cap)


# --------------------------------------------------------------- rain panel

def rain_panel(state: RainState, where: str):
    st.subheader("Rainfall forecast")
    if state.mode == "unavailable":
        st.error(
            f"Live rainfall is unavailable: {state.message} "
            "Scores below are computed without the rainfall factor and marked partial."
        )
        return
    s = state.summary
    if state.mode == "live":
        st.success(f"LIVE from Open-Meteo for {where}. Fetched {s.fetched_at:%H:%M} UTC.")
    else:
        st.warning(f"CACHED response for {where} (fetched {s.fetched_at:%H:%M} UTC). {state.message}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Next 24 h", fmt_mm(s.total_24h_mm))
    c2.metric("Next 48 h", fmt_mm(s.total_48h_mm))
    c3.metric("Peak hour", fmt_mm(s.peak_hourly_mm), help=s.peak_hour or None)
    c4.metric(
        "Max chance of rain",
        "missing" if s.max_probability_pct is None else f"{s.max_probability_pct:.0f}%",
    )
    if not s.complete:
        st.caption("Fewer than 24 hours of data were available; the 24 h total may be understated.")

    hours = [p.time[11:16] for p in s.window]
    fig = go.Figure(
        go.Bar(
            x=hours,
            y=[p.precipitation_mm or 0 for p in s.window],
            customdata=[
                "n/a" if p.probability_pct is None else f"{p.probability_pct:.0f}%" for p in s.window
            ],
            hovertemplate="%{x}<br>%{y:.1f} mm<br>chance %{customdata}<extra></extra>",
            marker_color="#4C78A8",
        )
    )
    fig.update_layout(
        height=210,
        margin=dict(l=0, r=0, t=8, b=0),
        yaxis_title="mm per hour",
        xaxis_title="Local hour (next 24 h)",
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(
        "One forecast covers the whole ward, so rainfall lifts or lowers every score together; "
        "the other factors decide the order."
    )


# ----------------------------------------------------------- workflow state

def _init_state(loaded):
    if "hotspots" not in st.session_state:
        st.session_state["hotspots"] = tuple(loaded.hotspots)
        st.session_state["status_log"] = []


def _reset_demo(loaded):
    st.session_state["hotspots"] = tuple(loaded.hotspots)
    st.session_state["status_log"] = []
    st.session_state.pop("last_report", None)


def _change_status(hotspot_id: str, status: str):
    hotspots, event = set_status(st.session_state["hotspots"], hotspot_id, status)
    st.session_state["hotspots"] = hotspots
    if event:
        st.session_state["status_log"].insert(0, event)


def loop_html() -> str:
    steps = [
        ("1. Citizen reports", "location, severity, note, photo link"),
        ("2. Ward inspects", "works the ranked queue before rain"),
        ("3. NGOs and academia validate", "add reports, review the weights"),
        ("4. Status returns", "the resident sees rank and status"),
    ]
    boxes = "".join(
        f'<div class="dw-step"><b>{a}</b><span>{b}</span></div>' for a, b in steps
    )
    return f'<div class="dw-loop">{boxes}</div>'


# ------------------------------------------------------------------ sections

def site_detail(ranked, rain_mm):
    """Map, selected-site explanation, affected assets and status buttons."""
    ids = [r.hotspot.id for r in ranked]
    by_id = {r.hotspot.id: r for r in ranked}

    pending = st.session_state.pop("_pending_sel", None)
    if pending in by_id:
        st.session_state["sel_id"] = pending
    if st.session_state.get("sel_id") not in by_id:
        first_open = next((r for r in ranked if r.hotspot.status != "resolved"), ranked[0])
        st.session_state["sel_id"] = first_open.hotspot.id

    st.subheader("Map and site detail")
    sel = st.selectbox(
        "Choose a hotspot (or click one on the map)",
        ids,
        format_func=lambda i: f"#{by_id[i].rank}  {by_id[i].hotspot.name}",
        key="sel_id",
    )
    item = by_id[sel]
    h = item.hotspot

    left, right = st.columns([3, 2])
    with left:
        ver = st.session_state.setdefault("map_ver", 0)
        event = st.plotly_chart(
            build_map(ranked, sel),
            key=f"map_{ver}",
            on_select="rerun",
            selection_mode="points",
            width="stretch",
            config={"displayModeBar": False},
        )
        clicked = selected_id_from_event(event)
        if clicked:
            st.session_state["map_ver"] = ver + 1  # fresh chart so the next click registers
            if clicked != sel and clicked in by_id:
                st.session_state["_pending_sel"] = clicked
            st.rerun()
        st.caption("Marker colour = priority band, number = queue rank, grey = resolved. Map data from OpenStreetMap.")
    with right:
        st.markdown(f"**{h.name}**")
        st.markdown(item.result.explain())
        st.markdown(stacked_bar_html(item.result), unsafe_allow_html=True)
        st.markdown(legend_html(), unsafe_allow_html=True)

        st.markdown("**Affected assets**")
        st.write(assets_line(h))
        for a in affected_assets(h):
            st.caption(f"{a.label}: {a.detail}")
        if h.access_note:
            st.caption(f"Field note: {h.access_note}")
        if h.note:
            st.caption(f"Reporter note: {h.note}")
        if h.photo_url:
            st.link_button("View reporter's photo", h.photo_url)
        st.caption(f"Source: {h.source} · Complaints in 12 months: {h.complaints_12m if h.complaints_12m is not None else 'missing'}")

        st.markdown(f"**Status: {h.status}**")
        b1, b2, b3 = st.columns(3)
        b1.button("Inspected", disabled=h.status == "inspected", on_click=_change_status, args=(h.id, "inspected"), key=f"i_{h.id}")
        b2.button("Resolved", disabled=h.status == "resolved", on_click=_change_status, args=(h.id, "resolved"), key=f"r_{h.id}")
        b3.button("Reopen", disabled=h.status == "pending", on_click=_change_status, args=(h.id, "pending"), key=f"p_{h.id}")
        st.caption("Prototype: anyone can change status. A real deployment would restrict this to ward staff.")

    with st.expander("Score breakdown table"):
        st.dataframe(pd.DataFrame(detail_rows(h, item.result, rain_mm)), hide_index=True, width="stretch")

    log = st.session_state["status_log"]
    if log:
        st.markdown("**Recent status changes**")
        for ev in log[:5]:
            st.caption(f"{ev.time:%H:%M} UTC · {ev.hotspot_name}: {ev.old} → {ev.new}")


def report_section(ranked):
    st.subheader("Citizen report")
    st.markdown(loop_html(), unsafe_allow_html=True)
    hotspots = st.session_state["hotspots"]
    centre = centroid(hotspots) or (23.8103, 90.4125)
    by_id = {h.id: h for h in hotspots}

    with st.form("report_form", clear_on_submit=True):
        kind = st.radio("This report is", ["A new location", "Another complaint at a listed site"], horizontal=True)
        repeat_id = st.selectbox(
            "If it is a listed site, which one?",
            list(by_id),
            format_func=lambda i: by_id[i].name,
        )
        name = st.text_input("Where is the problem? (short description)", max_chars=80, placeholder="e.g. Lane behind the mosque")
        c1, c2 = st.columns(2)
        lat = c1.number_input("Latitude", value=round(centre[0], 5), format="%.5f", min_value=-90.0, max_value=90.0)
        lon = c2.number_input("Longitude", value=round(centre[1], 5), format="%.5f", min_value=-180.0, max_value=180.0)
        severity = st.slider("Severity (1 = minor, 5 = road unusable)", 1, 5, 3)
        access = st.selectbox(
            "Does it block access to something important?",
            [v for v, _ in ACCESS_CHOICES],
            format_func=dict(ACCESS_CHOICES).get,
        )
        note = st.text_area("Note (optional)", max_chars=300)
        photo = st.text_input("Photo link (optional, http or https)")
        st.caption("Do not include names, phone numbers or other private details.")
        submitted = st.form_submit_button("Submit report")

    if submitted:
        repeat = kind.startswith("Another")
        try:
            if repeat:
                target = by_id[repeat_id]
                outcome = submit_report(
                    hotspots, name=target.name, lat=target.lat, lon=target.lon, severity=severity,
                    note=note, photo_url=photo, repeat_of=repeat_id,
                )
            else:
                outcome = submit_report(
                    hotspots, name=name, lat=lat, lon=lon, severity=severity, note=note,
                    photo_url=photo, critical_access=access,
                )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["hotspots"] = outcome.hotspots
            st.session_state["last_report"] = outcome.hotspot_id
            near = None if repeat else nearest_hotspot(hotspots, lat, lon)
            far = None if repeat else far_from_ward_warning(hotspots, lat, lon)
            st.session_state["report_notes"] = [
                n
                for n in (
                    far,
                    f"This point is {near[1]:.0f} m from '{near[0].name}'. If it is the same problem, use 'Another complaint at a listed site' so repeat complaints count."
                    if near and near[1] <= NEARBY_METRES
                    else None,
                )
                if n
            ]
            st.rerun()

    last = st.session_state.get("last_report")
    if last:
        info = track_report(ranked, last)
        if info:
            st.success(status_message(info))
        for n in st.session_state.get("report_notes", []):
            st.warning(n)

    lookup = st.text_input("Track a report by its ID (for example H11)")
    if lookup.strip():
        info = track_report(ranked, lookup)
        if info:
            st.info(status_message(info))
        else:
            st.warning(f"No report found with ID '{lookup.strip()}'.")


def _secret(name: str):
    """Read a Streamlit secret; None if secrets are not configured."""
    try:
        return st.secrets.get(name)
    except Exception:
        return None


def policy_section(ranked, rain_mm):
    st.subheader("Policy insight")
    insight = policy_insight(ranked, rain_mm)
    if insight is None:
        st.info("No open sites to act on.")
        return None
    st.success(insight.headline)
    for b in insight.bullets:
        st.markdown(f"- {b}")
    st.caption(insight.caveat)
    return insight


def export_section(ranked, rain_mm, insight):
    st.subheader("Export and brief")
    st.download_button(
        "Download ranked queue (CSV)",
        ranked_to_csv(ranked, rain_mm),
        "drainwatch_ranked_queue.csv",
        "text/csv",
    )
    key = _secret("GEMINI_API_KEY")
    if not key:
        st.caption("Optional AI brief: add GEMINI_API_KEY to Streamlit secrets to enable it. Everything else works without it.")
        return
    st.caption("Sends only site names, scores and bands to Google's Gemini API. No notes, photos or coordinates.")
    if st.button("Generate plain-language brief"):
        prompt = build_prompt(ranked, rain_mm, insight.headline if insight else "none")
        try:
            with st.spinner("Writing brief..."):
                st.session_state["brief"] = generate_brief(prompt, key, _secret("GEMINI_MODEL") or DEFAULT_MODEL)
        except GeminiError as exc:
            st.session_state.pop("brief", None)
            st.warning(f"{exc} The ranking above is unaffected.")
    if st.session_state.get("brief"):
        st.markdown(st.session_state["brief"])
        st.caption("AI-generated draft. Check it against the queue before using it.")


# --------------------------------------------------------------------- main

def main():
    st.title("💧 DrainWatch BD")
    st.markdown(
        "**Which reported drainage problems should the ward inspect first before the rain?** "
        "Citizen and ward reports plus a live rainfall forecast become an explained inspection queue."
    )
    st.caption(LIMITS_LINE)

    try:
        loaded = _load()
    except DataError as exc:
        st.error(f"Hotspot data could not be loaded: {exc}")
        st.stop()
    for w in loaded.warnings:
        st.warning(w)
    if not loaded.hotspots:
        st.error("The hotspot file has no usable rows.")
        st.stop()
    _init_state(loaded)
    hotspots = st.session_state["hotspots"]
    if all(h.is_demo for h in hotspots):
        st.info("DEMO DATA: every hotspot below is an invented example, not a real field report.")
    elif any(h.is_demo for h in hotspots):
        st.info("Some hotspots are demo records (tagged 'demo'); the others came from citizen or field reports.")

    label, lat, lon, percent, rain_cap = sidebar()
    st.sidebar.header("Data")
    if st.sidebar.button("Reset demo data"):
        _reset_demo(loaded)
        st.rerun()
    st.sidebar.download_button(
        "Download hotspots (CSV)", hotspots_to_csv_text(hotspots), "drainwatch_hotspots.csv", "text/csv"
    )
    st.sidebar.caption("Reports and status changes last for this browser session only; download the CSV to keep them.")

    if lat is None:
        lat, lon = centroid(hotspots)
        label = "the hotspot area"
    if sum(percent.values()) == 0:
        st.stop()

    cache = st.session_state.setdefault("rain_cache", {})
    state = resolve_rain(lambda: _rain(round(lat, 3), round(lon, 3)), (round(lat, 3), round(lon, 3)), cache)
    rain_panel(state, label)

    ranked = rank_hotspots(
        hotspots,
        state.rain_24h_mm,
        weights_from_percent(percent),
        rain_full_mm=rain_cap,
    )
    open_items = [r for r in ranked if r.hotspot.status != "resolved"]
    high = sum(1 for r in open_items if r.result.band == "High")

    st.subheader("Inspection queue")
    m1, m2, m3 = st.columns(3)
    m1.metric("Open sites", len(open_items))
    m2.metric("High priority", high)
    m3.metric("Resolved", len(ranked) - len(open_items))
    st.markdown(legend_html(), unsafe_allow_html=True)
    st.markdown(queue_html(ranked), unsafe_allow_html=True)
    st.caption(
        "Bands: 70+ High, 40 to 69 Medium, below 40 Monitor. Resolved sites sit at the bottom. "
        "A 'partial score' means an input is missing; it is dropped and the other weights re-scaled, never counted as zero."
    )

    site_detail(ranked, state.rain_24h_mm)
    insight = policy_section(ranked, state.rain_24h_mm)
    report_section(ranked)
    export_section(ranked, state.rain_24h_mm, insight)

    st.divider()
    st.caption(
        "Prototype built with AI tools for the BIP World Town Planning Day 2026 Apps4Solutions track. "
        "Weights and thresholds are adjustable assumptions."
    )


main()
