"""GridRisk AI — location outage dashboard.

A user-facing companion to the project pulse. Pick a point on satellite
imagery (or search for a town) and see what the project can honestly say:

* South Africa — national day-ahead load-shedding probability.
* Kenya — upcoming KPLC **planned** interruption notices for the county.
* Nigeria — the dated collapse record and an explicit no-model statement.

Run from the project root:

    .venv/bin/streamlit run app/outage_risk.py

The dashboard is read-only. Weather is display-only context and never enters
the trained features or labels (Day 18 leakage rule).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dashboard import location as L  # noqa: E402
from src.dashboard.state import PILOT_CITIES  # noqa: E402

st.set_page_config(
    page_title="GridRisk AI — Location risk",
    page_icon="🛰️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; padding-bottom: 2.2rem; }
      .gr-title { font-size: 2.0rem; font-weight: 780; letter-spacing: -0.03em;
                  margin: 0; line-height: 1.1; }
      .gr-sub { color: #8b93a7; font-size: 0.92rem; margin-top: 0.3rem; }
      .gr-card { border: 1px solid rgba(148,163,184,0.20); border-radius: 0.8rem;
                 padding: 0.8rem 1rem; margin-bottom: 0.6rem;
                 background: rgba(15,23,42,0.35); }
      .gr-kv { color: #8b93a7; font-size: 0.72rem; text-transform: uppercase;
               letter-spacing: 0.08em; }
      .gr-val { font-size: 1.05rem; font-weight: 680; margin-top: 0.1rem; }
      .gr-note { color: #8b93a7; font-size: 0.78rem; line-height: 1.45; }
      .gr-chip { display: inline-block; padding: 0.18rem 0.55rem;
                 border-radius: 999px; font-size: 0.72rem; font-weight: 650;
                 margin-right: 0.25rem; }
      .gr-ok { background: rgba(34,197,94,0.13); color: #4ade80;
               border: 1px solid rgba(34,197,94,0.35); }
      .gr-warn { background: rgba(250,204,21,0.13); color: #facc15;
                 border: 1px solid rgba(250,204,21,0.35); }
      .gr-info { background: rgba(56,189,248,0.13); color: #7dd3fc;
                 border: 1px solid rgba(56,189,248,0.35); }
      .gr-danger { background: rgba(239,68,68,0.13); color: #f87171;
                   border: 1px solid rgba(239,68,68,0.35); }
      [data-testid="stMetricValue"] { font-size: 1.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

FLAGS = {"KEN": "🇰🇪", "NGA": "🇳🇬", "ZAF": "🇿🇦"}


# ---------------------------------------------------------------------------
# Cached data access
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def cached_location(lat: float, lon: float) -> dict:
    return L.locate(lat, lon)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_planned_outages() -> pd.DataFrame:
    return L.load_planned_outages()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_zaf_forecast() -> dict:
    return L.latest_zaf_forecast()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_nga_summary() -> dict:
    return L.nga_summary()


@st.cache_data(ttl=600, show_spinner=False)
def cached_weather(lat: float, lon: float) -> dict:
    return L.point_weather(lat, lon)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_place_search(query: str) -> list[dict]:
    return L.search_places(query)


# ---------------------------------------------------------------------------
# Session selection
# ---------------------------------------------------------------------------
if "sel_lat" not in st.session_state:
    st.session_state.sel_lat = -1.2921   # Nairobi
    st.session_state.sel_lon = 36.8219
if "last_map_click" not in st.session_state:
    st.session_state.last_map_click = None

selected_lat = float(st.session_state.sel_lat)
selected_lon = float(st.session_state.sel_lon)


def select_point(lat: float, lon: float) -> None:
    st.session_state.sel_lat = float(lat)
    st.session_state.sel_lon = float(lon)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
now = datetime.now(timezone.utc)
st.markdown(
    '<div class="gr-title">⚡ GridRisk AI — Location outage risk</div>'
    '<div class="gr-sub">Satellite map · validated evidence · explicit limits. '
    'Click anywhere on the imagery or search for a place.</div>',
    unsafe_allow_html=True,
)
st.caption(
    f"Data snapshot {now:%d %b %Y} UTC · South Africa: national day-ahead model · "
    "Kenya: planned KPLC notices · Nigeria: no model claimed"
)

# ---------------------------------------------------------------------------
# Search / quick selection
# ---------------------------------------------------------------------------
search_col, city_col, spacer = st.columns([2.1, 2.1, 1.2])
with search_col:
    query = st.text_input(
        "Search a place",
        placeholder="Nairobi, Lagos, Durban, …",
        key="place_query",
        help="Open-Meteo geocoding; results are restricted to the three pilot countries.",
    )
    if query.strip():
        results = cached_place_search(query.strip())
        if results:
            labels = [
                f"{r['name']} · {r['admin1'] or r['country']} ({r['country_iso3']})"
                for r in results
            ]
            choice = st.selectbox(
                "Matches", labels, index=None, placeholder="Choose a match",
                key="place_match",
            )
            if choice:
                match = results[labels.index(choice)]
                if (abs(float(match["lat"]) - selected_lat) > 1e-7
                        or abs(float(match["lon"]) - selected_lon) > 1e-7):
                    select_point(match["lat"], match["lon"])
                    st.rerun()
        else:
            st.caption("No Kenya / Nigeria / South Africa match — try a town or city name.")

with city_col:
    city_labels = [
        f"{FLAGS.get(c['country'], '')} {c['city']} ({c['country']})"
        for c in PILOT_CITIES
    ]
    city_choice = st.selectbox(
        "Or jump to a pilot city", ["—"] + city_labels, index=0,
        key="city_choice",
    )
    if st.button("Go to city", width="stretch", disabled=city_choice == "—"):
        city = PILOT_CITIES[city_labels.index(city_choice)]
        select_point(city["lat"], city["lon"])
        st.rerun()

# ---------------------------------------------------------------------------
# Location resolution and map
# ---------------------------------------------------------------------------
location = cached_location(round(selected_lat, 7), round(selected_lon, 7))
planned = cached_planned_outages()
horizon = int(st.sidebar.slider(
    "KPLC notice horizon (days)", min_value=3, max_value=30, value=14, step=1,
    help="How far ahead to look for official planned interruptions in Kenya.",
))
show_boundaries = st.sidebar.checkbox("Show admin boundaries", value=True)
show_labels = st.sidebar.checkbox("Show place labels", value=True)
st.sidebar.divider()
st.sidebar.markdown("### Data freshness")
if plotted := cached_zaf_forecast():
    if plotted.get("ok"):
        st.sidebar.caption(
            f"ZAF labels: **{pd.Timestamp(plotted['label_cutoff']):%d %b %Y}**  \n"
            f"ZAF forecast day: **{pd.Timestamp(plotted['target_date']):%d %b %Y}**"
        )
if not planned.empty:
    upcoming_all = L.upcoming_outages(planned, horizon_days=horizon)
    if not upcoming_all.empty:
        st.sidebar.caption(
            f"KPLC notices: **{len(upcoming_all)}** planned event(s) in the next "
            f"{horizon} days  \nLatest notice: "
            f"**{upcoming_all['event_date'].max():%d %b %Y}**"
        )
st.sidebar.divider()
st.sidebar.caption(
    "Satellite tiles: Esri World Imagery.  \n"
    "Weather: Open-Meteo forecast, display only.  \n"
    "**No unsupported per-location outage probability is published.**"
)

map_col, panel_col = st.columns([1.55, 1], gap="large")

with map_col:
    fmap = L.build_satellite_map(
        selected_lat, selected_lon, location,
        show_boundaries=show_boundaries,
        show_labels=show_labels,
        horizon_days=horizon,
        planned_events=planned,
    )
    map_state = st_folium(
        fmap,
        key="satellite_map",
        height=585,
        use_container_width=True,
        returned_objects=["last_clicked"],
    )
    clicked = (map_state or {}).get("last_clicked")
    if clicked:
        click_key = (round(float(clicked["lat"]), 6), round(float(clicked["lng"]), 6))
        if click_key != st.session_state.last_map_click:
            st.session_state.last_map_click = click_key
            select_point(*click_key)
            st.rerun()
    st.caption(
        "Basemap: Esri World Imagery (© Esri, Maxar, Earthstar Geographics, "
        "and the GIS User Community). Click the imagery to move the pin."
    )

with panel_col:
    if not location["in_pilot"]:
        st.warning(
            "This point is outside the three pilot countries. The project has "
            "no validated outage data for this location."
        )
        st.markdown(
            '<div class="gr-card"><div class="gr-kv">Selected point</div>'
            f'<div class="gr-val">{selected_lat:.4f}, {selected_lon:.4f}</div>'
            '<div class="gr-note">Only Kenya, Nigeria and South Africa are in scope.</div></div>',
            unsafe_allow_html=True,
        )
    else:
        iso3 = location["country_iso3"]
        place_bits = [location.get("adm1"), location.get("adm2")]
        place_line = " · ".join([b for b in place_bits if b]) or "Country level"
        resolution = "point-in-polygon" if location["method"] == "boundaries" else "approximate bbox"
        st.markdown(
            f'<div class="gr-card"><div class="gr-kv">Selected location</div>'
            f'<div class="gr-val">{FLAGS.get(iso3, "")} {place_line}</div>'
            f'<div class="gr-note">{location["country"]} · '
            f'{selected_lat:.4f}, {selected_lon:.4f} · resolved by {resolution}</div></div>',
            unsafe_allow_html=True,
        )

        if iso3 == "ZAF":
            forecast = cached_zaf_forecast()
            if not forecast.get("ok"):
                st.warning(f"ZAF forecast unavailable: {forecast.get('reason', 'unknown reason')}")
                st.info(
                    "Rebuild the snapshot with `python -m src.datasets.zaf_daily` "
                    "and `python -m src.experiments.zaf_daily_model`."
                )
            else:
                p = float(forecast["probability"])
                target = pd.Timestamp(forecast["target_date"])
                cutoff = pd.Timestamp(forecast["label_cutoff"])
                age_days = (date.today() - target.date()).days
                st.markdown(
                    f'<div class="gr-card"><div class="gr-kv">Day-ahead national model</div>'
                    f'<div class="gr-val">P(load-shedding) = {p:.2%}</div>'
                    f'<div class="gr-note">Forecast day {target:%d %b %Y} (SAST) · '
                    f'trained on {forecast["train_rows"]:,} labelled days · '
                    f'national target, not province-specific</div></div>',
                    unsafe_allow_html=True,
                )
                gauge = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=p * 100,
                    number={"suffix": "%", "font": {"size": 34}},
                    gauge={
                        "axis": {"range": [0, 100], "ticksuffix": "%"},
                        "bar": {"color": "#38bdf8"},
                        "bgcolor": "rgba(148,163,184,0.10)",
                        "borderwidth": 0,
                    },
                ))
                gauge.update_layout(
                    height=190, margin=dict(l=18, r=18, t=10, b=0),
                    paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#cbd5e1"),
                )
                st.plotly_chart(gauge, width="stretch", config={"displayModeBar": False})

                if age_days > 2:
                    st.warning(
                        f"The latest scoreable day in this snapshot is "
                        f"{target:%d %b %Y}; the free Eskom mirror feed ends "
                        f"{cutoff:%d %b %Y}. This is the most recent honest "
                        "day-ahead score, not a live today/tomorrow forecast."
                    )
                st.caption(
                    "The target is **national** load-shedding. The model cannot "
                    "rank one province above another; Day 16–21 reports explain why."
                )
                metrics = forecast.get("metrics")
                if metrics:
                    a, b = st.columns(2)
                    a.metric("Walk-forward Brier skill", f"{metrics['brier_skill']:+.3f}",
                             help="vs a pinned climatology baseline")
                    b.metric("Calibration ECE", f"{metrics['ece']:.3f}")
                    st.caption(
                        f"{metrics['n']:,} out-of-fold days · pinned reference "
                        f"rate {metrics['ref_rate']:.3f} · raw logistic probabilities"
                    )
                with st.expander("Recent observed load-shedding (national)"):
                    recent = forecast.get("recent")
                    if isinstance(recent, pd.DataFrame) and not recent.empty:
                        recent = recent.copy()
                        recent["date"] = pd.to_datetime(recent["date"])
                        fig = go.Figure(go.Bar(
                            x=recent["date"], y=recent["hours_active"],
                            marker_color="#fb923c",
                            hovertemplate="%{x|%d %b %Y}<br>%{y:.0f} h<extra></extra>",
                        ))
                        fig.update_layout(
                            height=220, margin=dict(l=10, r=10, t=10, b=0),
                            yaxis_title="hours active", xaxis_title="",
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(color="#cbd5e1", size=11),
                        )
                        st.plotly_chart(fig, width="stretch")
                    else:
                        st.caption("No recent label rows in the snapshot.")

        elif iso3 == "KEN":
            upcoming = L.upcoming_outages(planned, horizon_days=horizon)
            matches = L.outages_for_admin1(upcoming, location.get("adm1"))
            st.markdown(
                f'<div class="gr-card"><div class="gr-kv">Official KPLC planned notices</div>'
                f'<div class="gr-val">{len(matches)} interruption(s) in the next {horizon} days</div>'
                f'<div class="gr-note">County/region match for '
                f'{location.get("adm1") or "the selected point"} · planned maintenance only</div></div>',
                unsafe_allow_html=True,
            )
            if not matches.empty:
                table = matches.copy()
                table["Date"] = table["event_date"].dt.strftime("%a %d %b %Y")
                table["Start (EAT)"] = table["time_start_local"].fillna("—")
                table["End (EAT)"] = table["time_end_local"].fillna("—")
                table["Area"] = table["area"].fillna("—")
                table["Notice"] = table["source_document"].fillna("—")
                table["Details"] = table["premises_text"].fillna("").str.slice(0, 220)
                area_filter = st.text_input(
                    "Filter these notices by area / estate",
                    placeholder="e.g. Embakasi, Westlands, Kikuyu",
                    key="ken_area_filter",
                    help="Free-text match against the notice area and details columns.",
                )
                view = table
                if area_filter.strip():
                    needle = area_filter.strip()
                    view = table[
                        table["Area"].str.contains(needle, case=False, na=False)
                        | table["Details"].str.contains(needle, case=False, na=False)
                    ]
                if view.empty:
                    st.info(
                        f"No notice in this county mentions **{area_filter}**. "
                        "The notice may use a different spelling, or the area may not "
                        "have a scheduled interruption."
                    )
                else:
                    st.dataframe(
                        view[["Date", "Start (EAT)", "End (EAT)", "Area", "Details", "Notice"]],
                        hide_index=True, width="stretch", height=330,
                    )
                    st.caption(
                        f"{len(view)} of {len(table)} matched notice(s) shown. KPLC "
                        "publishes planned interruption notices; a notice can be "
                        "cancelled and absence of a notice is not evidence of reliable supply."
                    )
            else:
                st.info(
                    f"No KPLC planned notice matched **{location.get('adm1') or 'this location'}** "
                    f"in the next {horizon} days. This does **not** mean the area "
                    "will have uninterrupted power."
                )
            st.markdown(
                '<div class="gr-card"><div class="gr-kv">Why no probability?</div>'
                '<div class="gr-note">Kenya has no validated unplanned-outage label '
                'source. KPLC notices are scheduled maintenance and are never used '
                'as an unplanned-outage target (Day 7 / Day 11 reports).</div></div>',
                unsafe_allow_html=True,
            )

        else:  # NGA
            summary = cached_nga_summary()
            st.markdown(
                '<div class="gr-card"><div class="gr-kv">Nigeria</div>'
                '<div class="gr-val">No outage model is claimed</div>'
                '<div class="gr-note">The 2025 test window contains two positive '
                'collapse days; any classifier would be memorising, so none is fit.</div></div>',
                unsafe_allow_html=True,
            )
            if summary.get("ok"):
                a, b = st.columns(2)
                a.metric("Dated grid collapses", f"{summary['events']}")
                span = summary["span"]
                b.metric("Record span", f"{span[0]:%Y}–{span[1]:%Y}")
                st.caption(summary["reason"])
                events = L.load_collapse_events()
                table = events.sort_values("event_datetime_utc", ascending=False).head(8).copy()
                table["Date (UTC)"] = table["event_datetime_utc"].dt.strftime("%d %b %Y %H:%M")
                table["Type"] = table["event_type"].fillna("—")
                table["Source"] = table["source_document"].fillna("—")
                st.dataframe(
                    table[["Date (UTC)", "Type", "Source"]],
                    hide_index=True, width="stretch", height=300,
                )
            else:
                st.info(summary.get("reason", "NERC extraction unavailable."))

        # ---- weather context (display only) --------------------------------
        weather = cached_weather(round(selected_lat, 6), round(selected_lon, 6))
        with st.expander("Weather context — display only", expanded=False):
            if weather.get("ok"):
                cur = weather["current"]
                w1, w2, w3 = st.columns(3)
                w1.metric("Temp", f"{cur['temperature_c']:.1f} °C"
                          if cur.get("temperature_c") is not None else "—")
                w2.metric("Rain", f"{cur['precip_mm']:.1f} mm"
                          if cur.get("precip_mm") is not None else "—")
                w3.metric("Wind", f"{cur['wind_kmh']:.0f} km/h"
                          if cur.get("wind_kmh") is not None else "—")
                st.caption(
                    f"{cur.get('conditions', '—')} · observed "
                    f"{weather.get('observed_utc') or '—'} UTC · Open-Meteo forecast. "
                    "Weather is **not** a live model input: the Day 21 ablation found "
                    "no material skill beyond persistence."
                )
            else:
                st.caption(weather.get("reason", "Weather unavailable."))

# ---------------------------------------------------------------------------
# Footer / limits
# ---------------------------------------------------------------------------
st.divider()
with st.expander("What this dashboard can and cannot answer", expanded=False):
    st.markdown(
        """
        **South Africa** — a calibrated *national* day-ahead probability of
        load-shedding. It does not identify which province, municipality or
        feeder will be affected.

        **Kenya** — official KPLC *planned* interruption notices resolved to a
        county/region. These are scheduled maintenance events, not unplanned
        outage labels, and a notice can be cancelled.

        **Nigeria** — no model. The dashboard shows the dated NERC collapse
        record and the reason a classifier is not justified.

        **Weather** — current conditions are display-only context from the
        Open-Meteo forecast API. They are never training features or labels.

        **Satellite imagery** — Esri World Imagery tiles, shown for geographic
        context; the model does not use imagery.
        """
    )
    st.caption(
        "Sources and evidence: `reports/day18_zaf_model.md`, "
        "`reports/day21_zaf_ablation.md`, `reports/day11_kplc_planned_events.md`, "
        "`reports/day20_nga_baseline.md`, `reports/day30_final_summary.md`."
    )
