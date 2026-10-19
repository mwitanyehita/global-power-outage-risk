# Day 10 — Kenya (KPLC): machine-readable path probes & sample acquisition

All probes run **2026-10-19** from this project's environment. Raw evidence:
`data/raw/outages/kenya/`. Tesseract availability: **`which tesseract` → not found
(exit 1)** — no local OCR.

---

## 1. Decision table

| # | Path | Machine-readable? | History depth (as of 2026-10-19) | Stability | Effort | Verdict |
|---|---|---|---|---|---|---|
| A1 | **KPLC official `GET /customer-support?page=N`** (server-rendered `sections.powerschedule` list) | ✅ plain HTML → `{title, posted, pdf_url}`, 8 rows/page | **2024-06-27 → now** (110 notices, 14 pages) | High — first-party, `robots.txt` `Allow: /`, no tokens/cookies needed | Low (regex over stable card markup) | ✅ **RECOMMENDED — primary** |
| A2 | KPLC official `POST /livewire/update` (`sections.powerschedule`, `gotoPage`) | ✅ JSON envelope + rendered HTML in `effects.html` | same as A1 | Medium — needs session cookie + CSRF meta + valid snapshot checksum (checksums rotate) | Medium | ⚠️ verified fallback only |
| A3 | `https://kplc.co.ke/power-interruptions` (the page Day 4 probed) | ⛔ renders **no notice list at all** — only the site-wide header search component (`sections.search`) | n/a | n/a | n/a | ⛔ dead end (Day-4 assumption corrected) |
| B | **kplc.project.co.ke** (third-party re-scraper) | ✅ clean semantic HTML (`.region-group` / `article.area`: region, area, sub-locations, hours) + **sitemap index of every day-page** | **2023-07-04 → 2026-10-24** (608 day-pages; uneven: 2023:33, 2024:262, 2025:100, 2026:213) | Medium — unofficial one-person site, dropped connections on rapid requests, could disappear | Low | ⚠️ **SECONDARY — area-level enrichment + pre-2024-06 backfill** |
| C | KPLC notice PDFs (`/storage/*.pdf`) via pypdf | ⚠️ text on 2026 samples (861 / 17,476 chars); Day-4 sample image-only (0 chars) | follows A1 | pypdf-only; **no OCR fallback (tesseract absent)** | Medium | ⚠️ best source of region/county/area detail for A1 rows |

**Recommended acquisition:** A1 via `src/ingestion/kplc.py::fetch_notices(limit)` as the
canonical notice list (official, robots-clean, first-party); download each notice PDF and
extract text with pypdf for region/area/time detail (C, skipping image-only docs
opportunistically); use B only to (i) enrich area-level structure without OCR and (ii)
backfill the 2023-07 → 2024-06 gap, citing it explicitly as third-party.

---

## 2. Evidence — KPLC official (paths A1–A3)

### A3 first: `/power-interruptions` is empty
- `GET https://kplc.co.ke/power-interruptions` → **HTTP 200, 237,197 bytes**
  (`kplc_power_interruptions.html`). `robots.txt` → 200, `User-agent: * / Allow: /`,
  sitemap declared (`kplc_robots.txt`).
- The page contains exactly **one** `wire:id` — the header search component
  `sections.search` (path `power-interruptions`). Between `</header>` and the footer
  there is **no content, no `<h1>`, no notice rows**; the only notice-looking PDF href
  is "Suppliers Information". So the notice list Day 4 thought it saw here does not
  exist on this page.

### Livewire probe on `sections.search` (documented as required)
- Snapshot (decoded, `kplc_snapshot.json`): `data = {search, loading, results}`;
  `memo = {id, name: "sections.search", path: "power-interruptions", method: GET}`;
  checksum present. Methods are not listed in Livewire v3 snapshots — recovered from
  blade attributes: `wire:submit.prevent="searchsite"`, `wire:model.defer="search"`
  (plus `wire:effects="[]"` → no listeners/URL bindings).
