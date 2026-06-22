#!/usr/bin/env python3
"""
ONE-OFF script: scrapes and downloads the booster box product image for
each set from en.onepiece-cardgame.com, storing them in
data/product-images/{set_id}.webp

Makes zero tcgapi.dev requests - only fetches from Bandai's own site.
Run once (or whenever a new set releases) to keep the images current.

The app uses these images on the set picker screen to show the booster
box art alongside each set name.
"""

import os
import re
import sys
import time
import urllib.request

OFFICIAL_BASE = "https://en.onepiece-cardgame.com"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "product-images")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# Product page URL for each set - update when new sets release
# URL pattern for OP sets: /products/opNN.html
# URL pattern for EB sets: /products/boosters/ebNN.php
# URL pattern for PRB sets: /products/boosters/prbNN.php
SET_PRODUCT_PAGES = {
    "OP-01":     "/products/op01.html",
    "OP-02":     "/products/op02.html",
    "OP-03":     "/products/op03.html",
    "OP-04":     "/products/op04.html",
    "OP-05":     "/products/op05.html",
    "OP-06":     "/products/op06.html",
    "OP-07":     "/products/op07.html",
    "OP-08":     "/products/op08.html",
    "OP-09":     "/products/op09.html",
    "OP-10":     "/products/op10.html",
    "OP-11":     "/products/op11.html",
    "OP-12":     "/products/op12.html",
    "OP-13":     "/products/op13.html",
    "OP14-EB04": "/products/op14.html",
    "OP15-EB04": "/products/op15.html",
    "OP-16":     "/products/op16.html",
    "EB-01":     "/products/boosters/eb01.php",
    "EB-02":     "/products/boosters/eb02.php",
    "EB-03":     "/products/boosters/eb03.php",
    "PRB-01":    "/products/boosters/prb01.php",
    "PRB-02":    "/products/boosters/prb02.php",
}

# Regex to find the product image in the page HTML
# The image is always the first img_item01.webp on the product page
IMG_PATTERN = re.compile(r'(https://[^"\']+img_item01\.webp[^"\']*)')


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def download_image(url: str, dest_path: str) -> bool:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if not data:
            return False
        with open(dest_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"    WARN: image download failed: {e}", file=sys.stderr)
        return False


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    downloaded = 0
    skipped = 0
    failed = 0

    for set_id, path in SET_PRODUCT_PAGES.items():
        dest_path = os.path.join(OUTPUT_DIR, f"{set_id}.webp")

        # Skip if already downloaded
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            print(f"  {set_id}: already exists, skipping", file=sys.stderr)
            skipped += 1
            continue

        url = f"{OFFICIAL_BASE}{path}"
        print(f"  {set_id}: fetching {url}", file=sys.stderr)

        try:
            html = fetch_html(url)
        except Exception as e:
            print(f"    WARN: failed to fetch product page: {e}", file=sys.stderr)
            failed += 1
            time.sleep(1.0)
            continue

        img_match = IMG_PATTERN.search(html)
        if not img_match:
            print(f"    WARN: no img_item01.webp found on page", file=sys.stderr)
            failed += 1
            time.sleep(1.0)
            continue

        img_url = img_match.group(1)
        print(f"    Found image: {img_url}", file=sys.stderr)

        if download_image(img_url, dest_path):
            size_kb = os.path.getsize(dest_path) // 1024
            print(f"    Downloaded ({size_kb}KB) -> {dest_path}", file=sys.stderr)
            downloaded += 1
        else:
            failed += 1

        time.sleep(0.8)  # be polite between requests

    print(f"\nDone: {downloaded} downloaded, {skipped} already cached, {failed} failed", file=sys.stderr)
    if failed > 0:
        print("Failed sets may have different URL patterns - check manually", file=sys.stderr)


if __name__ == "__main__":
    main()
