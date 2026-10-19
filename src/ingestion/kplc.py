"""Kenya Power (KPLC) planned power-interruption notices (Day 10).

Probe findings (reports/day10_kenya_probes.md):

* The advertised page https://kplc.co.ke/power-interruptions returns HTTP 200 but
  renders **no notice list** — its only Livewire component is the site-wide header
  search (``sections.search``).
* The real list is the ``sections.powerschedule`` Livewire section rendered at
  https://kplc.co.ke/customer-support#powerschedule ("Power Maintenance Schedule").
  The list is **server-rendered** and paginated with ``?page=N`` (the component's
  ``wire:effects`` pushes ``paginators.page`` to the URL), so plain GETs are enough —
  no ``POST /livewire/update`` needed. As a fallback the Livewire endpoint accepts
  ``calls: [{"method": "gotoPage", "params": [N, "page"]}]`` (verified, HTTP 200).
* 8 notices per page; page 1..14 populated (110 unique notices on 2026-10-19,
  back to 2024-06-27); pages >= 15 render empty.
* Each row: title (e.g. "Power Maintenance Notice - 08.10.2026"), posted timestamp
  ("Oct 07, 2026 14:06 pm" — a 24h clock with a spurious am/pm suffix), and a PDF
  under https://kplc.co.ke/storage/*.pdf.
* Notice PDFs are text-extractable with pypdf (861 and 17,476 chars on two 2026
  samples; the Day-4 sample 01K097...pdf is image-only with 0 chars). tesseract is
  NOT installed in this environment, so image-only PDFs cannot be OCR'd locally.

⚠️ These are **PLANNED** outages (maintenance schedules). They must never be used as
labels for unplanned/outage events.

Layout:

    data/raw/outages/kenya/
        kplc_customer_support_p{N}.html   verbatim page responses
        kplc_powerschedule_notices.json   full crawl of all pages (110 notices)

Usage:
    python -m src.ingestion.kplc --limit 16
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.config import DATA_RAW

BASE_URL = "https://kplc.co.ke/customer-support"
KENYA_RAW_DIR = DATA_RAW / "outages" / "kenya"

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) grid-risk-research"

# One card = posted timestamp div + title div + <a href="...pdf">. The title also
# appears a second time inside the anchor; we key the card on the first occurrence.
_CARD_RE = re.compile(
    r'class="text-sm font-sans font-semibold[^"]*">\s*(?P<posted>[^<]+?)\s*</div>\s*'
    r'<div class="text-sm font-sans font-normal[^"]*text-center">\s*(?P<title>[^<]+?)\s*</div>\s*'
    r'<a href="(?P<url>[^"]+\.pdf)"',
    re.S,
)
_MAX_PAGE_RE = re.compile(r"gotoPage\((\d+), 'page'\)")


def _get(url: str, tries: int = 4) -> requests.Response:
    """GET with backoff; the site has dropped connections on rapid bursts."""
    delay = 3.0
    last_exc: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, timeout=60, headers={"User-Agent": _USER_AGENT})
            if resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == tries:
                break
            print(f"  [warn] attempt {attempt} failed ({exc}); retrying in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise RuntimeError(f"request failed after {tries} attempts: {last_exc}")


def parse_posted(posted: str) -> str | None:
    """``'Oct 16, 2026 16:05 pm'`` -> ``'2026-10-16T16:05:00'`` (KPLC's clock is
    already 24h; the am/pm suffix is decorative and stripped)."""
    cleaned = re.sub(r"\s*(am|pm)\s*$", "", posted.strip(), flags=re.I)
    for fmt in ("%b %d, %Y %H:%M", "%b %d, %Y %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).isoformat()
        except ValueError:
            continue
    return None


def parse_notices(page_html: str) -> list[dict]:
    """Extract notice rows from a server-rendered customer-support page.

    Returns dicts: ``title``, ``posted``, ``posted_iso``, ``pdf_url`` — newest first
    (KPLC renders the schedule in descending posted order).
    """
    notices, seen = [], set()
    for m in _CARD_RE.finditer(page_html):
        url = html_lib.unescape(m.group("url"))
        if url in seen:
            continue
        seen.add(url)
        posted = html_lib.unescape(re.sub(r"\s+", " ", m.group("posted"))).strip()
        title = html_lib.unescape(re.sub(r"\s+", " ", m.group("title"))).strip()
        notices.append({
            "title": title,
            "posted": posted,
            "posted_iso": parse_posted(posted),
            "pdf_url": url,
        })
    return notices


def parse_last_page(page_html: str) -> int:
    """Highest page number offered by the Livewire pagination bar (1 if absent)."""
    pages = [int(x) for x in _MAX_PAGE_RE.findall(page_html)]
    return max(pages, default=1)


def _fetch_page(page: int) -> str:
    """Fetch one pagination page and store the raw HTML verbatim."""
    KENYA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    resp = _get(f"{BASE_URL}?page={page}")
    (KENYA_RAW_DIR / f"kplc_customer_support_p{page}.html").write_text(
        resp.text, encoding="utf-8")
    print(f"  [get ] customer-support?page={page}: {len(resp.text):,} bytes")
    return resp.text


def fetch_notices(limit: int = 8) -> list[dict]:
    """Fetch up to ``limit`` planned-outage notices, newest first.

    Walks ``/customer-support?page=N`` until the limit is met, the pagination bar
    runs out, or a page comes back empty. Raw page HTML is saved under
    ``data/raw/outages/kenya/``; each returned dict gains a ``page`` key.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    out: list[dict] = []
    seen: set[str] = set()
    page = 1
    while len(out) < limit:
        body = _fetch_page(page)
        rows = parse_notices(body)
        if not rows:
            break
        for row in rows:
            if row["pdf_url"] in seen:
                continue
            seen.add(row["pdf_url"])
            out.append({**row, "page": page})
            if len(out) >= limit:
                break
        if page >= parse_last_page(body):
            break
        page += 1
    return out[:limit]


def fetch_all() -> list[dict]:
    """Fetch every page (14 pages / 110 notices as of 2026-10-19)."""
    notices = fetch_notices(limit=10_000)
    KENYA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": BASE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notice_count": len(notices),
        "notices": notices,
    }
    out = KENYA_RAW_DIR / "kplc_powerschedule_notices.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [save] {out.name}: {len(notices)} notices")
    return notices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=8,
                        help="number of notices to fetch (default 8 = one page)")
    parser.add_argument("--all", action="store_true",
                        help="fetch every page and refresh the JSON extract")
    args = parser.parse_args(argv)

    notices = fetch_all() if args.all else fetch_notices(limit=args.limit)
    print(f"{len(notices)} planned-outage notices "
          f"({notices[0]['posted'] if notices else 'n/a'} .. "
          f"{notices[-1]['posted'] if notices else 'n/a'})")
    for n in notices[:10]:
        print(f"  {n['posted']} | {n['title']} | {n['pdf_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