- POST attempts to `https://kplc.co.ke/livewire/update` **without CSRF → HTTP 419**
  `{"message": "CSRF token mismatch."}` (3 payload variants: updates+call, call-only,
  params-as-list — all 419; bodies in `livewire_response_{1,2,3}_*.txt`).
- With `<meta name="csrf-token">` + `XSRF-TOKEN` cookie (headers `X-CSRF-TOKEN`,
  `X-XSRF-TOKEN`, `X-Livewire`) and payload
  `{"components":[{"snapshot":…, "updates":{"search":"Nairobi"}, "calls":[{"path":"","method":"searchsite","params":{}}]}]}` →
  **HTTP 200, 56,989 bytes JSON** (`livewire_response_csrf_updates_searchsite.txt`).
- Response: `effects.returns = [null]`, `effects.html` = 53,120 bytes of *site-wide
  search results* (news articles, tenders, FAQs — `App\Models\Content\Article`
  collections). **No outage notices.** This component is a header search, not the
  schedule.

### A1: the real list — `sections.powerschedule` at `/customer-support#powerschedule`
- `GET https://kplc.co.ke/customer-support` → **HTTP 200, ~634 KB**
  (`kplc_customer_support.html`). Contains the child Livewire section
  `sections.powerschedule` (anchor `id="powerschedule"`, "Power Maintenance Schedule")
  with `data = {section: Sections#27, sectiondata: PowerMaintenance#1,
  paginators: [{page: 1}]}` and `wire:effects` → URL binding
  `paginators.page {as: "page", use: "push"}`.
- Because the binding pushes the page number to the URL, **plain GET pagination works**:
  `GET /customer-support?page=2` → 200, distinct 8 notices (Aug 2026 → Jul 2026).
- Markup methods (from `wire:click`): `gotoPage(N, 'page')`, `nextPage('page')`.
- **Crawl (plain GET, `kplc_crawl`)**: pages 1–14 populated (8/page, page 14 = 6),
  pages 15+ → 200 but 0 notices (pagination bound reached). Dedupe by PDF URL:
  **110 unique notices, newest posted Oct 16 2026, oldest posted Jun 27 2024**
  → archive depth **≈ 28 months**. Saved as `kplc_powerschedule_notices.json`.
- **Livewire fallback verified**: POST with the `sections.powerschedule` snapshot,
  `calls:[{method:"gotoPage", params:[10,"page"]}]` → **HTTP 200, 34,940 bytes**;
  returned snapshot `paginators.page = 10`, `effects.html` renders 8 notices
  (Apr 10 2025 → May 29 2025). Saved: `livewire_powerschedule_goto10.txt` (raw JSON),
  `livewire_powerschedule_goto10.html`, `livewire_notices.json` (the parsed rows).
- Row fields: `title` ("Special Power Supply Interruption Notice - 16.10.2026",
  "Power Maintenance Notice - 08.10.2026" …), `posted` ("Oct 07, 2026 14:06 pm" — a 24h
  clock with a decorative am/pm suffix; parsed to ISO in the module), `pdf_url`
  (`https://kplc.co.ke/storage/01M….pdf`).

### Sample notice PDFs acquired (path C)
| File | Bytes | Pages | pypdf chars |
|---|---|---|---|
| `kplc_notice_2026-10-16.pdf` (01M52D3…) | 114,635 | 1 | **861** ✅ text |
| `kplc_notice_2026-10-01.pdf` (01M3XNA…) | 363,214 | 2 | **17,476** ✅ text |
| `kplc_notice_day4_sample_01K097….pdf` | 482,887 | 1 | **0** ⛔ image-only |

- `which tesseract` → **not found (exit 1)**; image-only PDFs cannot be OCR'd in this
  environment. Note the Day-4 "sample notice" is actually a *Suppliers Information*
  document, not an interruption notice — its 0-char result was partly a mislabel.
