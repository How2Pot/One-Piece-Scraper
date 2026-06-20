#!/usr/bin/env python3
"""
One Piece TCG card data + price scraper.

Pulls card names, IDs, rarities, set info, and images from the OFFICIAL
Bandai card list site (en.onepiece-cardgame.com) — this is publicly
accessible card data, no ToS concerns.

Pulls market prices from tcgapi.dev using your API key. This runs from
GitHub Actions (a server, not a browser), so tcgapi.dev's CORS/origin
restrictions do not apply here — it works exactly as designed.

Output: data/cards.json — a single file your app reads directly with
no live API calls needed at runtime.

Run on a schedule via GitHub Actions (see .github/workflows/refresh.yml).
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser

# ---- Config -----------------------------------------------------------

OFFICIAL_BASE = "https://en.onepiece-cardgame.com"
CARDLIST_URL = f"{OFFICIAL_BASE}/cardlist/"
TCGAPI_BASE = "https://api.tcgapi.dev/v1"
TCGAPI_KEY = os.environ.get("TCGAPI_KEY", "")  # set as a GitHub Actions secret

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cards.json")

REQUEST_DELAY = 0.8  # be polite between requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

DEBUG_DUMP_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_debug_raw_page.html")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"    [fetch] {url} -> HTTP {resp.status}, {len(body)} bytes", file=sys.stderr)
        return body


# ---- Card block extraction ---------------------------------------------
#
# Verified against live page source. Each card is a <dl class="modalCol"
# id="OP16-001"> block containing:
#
#   <div class="infoCol">
#     <span>OP16-001</span> | <span>L</span> | <span>LEADER</span>
#   </div>
#   <div class="cardName">Portgas.D.Ace</div>
#
# Images are lazy-loaded via data-src on a preceding <a class="modalOpen">:
#   <a data-src="#OP16-001"><img data-src="../images/cardlist/card/OP16-001.png?v" alt="..."></a>
#
# Parallel/alternate-art variants share the base card ID with a _pN suffix
# (e.g. OP16-001_p1) and appear as their own complete <dl> block.

CARD_BLOCK = re.compile(
    r'<dl class="modalCol" id="([^"]+)">.*?'
    r'<div class="infoCol">\s*'
    r'<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*'
    r'.*?<div class="cardName">([^<]+)</div>',
    re.DOTALL,
)


def make_img_pattern(card_id: str):
    escaped = re.escape(card_id)
    return re.compile(rf'data-src="([^"]*?/card/{escaped}\.png[^"]*)"')


def extract_cards_from_html(html: str, set_label: str) -> list:
    cards = []

    for m in CARD_BLOCK.finditer(html):
        dl_id, card_no, rarity, category, name = m.groups()

        img_pattern = make_img_pattern(card_no.strip())
        img_match = img_pattern.search(html)
        if img_match:
            path = img_match.group(1)
            img_url = path if path.startswith("http") else f"{OFFICIAL_BASE}/{path.lstrip('.').lstrip('/')}"
        else:
            img_url = f"{OFFICIAL_BASE}/images/cardlist/card/{card_no.strip()}.png"

        cards.append({
            "id": dl_id.strip(),
            "card_number": card_no.strip(),
            "name": name.strip(),
            "rarity": rarity.strip(),
            "category": category.strip().title(),
            "set_label": set_label,
            "image_url": img_url,
        })

    return cards


def discover_series_ids() -> dict:
    """Parse the cardlist page's set-selector to find real series IDs,
    instead of relying on a hardcoded (and possibly wrong) mapping."""
    try:
        html = fetch(CARDLIST_URL)
    except Exception as e:
        print(f"ERROR: failed to fetch cardlist page at all: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

    os.makedirs(os.path.dirname(DEBUG_DUMP_PATH), exist_ok=True)
    with open(DEBUG_DUMP_PATH,
