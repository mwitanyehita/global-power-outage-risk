"""GridRisk AI — live project dashboard.

Shows what the project is doing *right now*: which plan day is in flight,
which files just landed, whether the downloaded data still passes its own
validation, and what the nine pilot cities are experiencing at this moment.

Run it from the project root:

    .venv/bin/streamlit run app/dashboard.py

The dashboard is read-only. It never writes to the repository, and the live
conditions panel is explicitly display-only: forecast values must never be
used as training features or labels (project leakage rule, Day 18).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dashboard import state as S  # noqa: E402

st.set_page_config(page_title="GridRisk AI — Live", page_icon="⚡", layout="wide")

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
      .block-container { padding-top: 4.1rem; padding-bottom: 2.2rem; }
      .gr-title { font-size: 1.9rem; font-weight: 750; letter-spacing: -0.02em;
                  margin: 0; line-height: 1.15; }
      .gr-sub { color: #8b93a7; font-size: 0.86rem; margin-top: 0.25rem; }
      .gr-clock { font-variant-numeric: tabular-nums; font-size: 1.02rem;
                  font-weight: 650; letter-spacing: 0.01em; }
      .gr-chip { display: inline-block; padding: 0.16rem 0.55rem; border-radius: 999px;
                 font-size: 0.74rem; font-weight: 600; letter-spacing: 0.02em; }
      .gr-done   { background: rgba(34,197,94,0.16);  color: #4ade80; border: 1px solid rgba(34,197,94,0.35); }
      .gr-wip    { background: rgba(250,204,21,0.16); color: #facc15; border: 1px solid rgba(250,204,21,0.35); }
      .gr-todo   { background: rgba(148,163,184,0.13);color: #94a3b8; border: 1px solid rgba(148,163,184,0.28); }
      .gr-bad    { background: rgba(239,68,68,0.16);  color: #f87171; border: 1px solid rgba(239,68,68,0.35); }
      .gr-skip   { background: rgba(148,163,184,0.07);color: #64748b; border: 1px dashed rgba(148,163,184,0.28); }
      .gr-card { border: 1px solid rgba(148,163,184,0.18); border-radius: 0.7rem;
                 padding: 0.7rem 0.9rem; margin-bottom: 0.55rem; }
      .gr-kv { color: #8b93a7; font-size: 0.76rem; text-transform: uppercase;
               letter-spacing: 0.06em; }
      .gr-val { font-size: 1.02rem; font-weight: 650; margin-top: 0.1rem; }
      .gr-note { color: #8b93a7; font-size: 0.78rem; }
      .gr-flash { border-left: 3px solid #38bdf8; padding: 0.35rem 0.7rem;
                  margin: 0.25rem 0; font-size: 0.85rem; }
      hr { margin-top: 0.6rem; margin-bottom: 0.6rem; }
      [data-testid="stMetricValue"] { font-size: 1.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

STATUS_CLASS = {
    "done": "gr-done", "in progress": "gr-wip",
    "pending": "gr-todo", "blocked": "gr-bad", "skipped": "gr-skip",
}
STATUS_MARK = {
    "done": "● done", "in progress": "◐ in progress",
    "pending": "○ pending", "blocked": "✕ blocked", "skipped": "– skipped",
}


# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def cached_health():
    """Re-run the weather and boundary validation checks."""
    return S.weather_health(), S.boundary_health()


@st.cache_data(ttl=900, show_spinner=False)
def cached_weather_long() -> pd.DataFrame:
    """All nine cities' hourly history as one long frame (cached)."""
    frames = []
    for path in S.weather_files():
        try:
            df = S.load_weather_frame(path)
        except (OSError, ValueError, KeyError):
            continue
        city = path.stem.split("_", 1)[1].replace("-", " ").title()
        country = path.stem.split("_", 1)[0]
        df = df[["time", "temperature_2m", "precipitation", "wind_speed_10m",
                 "wind_gusts_10m", "relative_humidity_2m", "weather_code"]]
        df = df.assign(city=city, country=country)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