- The 2026 notices carry a regular text structure: `WESTERN REGION` / `KISUMU COUNTY`
  / `AREA: …` / `DATE: Friday 16.10.2026` / `TIME: 11.00 A.M. – 6.00 P.M.` + the
  affected-place list → region/area detail is extractable from text PDFs with pypdf.

---

## 3. Evidence — kplc.project.co.ke (path B)

- `GET /power-interruptions` → **200, 96,276 bytes** (`project_co_ke_home.html`):
  an *upcoming-days* index (`/power-interruptions/2026-10-19` … `2026-10-24`) plus
  `/power-interruptions/area/{slug}` links. It does **not** list past notices inline —
  history is browsed by date URL.
- **URL patterns:**
  - `/power-interruptions` — upcoming index
  - `/power-interruptions/{YYYY-MM-DD}` — day page (404 for days without notices,
    e.g. `2024-01-01` → 404)
  - `/power-interruptions/regions`, `/power-interruptions/region/{slug}` (8 regions)
  - `/power-interruptions/area/{slug}` (2,682), `/power-interruptions/location/{slug}` (20,605)
- **Archive depth via `sitemap.xml`** → **HTTP 200, 4,287,645 bytes, 23,906 URLs**
  (`project_co_ke_sitemap.xml`): **608 day-pages, earliest `2023-07-04`, latest
  `2026-10-24`** (by year: 2023: 33, 2024: 262, 2025: 100, 2026: 213) → depth
  **≈ 39 months**, but coverage is uneven (thin in 2023 and 2025 — scraping gaps).
- **Structured fields (HTML parse):** each day-page has `.region-group` →
  `<h3><a>` = region (+region URL), `article.area` → `<h4><a>` = area name + area URL,
  `span.time` = outage hours (optional), `p.locs` = comma-separated sub-locations.
- 12 date pages sampled (3 earliest + 6 random + 3 latest) — **all HTTP 200, all parsed**;
  74 area entries → `project_co_ke_notices.json` (`{title, url, date, region, areas,
  hours, region_url}` per entry). Raw HTML saved for each sampled day.
- **robots.txt** → 200 (`project_co_ke_robots.txt`): disallows only `/account`,
  `/confirm-email`, `/validate-email`, `/register-email`, `/add-keyword`, `/remove-keyword`;
  declares the sitemap. **Notice pages are crawlable** under robots.
- ⚠️ The server **dropped the connection** on rapid consecutive requests
  (`RemoteDisconnected` on request #2) — needs retries and ~2 s pacing (handled in the
  probe script; keep any future crawl equally gentle).

---

## 4. Module created

✅ **`src/ingestion/kplc.py`** (path A1 proven working):
- `fetch_notices(limit) -> list[dict]` — walks `/customer-support?page=N`, parses
  `{title, posted, posted_iso, pdf_url, page}` newest-first, stops at the pagination
  bound or an empty page, dedupes by PDF URL, and saves each raw page to
  `data/raw/outages/kenya/kplc_customer_support_p{N}.html`.
- `fetch_all()` refreshes `kplc_powerschedule_notices.json`.
- Live smoke test: `python -m src.ingestion.kplc --limit 3` → 3 notices (Oct 07–16, 2026).

✅ **`tests/test_kplc.py`** — 10 tests over the saved raw fixtures (parse, ordering,
posted-timestamp quirks, pagination bound, empty-page termination, limit/dedupe with the
network mocked, archive-JSON consistency; fixtures skip if absent):
`.venv/bin/python -m pytest tests/test_kplc.py -q` → **10 passed**.

---

## 5. Files written

