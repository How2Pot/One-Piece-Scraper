#!/usr/bin/env python3
"""
TEMPORARY / ONE-TIME script: downloads card images ONLY, with zero calls
to tcgapi.dev. Use this to verify image downloading actually works
(Bandai's site may or may not block server-side requests the way it
blocks direct browser hotlinking - this has not been confirmed from a
real GitHub Actions run yet) before merging that logic into the main
scrape_cards.py and spending tcgapi.dev quota on the same run.

Once you've confirmed this works (check data/images/ in your repo for
new .png files after running), switch back to the main scrape_cards.py,
which has the same image-download step PLUS price fetching combined,
and skips any image already present here.

Run manually via workflow_dispatch using image_refresh.yml - does NOT
run on the daily schedule, so it never competes with the regular A/B
price-refresh cron.
"""

import json
import os
import re
import sys
import time
import urllib.request

# ---- Config (mirrors scrape_cards.py) -----------------------------------

OFFICIAL_BASE = "https://en.onepiece-cardgame.com"
CARDLIST_URL = f"{OFFICIAL_BASE}/cardlist/"

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cards.json")
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "images")
IMAGES_PUBLIC_PREFIX = "data/images"

REQUEST_DELAY = 0.8
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

DEBUG_DUMP_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_debug_raw_page.html")

# For this one-time test run, only do ONE small set first (OP-01) so we
# get a fast, cheap signal on whether downloading works at all, before
# committing to downloading the entire catalog's worth of images.
TEST_SETS = ["OP-01"]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"    [fetch] {url} -> HTTP {resp.status}, {len(body)} bytes", file=sys.stderr)
        return body


CARD_BLOCK = re.compile(
    r'<dl class="modalCol" id="([^"]+)">.*?'
    r'<div class="infoCol">\s*'
    r'<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*'
    r'.*?<div class="cardName">([^<]+)</div>',
    re.DOTALL,
)


def make_img_pattern(unique_id: str):
    """Match using the unique variant id (e.g. 'OP01-001_p1'), not the
    shared card_number - otherwise base and parallel cards collide since
    'OP01-001' is a substring of 'OP01-001_p1'."""
    escaped = re.escape(unique_id)
    return re.compile(rf'data-src="([^"]*?/card/{escaped}\.png[^"]*)"')


def local_image_path(unique_id: str) -> str:
    safe_id = re.sub(r'[^A-Za-z0-9_\-]', '_', unique_id)
    return os.path.join(IMAGES_DIR, f"{safe_id}.png")