@st.cache_data(ttl=600, show_spinner=False)
def cached_eskom():
    """Parsed Eskom hourly load-reduction series."""
    return S.eskom_load_reduction()


@st.cache_data(ttl=600, show_spinner=False)
def cached_eskom_episodes():
    """Contiguous load-shedding episodes derived from the Eskom series."""
    return S.eskom_episodes(S.eskom_load_reduction())


@st.cache_data(ttl=900, show_spinner=False)
def cached_boundary_rings(level: str, countries: tuple[str, ...], tolerance: float):
    """Simplified polygon outlines for one ADM level, grouped by country.

    Returns ``{iso3: [(unit_name, [(lon, lat), ...]), ...]}``. Only exterior
    rings are kept — interior holes add payload without adding information at
    this zoom level, and the ring is rounded to 3 decimals (~100 m).
    """
    import geopandas as gpd

    out: dict[str, list[tuple[str, list[tuple[float, float]]]]] = {}
    for iso3 in countries:
        path = S.BOUNDARY_DIR / f"{iso3}_{level}.geojson"
        if not path.exists():
            continue
        gdf = gpd.read_file(path)
        if tolerance > 0:
            gdf = gdf.set_geometry(gdf.geometry.simplify(tolerance, preserve_topology=True))
        name_col = next(
            (c for c in ("shapeName", "shapeGroup", "name") if c in gdf.columns),
            None,
        )
        rings = []
        for geom, row in zip(gdf.geometry, gdf.to_dict("records")):
            if geom is None or geom.is_empty:
                continue
            polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for poly in polys:
                coords = [(round(x, 3), round(y, 3)) for x, y in poly.exterior.coords]
                if len(coords) >= 3:
                    rings.append((row.get(name_col, "?"), coords))
        out[iso3] = rings
    return out


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.markdown("### ⚡ GridRisk AI")
st.sidebar.caption("Live project pulse — read-only view of the workspace.")
st.sidebar.caption(
    "Visitor-facing satellite view: `streamlit run app/outage_risk.py`"
)

refresh_choice = st.sidebar.selectbox(
    "Live refresh", ["Off", "5 s", "15 s", "30 s", "60 s"], index=2,
    help="How often the live panels re-read the workspace and the API.",
)
refresh_seconds = int(refresh_choice.split()[0]) if refresh_choice != "Off" else None

countries = st.sidebar.multiselect(
    "Pilot countries", ["KEN", "ZAF", "NGA"], default=["KEN", "ZAF", "NGA"],
)
adm_level = st.sidebar.selectbox(
    "Boundary level", ["ADM0", "ADM1", "ADM2"], index=1,
    help="ADM2 is the largest layer — it takes a moment to render.",
)
grid_variable = st.sidebar.selectbox(
    "Live map colours by",
    ["temperature_c", "precip_mm", "wind_kmh", "gust_kmh", "humidity_pct"],
    index=0,
)
show_raw_detail = st.sidebar.checkbox("Show raw grid detail", value=False)

if not countries:
    st.sidebar.warning("Pick at least one country.")
    st.stop()

st.sidebar.divider()
st.sidebar.caption(
    "Live conditions come from the Open-Meteo forecast API — **display only**. "
    "They must never enter training features or labels."
)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def render_clock() -> None:
    now = datetime.now(timezone.utc)
    git = S.git_snapshot()
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            '<div class="gr-title">GridRisk AI — Global Power Outage Risk</div>'
            '<div class="gr-sub">Live project pulse · readings are display-only '
            'until a model exists</div>',
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f'<div style="text-align:right">'
            f'<div class="gr-clock">{now:%H:%M:%S} UTC</div>'
            f'<div class="gr-note">{now:%a %d %b %Y} · '
            f'branch <code>{git["branch"]}</code> · '
            f'<code>{git["short"]}</code> · {git["commits"]} commits'
            f'{" · " + str(git["dirty_files"]) + " uncommitted" if git["dirty_files"] else ""}'
            f'</div></div>',
            unsafe_allow_html=True,
        )