| File | Contents |
|---|---|
| `data/raw/outages/kenya/kplc_power_interruptions.html` | official page (no notice list) |
| `data/raw/outages/kenya/kplc_customer_support.html`, `…_p1/p14/p15.html` | official schedule pages (raw) |
| `data/raw/outages/kenya/kplc_powerschedule_notices.json` | full official crawl: 110 notices, 2024-06-27 → 2026-10-16 |
| `data/raw/outages/kenya/kplc_snapshot.json` | decoded `sections.search` Livewire snapshot |
| `data/raw/outages/kenya/livewire_response_{1,2,3}_*.txt` | HTTP 419 CSRF failures (3 payload variants) |
| `data/raw/outages/kenya/livewire_response_csrf_updates_searchsite.txt` | HTTP 200 site-wide search response |
| `data/raw/outages/kenya/livewire_effects_html.html` | HTML effect of the search response |
| `data/raw/outages/kenya/livewire_powerschedule_goto10.{txt,html}` | HTTP 200 `gotoPage(10)` response |
| `data/raw/outages/kenya/livewire_notices.json` | 8 notice rows extracted from the Livewire response |
| `data/raw/outages/kenya/kplc_notice_2026-10-16.pdf`, `kplc_notice_2026-10-01.pdf` | real notice samples (text-extractable) |
| `data/raw/outages/kenya/kplc_notice_day4_sample_01K097.pdf` | Day-4 image-only sample (0 chars) |
| `data/raw/outages/kenya/kplc_robots.txt` | official robots (Allow: /) |
| `data/raw/outages/kenya/project_co_ke_home.html` | third-party index page |
| `data/raw/outages/kenya/project_co_ke_date_YYYY-MM-DD.html` (12 files) | sampled day pages |
| `data/raw/outages/kenya/project_co_ke_sitemap.xml` | 23,906 URLs / 608 day pages (4.3 MB) |
| `data/raw/outages/kenya/project_co_ke_robots.txt` | third-party robots |
| `data/raw/outages/kenya/project_co_ke_notices.json` | 74 parsed area entries from 12 sampled days |
| `src/ingestion/kplc.py`, `tests/test_kplc.py` | module + tests |
| `reports/day10_kenya_probes.md` | this report |

---

## 6. Caveats

1. **Planned ≠ unplanned.** Everything acquired here is KPLC's **planned**
   interruption/maintenance schedule (published in advance). These records must **never**
   be used as labels for unplanned outage events — per Day 15's label strategy they are
   coverage/geography material for the planned-vs-unplanned split only. The Day-16
   "Special Power Supply Interruption Notice" is an unplanned-event *announcement*, but
   it arrives after the fact and its selection process is unknown; treat it as narrative
   evidence, not as a sampled label.
2. **Third-party ToS/robots (path B).** `kplc.project.co.ke` is explicitly
   "not affiliated with Kenya Power". Its `robots.txt` permits the notice pages and the
   site invites indexing, but its Terms may still restrict bulk reuse; it also
   dropped connections on fast requests. Crawl gently (≥2 s), cite it as a third-party
   re-scraper, and cross-check its rows against official PDFs before analysis. Do not
   republish its data as if official.
3. **Official page structure is fragile.** `/power-interruptions` is (currently) an
   empty shell; the actual list lives at `/customer-support#powerschedule`. KPLC can
   move or restructure this at any time — the parser keys on stable card classes but the
   fixture tests exist precisely to detect drift early.
4. **Livewire checksums rotate.** Path A2 needs a fresh snapshot per session; it is a
   fallback, not the primary. Path A1 (plain GET) has no such state.
5. **PDF text quality varies.** Two 2026 notices extracted cleanly (861 / 17,476 chars),
   but the Day-4 sample yields 0 chars and **tesseract is not installed**, so image-only
   notices cannot be OCR'd in this environment — such notices would have to be skipped
   or OCRed elsewhere (e.g. Colab), and this limitation belongs in the Day 27 provenance
   notes.
6. **History asymmetry.** Official KPLC list depth is ~28 months (2024-06 onward);
   project.co.ke reaches 2023-07 but with uneven yearly coverage. Neither supports a
   deep multi-year planned-outage history — consistent with the Day 15 decision that
   Kenya is *not* a modeling target.