def download_image(unique_id: str, remote_url: str) -> tuple:
    """Returns (public_path, status) where status is one of:
    'downloaded', 'skipped_cached', 'failed'."""
    local_path = local_image_path(unique_id)
    public_path = f"{IMAGES_PUBLIC_PREFIX}/{os.path.basename(local_path)}"

    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return public_path, "skipped_cached"

    os.makedirs(IMAGES_DIR, exist_ok=True)
    req = urllib.request.Request(remote_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if not data:
            print(f"    WARN: empty image response for {unique_id}", file=sys.stderr)
            return public_path, "failed"
        with open(local_path, "wb") as f:
            f.write(data)
        return public_path, "downloaded"
    except Exception as e:
        print(f"    WARN: image download failed for {unique_id} ({remote_url}): {e}", file=sys.stderr)
        return public_path, "failed"


def extract_cards_from_html(html: str, set_label: str) -> list:
    cards = []
    for m in CARD_BLOCK.finditer(html):
        dl_id, card_no, rarity, category, name = m.groups()
        unique_id = dl_id.strip()
        img_pattern = make_img_pattern(unique_id)
        img_match = img_pattern.search(html)
        if img_match:
            path = img_match.group(1)
            remote_img_url = path if path.startswith("http") else f"{OFFICIAL_BASE}/{path.lstrip('.').lstrip('/')}"
        else:
            remote_img_url = f"{OFFICIAL_BASE}/images/cardlist/card/{unique_id}.png"
        cards.append({
            "id": unique_id,
            "card_number": card_no.strip(),
            "name": name.strip(),
            "rarity": rarity.strip(),
            "category": category.strip().title(),
            "set_label": set_label,
            "set_id": set_label,
            "_remote_image_url": remote_img_url,
        })
    return cards


def discover_series_ids() -> dict:
    try:
        html = fetch(CARDLIST_URL)
    except Exception as e:
        print(f"ERROR: failed to fetch cardlist page at all: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

    os.makedirs(os.path.dirname(DEBUG_DUMP_PATH), exist_ok=True)
    with open(DEBUG_DUMP_PATH, "w", encoding="utf-8") as f:
        f.write(html[:50000])

    option_pattern = re.compile(
        r'<option\s+value="(\d+)"[^>]*>(.*?)</option>',
        re.IGNORECASE | re.DOTALL,
    )
    code_pattern = re.compile(r'\[([A-Z]{2,4}-?\d{0,2}(?:-EB\d{2})?)\]')

    found = {}
    for m in option_pattern.finditer(html):
        series_num, label = m.groups()
        code_match = code_pattern.search(label)
        if not code_match:
            continue
        set_code = code_match.group(1).strip()
        if set_code and set_code not in found:
            found[set_code] = series_num

    print(f"Discovered {len(found)} series IDs from set selector: {found}", file=sys.stderr)
    return found


def main():
    print(f"=== IMAGE-ONLY TEST RUN: fetching just {TEST_SETS} to verify downloads work ===", file=sys.stderr)
    print("This script makes ZERO calls to tcgapi.dev - your price quota is untouched.", file=sys.stderr)

    series_map = discover_series_ids()
    if not series_map:
        print("ERROR: could not discover series IDs - aborting test.", file=sys.stderr)
        sys.exit(1)

    test_relevant = {k: v for k, v in series_map.items() if k in TEST_SETS}
    if not test_relevant:
        print(f"ERROR: none of {TEST_SETS} found in discovered sets: {list(series_map.keys())}", file=sys.stderr)
        sys.exit(1)

    all_cards = []
    for set_id, series in test_relevant.items():
        url = f"{CARDLIST_URL}?series={series}"
        print(f"Fetching {set_id} ({url})", file=sys.stderr)
        html = fetch(url)
        cards = extract_cards_from_html(html, set_id)
        print(f"  Found {len(cards)} cards", file=sys.stderr)
        all_cards.extend(cards)
        time.sleep(REQUEST_DELAY)

    print(f"\nDownloading images for {len(all_cards)} test cards...", file=sys.stderr)
    downloaded, skipped, failed = 0, 0, 0
    results = []

    for i, c in enumerate(all_cards):
        remote_url = c.pop("_remote_image_url", None)
        public_path, status = download_image(c["id"], remote_url)
        c["image_url"] = public_path
        results.append((c["id"], c["card_number"], c["name"], status))

        if status == "downloaded":
            downloaded += 1
            time.sleep(0.3)
        elif status == "skipped_cached":
            skipped += 1
        else:
            failed += 1

        print(f"  [{i+1}/{len(all_cards)}] {c['id']} ({c['name']}): {status}", file=sys.stderr)

    print(f"\n=== TEST RESULT ===", file=sys.stderr)
    print(f"Downloaded: {downloaded}", file=sys.stderr)
    print(f"Already cached: {skipped}", file=sys.stderr)
    print(f"Failed: {failed}", file=sys.stderr)

    if downloaded == 0 and skipped == 0:
        print("\nALL DOWNLOADS FAILED. Bandai is likely blocking GitHub Actions' IP too.", file=sys.stderr)
        print("Do NOT merge image-downloading into the main scraper yet - investigate further first.", file=sys.stderr)
        sys.exit(1)
    elif failed > 0:
        print(f"\nPARTIAL SUCCESS: {downloaded + skipped} worked, {failed} failed. Investigate the failed ones.", file=sys.stderr)
    else:
        print("\nALL DOWNLOADS SUCCEEDED. Safe to merge this logic into scrape_cards.py for real use.", file=sys.stderr)

    # Write a small test-results file so it's visible in the repo after the run,
    # without touching the real cards.json at all.
    test_output_path = os.path.join(os.path.dirname(__file__), "..", "data", "_image_test_results.json")
    with open(test_output_path, "w", encoding="utf-8") as f:
        json.dump({
            "tested_sets": TEST_SETS,
            "total_cards": len(all_cards),
            "downloaded": downloaded,
            "skipped_cached": skipped,
            "failed": failed,
            "details": [{"id": uid, "card_number": cn, "name": n, "status": s} for uid, cn, n, s in results],
        }, f, indent=2, ensure_ascii=False)
    print(f"\nWrote test results to {test_output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