st.fragment(render_clock, run_every="1s")()


# ---------------------------------------------------------------------------
# Live body
# ---------------------------------------------------------------------------
def _status_chip(status: str) -> str:
    cls = STATUS_CLASS.get(status, "gr-todo")
    return f'<span class="gr-chip {cls}">{STATUS_MARK.get(status, status)}</span>'


def _snapshot() -> dict:
    """Cheap signature of the workspace, used to spot what just changed."""
    sig = {}
    for row in S.file_activity(limit=200):
        sig[row["path"]] = row["mtime"].isoformat()
    sig["__git__"] = S.git_snapshot()["commit"]
    return sig


def render_changes() -> None:
    current = _snapshot()
    previous = st.session_state.get("prev_snapshot")
    st.session_state["prev_snapshot"] = current
    if not previous:
        return
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    modified = sorted(
        p for p in set(current) & set(previous) if current[p] != previous[p]
    )
    if not (added or removed or modified):
        st.markdown(
            '<div class="gr-note">◦ No workspace changes since the last refresh.</div>',
            unsafe_allow_html=True,
        )
        return
    parts = []
    if added:
        parts.append(f"**{len(added)} new**")
    if modified:
        parts.append(f"**{len(modified)} modified**")
    if removed:
        parts.append(f"**{len(removed)} removed**")
    st.markdown(
        f'<div class="gr-flash">● {" · ".join(parts)} since the last refresh</div>',
        unsafe_allow_html=True,
    )
    for path in (added + modified)[:6]:
        tag = "new" if path in added else "changed"
        st.markdown(
            f'<div class="gr-flash">↻ <code>{path}</code> — {tag}</div>',
            unsafe_allow_html=True,
        )


