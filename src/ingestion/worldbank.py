"""World Bank outage indicators (Day 5).

Two roles in this project:
  * outage indicators (IC.FRM.INFRA.*, IC.ELC.OUTG.*, ...) are **country-year**
    survey aggregates — useful as national context / qualitative cross-checks,
    never as geolocated labels (see reports/day05_worldbank_coverage.md);
  * access-to-electricity indicators (EG.ELC.ACCS.*) are background context.

Source: World Bank API v2 (https://api.worldbank.org/v2), CC BY 4.0 terms for
the underlying indicator data (per World Bank dataset terms of use).

Layout: data/raw/worldbank/{indicator}.json — verbatim API responses, plus
_manifest.json with URL, params and retrieval time.

Usage:
    python -m src.ingestion.worldbank            # download + coverage report
    python -m src.ingestion.worldbank --audit     # rebuild report only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from src.config import DATA_RAW, REPORTS_DIR, ensure_dirs

API_BASE = "https://api.worldbank.org/v2"

# Indicators that measure outages directly (survey / regulatory aggregates).
OUTAGE_INDICATORS = {
    "IC.FRM.INFRA.IN16": "Percent of firms experiencing electrical outages (Enterprise Surveys)",
    "IC.FRM.INFRA.IN2": "Number of electrical outages in a typical month (Enterprise Surveys)",
    "IC.FRM.INFRA.IN3_C": "Average duration of a typical outage, hours (Enterprise Surveys)",
    "IC.FRM.INFRA.IN4_C": "Average losses due to outages, % of annual sales (Enterprise Surveys)",
    "IC.FRM.OUTG.ZS": "Value lost due to electrical outages, % of sales (WDI)",
    "IC.ELC.OUTG.ZS": "Firms experiencing electrical outages, % of firms (WDI)",
    "IC.ELC.OUTG": "Power outages in firms in a typical month, number (WDI archives)",
    "IC.ELC.OUTG.DY": "Electrical outages, days (WDI archives)",
    "IC.ELC.OUTG.HR": "Average duration of power outages, hours (Africa Dev. Indicators)",
    "IC.ELC.SAID.XD.DB1619": "SAIDI - getting electricity (Doing Business 16-20)",
}

# Background context (not outage labels).
CONTEXT_INDICATORS = {
    "EG.ELC.ACCS.ZS": "Access to electricity, % of population",
    "EG.ELC.ACCS.RU.ZS": "Access to electricity, rural, % of rural population",
    "EG.ELC.ACCS.UR.ZS": "Access to electricity, urban, % of urban population",
    "EG.USE.ELEC.KH.PC": "Electric power consumption, kWh per capita",
}

COUNTRIES = ("KEN", "ZAF", "NGA")
DATE_RANGE = "2000:2026"

WB_DIR = DATA_RAW / "worldbank"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def download_indicator(indicator: str, countries: tuple[str, ...] = COUNTRIES,
                       date_range: str = DATE_RANGE, overwrite: bool = False) -> Path:
    """Fetch one indicator for the pilot countries; response bytes kept verbatim."""
    ensure_dirs()
    WB_DIR.mkdir(parents=True, exist_ok=True)
    path = WB_DIR / f"{indicator}.json"

    if path.exists() and not overwrite:
        print(f"  [skip] {indicator}.json exists")
        return path

    url = f"{API_BASE}/country/{';'.join(countries)}/indicator/{indicator}"
    params = {"format": "json", "per_page": 1000, "date": date_range}
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()

    # Store the response verbatim even when it carries no data rows — the
    # absence of an indicator for the pilots is itself a coverage finding.
    path.write_bytes(resp.content)
    _record_manifest(path, resp, params, indicator)

    payload = resp.json()
    rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else None
    if not rows:
        message = payload[0].get("message", "no payload") if isinstance(payload, list) and payload else "empty"
        print(f"  [none] {indicator}: no data rows ({message})")
        return path
    print(f"  [get ] {indicator}: {len(rows)} country-year rows")
    time.sleep(0.5)  # be polite to the public API
    return path


def _record_manifest(path: Path, resp: requests.Response, params: dict, indicator: str) -> None:
    manifest_path = WB_DIR / "_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest[path.name] = {
        "indicator": indicator,
        "requested_url": resp.url.split("?")[0],
        "requested_params": {k: str(v) for k, v in params.items()},
        "retrieved_utc": _utc_now(),
        "http_status": resp.status_code,
        "bytes": len(resp.content),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def load_indicator(indicator: str) -> pd.DataFrame:
    """Load one raw response into a tidy DataFrame.

    Returns columns: country (ISO3), country_name, year, value, indicator.
    Null values are kept (coverage analysis counts real observations).
    """
    path = WB_DIR / f"{indicator}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else None
    if not rows:
        return pd.DataFrame(columns=["country", "country_name", "year", "value", "indicator"])

    df = pd.DataFrame(rows)
    # 'country' arrives as {"id": ..., "value": ...} objects; normalise to columns.
    raw_country = df["country"]
    if len(raw_country) and isinstance(raw_country.iloc[0], dict):
        country_id = raw_country.map(lambda d: d.get("id"))
        country_name = raw_country.map(lambda d: d.get("value"))
    else:  # defensive: flat string column
        country_id = raw_country
        country_name = raw_country

    iso3 = df["countryiso3code"] if "countryiso3code" in df else pd.Series([None] * len(df))
    out = pd.DataFrame({
        "country": iso3.replace("", pd.NA).fillna(country_id),
        "country_name": country_name,
        "year": df["date"].astype(int),
        "value": pd.to_numeric(df["value"], errors="coerce"),
        "indicator": indicator,
    })
    return out.sort_values(["country", "year"]).reset_index(drop=True)


def coverage_table(indicators: dict[str, str], countries: tuple[str, ...] = COUNTRIES) -> pd.DataFrame:
    """One row per indicator x country: observation years and last observation."""
    rows = []
    for ind in indicators:
        path = WB_DIR / f"{ind}.json"
        if not path.exists():
            rows.extend(
                {"indicator": ind, "label": indicators[ind], "country": c,
                 "years": None, "n_obs": 0, "first": None, "last": None}
                for c in countries
            )
            continue
        df = load_indicator(ind)
        for c in countries:
            sub = df[(df["country"] == c) & df["value"].notna()]
            years = sorted(sub["year"].unique().tolist())
            rows.append({
                "indicator": ind,
                "label": indicators[ind],
                "country": c,
                "n_obs": len(years),
                "first": years[0] if years else None,
                "last": years[-1] if years else None,
                "years": ",".join(str(y) for y in years),
            })
    return pd.DataFrame(rows)


def write_audit(out_path: Path | None = None) -> Path:
    """Write reports/day05_worldbank_coverage.md."""
    out_path = out_path or (REPORTS_DIR / "day05_worldbank_coverage.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    outage_cov = coverage_table(OUTAGE_INDICATORS)
    ctx_cov = coverage_table(CONTEXT_INDICATORS)

    def _tbl(df: pd.DataFrame) -> str:
        show = df.drop(columns=["label"], errors="ignore")
        return show.to_markdown(index=False) if len(show) else "_no data_"

    # Which outage indicators have at least one pilot-country observation?
    usable = outage_cov[outage_cov["n_obs"] > 0]
    usable_inds = sorted(usable["indicator"].unique().tolist())
    dead_inds = sorted(set(OUTAGE_INDICATORS) - set(usable_inds))

    # Latest observation per country across outage indicators
    latest = (
        outage_cov[outage_cov["n_obs"] > 0]
        .sort_values("last", ascending=False)
        .groupby("country")
        .head(3)[["country", "indicator", "n_obs", "first", "last"]]
    )

    text = f"""# Day 5 audit — World Bank outage indicators