def render_pulse() -> None:
    now = datetime.now(timezone.utc)
    plan = S.plan_status()
    day_now = S.current_build_day(plan)
    git = S.git_snapshot()
    totals = S.data_totals()

    with st.spinner("Re-validating downloaded data…"):
        health, bounds = cached_health()

    done = sum(1 for r in plan if r["status"] == "done")
    total_steps = len(plan)
    cities_ok = sum(1 for c in health.get("cities", {}).values() if c.get("ok"))
    n_cities = len(health.get("cities", {}))
    units = sum(r["units"] or 0 for r in bounds)

    # ---- KPI row -------------------------------------------------------
    k = st.columns(6)
    k[0].metric("Current day", f"Day {day_now}")
    k[1].metric("Plan steps done", f"{done}/{total_steps}")
    k[2].metric("Cities instrumented", f"{cities_ok}/{n_cities}")
    k[3].metric("Hourly rows", f"{health.get('rows', 0):,}")
    k[4].metric("Boundary units", f"{units:,}")
    k[5].metric("Raw + external", f"{totals['raw_mb'] + totals['external_mb']:.0f} MB")

    # ---- live conditions ----------------------------------------------
    live_df, live_err = S.live_conditions()
    if live_err:
        st.warning(live_err)
    elif not live_df.empty:
        obs = live_df["observed_utc"].iloc[0]
        st.caption(
            f"Live grid observed at **{obs} UTC** from the Open-Meteo forecast API "
            f"— display only, never a training input."
        )

    tabs = st.tabs(["🎯 Pulse", "🌦 Live grid", "⚡ Outages", "🗺 Boundaries", "📦 Provenance"])

    # =====================================================================
    # Tab 1 — Pulse
    # =====================================================================
    with tabs[0]:
        c1, c2 = st.columns([1.45, 1])

        with c1:
            st.subheader("Plan board")
            st.caption("Status is re-derived from the files on disk at every refresh.")

            rows = []
            for r in plan:
                rows.append({
                    "Day": r["day"],
                    "Step": r["task"],
                    "Area": r["category"],
                    "Status": STATUS_MARK.get(r["status"], r["status"]),
                    "Evidence": r["evidence"],
                })
            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True, width="stretch", height=460,
                column_config={
                    "Day": st.column_config.NumberColumn(format="%d", width="small"),
                    "Status": st.column_config.TextColumn(width="small"),
                    "Step": st.column_config.TextColumn(width="medium"),
                    "Area": st.column_config.TextColumn(width="small"),
                    "Evidence": st.column_config.TextColumn(width="medium"),
                },
            )

        with c2:
            st.subheader("Timeline")
            fig = go.Figure()
            colors = {
                "done": "#22c55e", "in progress": "#facc15",
                "pending": "#475569", "skipped": "#334155",
            }
            for r in plan:
                fig.add_trace(go.Scatter(
                    x=[r["day"]], y=[r["task"]],
                    mode="markers+text",
                    marker=dict(
                        size=13,
                        color=colors.get(r["status"], "#475569"),
                        symbol="circle" if r["status"] == "done" else "diamond",
                        line=dict(width=1, color="rgba(255,255,255,0.35)"),
                    ),
                    text=[f"Day {r['day']}"], textposition="middle right",
                    textfont=dict(size=10, color="#94a3b8"),
                    hovertemplate=f"<b>{r['task']}</b><br>Day {r['day']} · "
                                  f"{r['status']}<br>{r['evidence']}<extra></extra>",
                    showlegend=False,
                ))
            fig.update_layout(
                height=460, margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title="Plan day", yaxis_title="",
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#cbd5e1", size=11),
            )
            st.plotly_chart(fig, width="stretch")

            st.subheader("Working now")
            st.markdown(
                f'<div class="gr-card"><div class="gr-kv">In flight</div>'
                f'<div class="gr-val">Day {day_now} — '
                f'{next((r["task"] for r in plan if r["day"] == day_now), "—")}</div>'
                f'<div class="gr-note">{next((r["evidence"] for r in plan if r["day"] == day_now), "")}</div>'
                f"</div>",
                unsafe_allow_html=True,
            )

        st.divider()
        st.subheader("What just happened")
        render_changes()

        a1, a2 = st.columns(2)
        with a1:
            st.markdown("**Recent commits**")
            activity = S.repo_activity(limit=8)
            if activity:
                for row in activity:
                    st.markdown(
                        f'<div class="gr-flash"><code>{row["short"]}</code> · '
                        f'{row["subject"]}<br><span class="gr-note">'
                        f'{row["author"]} · {row["committed"][:16].replace("T", " ")}</span></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No git history available.")
        with a2:
            st.markdown("**Recently touched files**")
            for row in S.file_activity(limit=8):
                touched = row["mtime"].strftime("%Y-%m-%d %H:%M:%S")
                st.markdown(
                    f'<div class="gr-flash"><code>{row["path"]}</code> · '
                    f'{row["size_kb"]} KB<br><span class="gr-note">'
                    f'{touched} UTC</span></div>',
                    unsafe_allow_html=True,
                )

    # =====================================================================
    # Tab 2 — Live grid
    # =====================================================================
    with tabs[1]:
        if live_df.empty:
            st.info("Live conditions are unavailable right now.")
        else:
            view = live_df[live_df["country"].isin(countries)].copy()
            unit = {
                "temperature_c": "°C", "precip_mm": " mm", "wind_kmh": " km/h",
                "gust_kmh": " km/h", "humidity_pct": "%",
            }[grid_variable]

            m1, m2 = st.columns([1.35, 1])
            with m1:
                st.subheader("Pilot grid, right now")
                fig = go.Figure()
                fig.add_trace(go.Scattergeo(
                    lon=view["lon"], lat=view["lat"], mode="markers+text",
                    text=view["city"], textposition="top center",
                    textfont=dict(size=10, color="#cbd5e1"),
                    marker=dict(
                        size=17, color=view[grid_variable], colorscale="Viridis",
                        cmin=view[grid_variable].min(), cmax=view[grid_variable].max(),
                        showscale=True,
                        colorbar=dict(title=grid_variable.replace("_", " "), thickness=10),
                        line=dict(width=1.4, color="rgba(255,255,255,0.75)"),
                    ),
                    customdata=view[["conditions", "temperature_c", "precip_mm",
                                     "wind_kmh", "humidity_pct"]],
                    hovertemplate=(
                        "<b>%{text}</b><br>%{customdata[0]}<br>"
                        "temp %{customdata[1]:.1f} °C<br>"
                        "rain %{customdata[2]:.1f} mm<br>"
                        "wind %{customdata[3]:.0f} km/h<br>"
                        "rh %{customdata[4]:.0f}%"
                        "<extra></extra>"
                    ),
                    showlegend=False,
                ))
                fig.update_layout(
                    height=460, margin=dict(l=0, r=0, t=10, b=0),
                    geo=dict(
                        projection_type="equirectangular",
                        lataxis_range=[-37, 15], lonaxis_range=[3, 44],
                        showland=True, landcolor="rgba(30,41,59,0.65)",
                        showcountries=True, countrycolor="rgba(100,116,139,0.55)",
                        coastlinecolor="rgba(100,116,139,0.7)",
                        bgcolor="rgba(0,0,0,0)",
                    ),
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#cbd5e1"),
                )
                st.plotly_chart(fig, width="stretch")

            with m2:
                st.subheader("Current conditions")
                show = view[["city", "conditions", "temperature_c",
                             "humidity_pct", "precip_mm", "wind_kmh", "gust_kmh"]]
                st.dataframe(
                    show.rename(columns={
                        "city": "City", "conditions": "Conditions",
                        "temperature_c": "Temp °C", "humidity_pct": "RH %",
                        "precip_mm": "Rain mm", "wind_kmh": "Wind", "gust_kmh": "Gust",
                    }),
                    hide_index=True, width="stretch", height=460,
                    column_config={
                        "City": st.column_config.TextColumn(width="small"),
                        "Conditions": st.column_config.TextColumn(width="small"),
                        "Temp °C": st.column_config.NumberColumn(format="%.1f", width="small"),
                        "RH %": st.column_config.NumberColumn(format="%d", width="small"),
                        "Rain mm": st.column_config.NumberColumn(format="%.2f", width="small"),
                        "Wind": st.column_config.NumberColumn(format="%.0f", width="small"),
                        "Gust": st.column_config.NumberColumn(format="%.0f", width="small"),
                    },
                )
                st.caption(
                    f"Observed {live_df['observed_utc'].iloc[0]} UTC · "
                    "Open-Meteo forecast API · display only."
                )

            # ---- history + live edge ------------------------------------
            st.subheader("Archive history with the live edge")
            hist = cached_weather_long()
            if not hist.empty:
                daily = (
                    hist[hist["country"].isin(countries)]
                    .set_index("time")
                    .groupby("city")
                    .resample("D")
                    .agg({"temperature_2m": "mean", "precipitation": "sum"})
                    .reset_index()
                )
                d1, d2 = st.columns(2)
                with d1:
                    fig = go.Figure()
                    for city, grp in daily.groupby("city"):
                        fig.add_trace(go.Scatter(
                            x=grp["time"], y=grp["temperature_2m"], name=city,
                            mode="lines", line=dict(width=1.6),
                        ))
                    if not view.empty:
                        fig.add_trace(go.Scatter(
                            x=[pd.Timestamp.now("UTC").tz_localize(None)],
                            y=[view["temperature_c"].mean()],
                            mode="markers", name="live edge",
                            marker=dict(size=13, color="#f97316",
                                        symbol="diamond",
                                        line=dict(width=1.5, color="#fff")),
                            hovertemplate="live edge · %{y:.1f} °C<extra></extra>",
                        ))
                    fig.update_layout(
                        height=340, margin=dict(l=45, r=10, t=34, b=0),
                        title="Daily mean temperature",
                        yaxis_title="°C", xaxis_title="",
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#cbd5e1", size=11), legend=dict(orientation="h"),
                    )
                    st.plotly_chart(fig, width="stretch")
                with d2:
                    fig = go.Figure()
                    for city, grp in daily.groupby("city"):
                        fig.add_trace(go.Bar(
                            x=grp["time"], y=grp["precipitation"], name=city,
                            opacity=0.75,
                        ))
                    fig.update_layout(
                        height=340, margin=dict(l=45, r=10, t=34, b=0),
                        title="Daily total precipitation",
                        yaxis_title="mm", xaxis_title="", barmode="group",
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#cbd5e1", size=11), showlegend=False,
                    )
                    st.plotly_chart(fig, width="stretch")

            if show_raw_detail and not hist.empty:
                st.subheader("Raw grid sample")
                sample = (
                    hist[hist["country"].isin(countries)]
                    .sort_values("time", ascending=False)
                    .head(200)
                )
                st.dataframe(
                    sample[["city", "time", "temperature_2m", "precipitation",
                             "wind_speed_10m", "weather_code"]],
                    hide_index=True, width="stretch", height=320,
                )

    # =====================================================================
    # Tab 3 — Outages (the label signal)
    # =====================================================================
    with tabs[2]:
        out = S.outage_health()
        if not out["files"]:
            st.info(
                "No outage label data found under `data/raw/outages/`. "
                "Populate it with the acquisition CLIs (for example "
                "`python -m src.ingestion.kplc --all` for Kenya, "
                "`python -m src.ingestion.nerc` for Nigeria, and the Eskom "
                "hourly record for South Africa) — the dashboard re-reads the "
                "workspace on every refresh and picks the files up as soon "
                "as they land."
            )
        else:
            st.subheader("Load-shedding history — the label signal")
            st.caption(
                f"{out['files']} raw file(s) · {out['bytes'] / 1e6:.0f} MB · "
                f"{', '.join(out['sqlite_files'] + out['json_files'])}"
            )

            eskom = cached_eskom()
            if not eskom.empty:
                active = eskom[eskom["mlr_mw"] > 0]
                episodes = cached_eskom_episodes()
                span = eskom["time"]
                kk = st.columns(5)
                kk[0].metric("Hours with load reduction", f"{len(active):,}")
                kk[1].metric(
                    "Share of hours",
                    f"{len(active) / len(eskom) * 100:.1f}%" if len(eskom) else "—",
                )
                kk[2].metric(
                    "Peak reduction",
                    f"{eskom['mlr_mw'].max():,.0f} MW" if len(eskom) else "—",
                )
                kk[3].metric("Load-shedding events", f"{len(episodes):,}")
                kk[4].metric(
                    "Coverage",
                    f"{span.min():%Y-%m} → {span.max():%Y-%m}",
                )

                o1, o2 = st.columns([1.5, 1])
                with o1:
                    daily = (
                        eskom.set_index("time")["mlr_mw"]
                        .resample("D").max().dropna()
                    )
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=daily.index, y=daily.values, mode="lines",
                        line=dict(width=0.6, color="#f97316"),
                        fill="tozeroy",
                        fillcolor="rgba(249,115,22,0.28)",
                        hovertemplate="%{x|%b %Y}<br>%{y:,.0f} MW<extra></extra>",
                        name="daily peak",
                    ))
                    fig.update_layout(
                        height=330, margin=dict(l=0, r=0, t=30, b=0),
                        title="Daily peak manual load reduction (MW)",
                        xaxis_title="", yaxis_title="MW",
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#cbd5e1", size=11),
                    )
                    st.plotly_chart(fig, width="stretch")
                with o2:
                    yearly = (
                        eskom[eskom["mlr_mw"] > 0].set_index("time")["mlr_mw"]
                        .resample("YE").agg(["count", "max"])
                    )
                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        x=yearly.index.year, y=yearly["count"],
                        marker_color="#fb923c", name="hours",
                        hovertemplate="%{x}<br>%{y:,} hours<extra></extra>",
                    ))
                    fig.update_layout(
                        height=330, margin=dict(l=0, r=0, t=30, b=0),
                        title="Load-shedding hours per year",
                        xaxis_title="", yaxis_title="hours",
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#cbd5e1", size=11), showlegend=False,
                    )
                    st.plotly_chart(fig, width="stretch")

                st.markdown("**Severest events**")
                if not episodes.empty:
                    top = episodes.head(12).copy()
                    top["start"] = top["start"].dt.strftime("%Y-%m-%d %H:%M")
                    top["end"] = top["end"].dt.strftime("%Y-%m-%d %H:%M")
                    top["peak_mw"] = top["peak_mw"].round(0)
                    top["mean_mw"] = top["mean_mw"].round(0)
                    st.dataframe(
                        top.rename(columns={
                            "start": "Start (UTC)", "end": "End (UTC)",
                            "hours": "Hours", "peak_mw": "Peak MW",
                            "mean_mw": "Mean MW",
                        }),
                        hide_index=True, width="stretch", height=300,
                    )
                st.caption(
                    "Eskom system-operations export. `Manual Load_Reduction` is the "
                    "load-shedding signal for South Africa — the label the model will "
                    "predict. Planned and unplanned interruptions must still be "
                    "separated downstream (Day 10)."
                )
            else:
                st.info("The Eskom export has not been parsed yet.")

    # =====================================================================
    # Tab 4 — Boundaries
    # =====================================================================
    with tabs[3]:
        tolerance = 0.05 if adm_level == "ADM2" else 0.01
        rings_by_country = cached_boundary_rings(adm_level, tuple(countries), tolerance)

        b1, b2 = st.columns([1.4, 1])
        with b1:
            st.subheader(f"{adm_level} coverage")
            if not rings_by_country:
                st.info(f"No {adm_level} boundary files for the selected countries.")
            else:
                palette = {"KEN": "#0ea5e9", "ZAF": "#22c55e", "NGA": "#f59e0b"}
                fig = go.Figure()
                for iso3, rings in rings_by_country.items():
                    xs, ys, labels = [], [], []
                    for name, coords in rings:
                        xs.extend([c[0] for c in coords] + [None])
                        ys.extend([c[1] for c in coords] + [None])
                        labels.extend([name] * (len(coords) + 1))
                    fig.add_trace(go.Scattergeo(
                        lon=xs, lat=ys, mode="lines",
                        name=iso3,
                        line=dict(width=1.1, color=palette.get(iso3, "#64748b")),
                        text=labels,
                        hovertemplate="%{text}<br>" + iso3 + " · " + adm_level
                                      + "<extra></extra>",
                        showlegend=True,
                    ))
                fig.add_trace(go.Scattergeo(
                    lon=[c["lon"] for c in S.PILOT_CITIES if c["country"] in countries],
                    lat=[c["lat"] for c in S.PILOT_CITIES if c["country"] in countries],
                    mode="markers", marker=dict(size=8, color="#f97316",
                                                line=dict(width=1.2, color="#fff")),
                    text=[c["city"] for c in S.PILOT_CITIES if c["country"] in countries],
                    textposition="top center", textfont=dict(size=9, color="#fbbf24"),
                    hovertemplate="%{text}<extra></extra>", showlegend=False,
                ))
                fig.update_layout(
                    height=520, margin=dict(l=0, r=0, t=10, b=0),
                    geo=dict(projection_type="equirectangular",
                             lataxis_range=[-37, 15], lonaxis_range=[3, 44],
                             showland=True, landcolor="rgba(30,41,59,0.65)",
                             showcountries=True,
                             countrycolor="rgba(100,116,139,0.5)",
                             coastlinecolor="rgba(100,116,139,0.7)",
                             bgcolor="rgba(0,0,0,0)"),
                    paper_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", y=1.02),
                )
                st.plotly_chart(fig, width="stretch")

        with b2:
            st.subheader("Layer facts")
            rows = [r for r in bounds if r["country"] in countries]
            if rows:
                st.dataframe(
                    pd.DataFrame(rows)[
                        ["country", "level", "canonical", "units", "year", "license"]
                    ].rename(columns={
                        "country": "ISO", "level": "Lvl", "canonical": "Units type",
                        "units": "N", "year": "Year", "license": "License",
                    }),
                    hide_index=True, width="stretch", height=320,
                )
                st.caption(
                    "Licences must be attributed in the README (Day 29). "
                    "The geoBoundaries gbOpen package is CC BY 4.0."
                )
            else:
                st.info("No boundary files for the selected countries.")

            st.subheader("Data health")
            if health.get("cities"):
                ok_all = all(c.get("ok") for c in health["cities"].values())
                align = health.get("alignment_ok")
                st.markdown(
                    f'<div class="gr-card"><div class="gr-kv">Weather grid</div>'
                    f'<div class="gr-val">{"PASS" if ok_all else "ATTENTION"}</div>'
                    f'<div class="gr-note">{len(health["cities"])} cities · '
                    f'cross-city alignment: {"yes" if align else "no"} · '
                    f'window {health.get("window", ("?", "?"))[0]} → '
                    f'{health.get("window", ("?", "?"))[1]}</div></div>',
                    unsafe_allow_html=True,
                )
                st.dataframe(
                    pd.DataFrame([
                        {
                            "City": stem,
                            "Rows": c.get("rows"),
                            "Gaps": c.get("gaps"),
                            "Duplicates": c.get("duplicates"),
                            "Nulls": c.get("nulls"),
                            "Grid OK": c.get("grid_ok"),
                        }
                        for stem, c in health["cities"].items()
                    ]),
                    hide_index=True, width="stretch", height=240,
                )
            else:
                st.info("No weather downloads yet.")

    # =====================================================================
    # Tab 5 — Provenance
    # =====================================================================
    with tabs[4]:
        p1, p2 = st.columns([1.25, 1])
        with p1:
            st.subheader("Acquired files")
            catalog = S.source_catalog()
            if not catalog.empty:
                view = catalog.copy()
                view["MB"] = (view["bytes"] / 1e6).round(2)
                st.dataframe(
                    view[["file", "kind", "MB", "retrieved_utc", "license"]],
                    hide_index=True, width="stretch", height=420,
                )
                st.caption(
                    "Raw responses are stored byte-identical to the source and never "
                    "edited. Weather files carry a SHA-256 in "
                    "data/raw/weather/_manifest.json."
                )
            else:
                st.info("No raw files yet.")

        with p2:
            st.subheader("Dataset register")
            entries = S.inventory_entries()
            if entries:
                df = pd.DataFrame(entries)
                df = df[["id", "status", "source", "section"]]
                st.dataframe(
                    df.rename(columns={
                        "id": "ID", "status": "Status", "source": "Source",
                        "section": "Section",
                    }),
                    hide_index=True, width="stretch", height=420,
                    column_config={
                        "ID": st.column_config.TextColumn(width="small"),
                        "Status": st.column_config.TextColumn(width="small"),
                        "Source": st.column_config.TextColumn(width="medium"),
                        "Section": st.column_config.TextColumn(width="medium"),
                    },
                )
                st.caption(
                    "Statuses are the declared ones from reports/data_inventory.md. "
                    "An entry becomes *acquired* only after its download lands and "
                    "passes validation."
                )
            else:
                st.info("No inventory found.")


# ---- live wrapper -------------------------------------------------------
if refresh_seconds:
    st.fragment(render_pulse, run_every=refresh_seconds)()
else:
    render_pulse()