Generated by `python -m src.ingestion.worldbank --audit` on {_utc_now()}.

**Source:** World Bank API v2 (`api.worldbank.org`), responses stored verbatim in
`data/raw/worldbank/`. Pilot countries: {", ".join(COUNTRIES)}.

## Outage indicators — geographic coverage

{_tbl(outage_cov)}

## Context indicators (electricity access)

{_tbl(ctx_cov)}

## Findings

- **{len(usable_inds)}/{len(OUTAGE_INDICATORS)} outage indicators carry pilot-country data:**
  {", ".join(usable_inds) or "none"}.
- **No pilot-country data:** {", ".join(dead_inds) or "none"}.
- Latest usable observations per country:

{latest.to_markdown(index=False) if len(latest) else "_none_"}

- **Granularity is country-year, not locality.** Enterprise Surveys aggregates firms within
  a country for a survey year; there are no coordinates and no monthly resolution. These
  indicators can therefore only serve as *national context features* and as a qualitative
  cross-check on modelled risk (they must never become labels — Day 15/17).
- Survey years are sparse (a handful per country, see the `years` columns) — they will not
  align with the hourly/daily modelling grid and should be attached as static
  country-level columns, not joined on time.
- Doing Business SAIDI (`IC.ELC.SAID.XD.DB1619`) is a *regulator-reported* reliability
  metric where available — useful for country ranking context, but its methodology vintage
  ended with the Doing Business programme (noted in the source register, Day 6).
"""
    out_path.write_text(text, encoding="utf-8")
    print(f"Coverage report written to {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", help="rebuild report from existing downloads")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    if not args.audit:
        all_indicators = {**OUTAGE_INDICATORS, **CONTEXT_INDICATORS}
        failed = []
        for indicator in all_indicators:
            try:
                download_indicator(indicator, overwrite=args.overwrite)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                print(f"  [FAIL] {indicator}: {exc}", file=sys.stderr)
                failed.append(indicator)
        if len(failed) == len(all_indicators):
            print("Every download failed - network problem?", file=sys.stderr)
            return 1
        if failed:
            print(f"{len(failed)} indicator(s) failed to download: {failed}", file=sys.stderr)

    write_audit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
