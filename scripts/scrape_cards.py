#!/usr/bin/env python3
"""
One Piece TCG card data + price scraper.

Pulls card names, IDs, rarities, set info, and images from the OFFICIAL
Bandai card list site (en.onepiece-cardgame.com) - this is publicly
accessible card data, no ToS concerns.

Pulls market prices from tcgapi.dev using your API key. This runs from
GitHub Actions (a server, not a browser), so tcgapi.dev's CORS/origin
restrictions do not apply here - it works exactly as designed.

Output: data/cards.json - a single file your app reads directly with
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

    # Save raw HTML for debugging if extraction ever yields nothing -
    # this gets committed so we can inspect actual site structure.
    os.makedirs(os.path.dirname(DEBUG_DUMP_PATH), exist_ok=True)
    with open(DEBUG_DUMP_PATH, "w", encoding="utf-8") as f:
        f.write(html[:50000])  # cap size
    print(f"  Wrote debug dump to {DEBUG_DUMP_PATH} ({min(len(html),50000)} bytes)", file=sys.stderr)

    # Real structure (verified against live page source):
    #   <option value="569116" selected>BOOSTER PACK ... [OP-16]</option>
    #   <option value="569101" >BOOSTER PACK ... [OP-01]</option>
    # Two-step: grab each <option> block, then pull the bracketed set code
    # from its label. The label text contains literal HTML-entity-encoded
    # markup (e.g. &lt;br ...&gt;), so we search broadly rather than
    # anchoring tightly to the text right after '>'.
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


def scrape_official_cards() -> list:
    all_cards = {}

    series_map = discover_series_ids()

    if not series_map:
        print("WARN: could not discover series IDs from selector - falling back to default 'ALL' page only.", file=sys.stderr)
        html = fetch(CARDLIST_URL)
        cards = extract_cards_from_html(html, "ALL")
        print(f"  Found {len(cards)} cards on default page", file=sys.stderr)
        for c in cards:
            c["set_id"] = c["id"][:4].rstrip("-")
            all_cards[c["id"]] = c
        return list(all_cards.values())

    # Only scrape OP-xx and EB-xx booster sets (skip starter decks, promos)
    relevant = {k: v for k, v in series_map.items() if re.match(r'^(OP|EB)-?\d{2}', k)}

    print(f"Scraping {len(relevant)} booster sets...", file=sys.stderr)
    for set_id, series in relevant.items():
        url = f"{CARDLIST_URL}?series={series}"
        print(f"  Fetching {set_id} ({url})", file=sys.stderr)
        try:
            html = fetch(url)
        except Exception as e:
            print(f"    WARN: failed to fetch {set_id}: {e}", file=sys.stderr)
            continue

        cards = extract_cards_from_html(html, set_id)
        print(f"    Found {len(cards)} cards", file=sys.stderr)

        for c in cards:
            c["set_id"] = set_id
            all_cards[c["id"]] = c  # dedupe by card id

        time.sleep(REQUEST_DELAY)

    return list(all_cards.values())


# ---- TCGapi.dev pricing -------------------------------------------------

def fetch_price(card_name: str, card_number: str) -> dict:
    """Query tcgapi.dev for a card's market price. Returns dict with
    market_price and tcg_image_url (fallback image), or empty dict."""
    if not TCGAPI_KEY:
        return {}

    params = urllib.parse.urlencode({
        "q": card_name,
        "game": "one-piece",
        "per_page": "10",
    })
    url = f"{TCGAPI_BASE}/search?{params}"
    req = urllib.request.Request(url, headers={**HEADERS, "X-API-Key": TCGAPI_KEY})

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"    WARN: price lookup failed for {card_name}: {e}", file=sys.stderr)
        return {}

    results = data.get("data", [])
    if not results:
        return {}

    # Try to match by card number first (most precise), else take first result
    for r in results:
        if card_number and card_number in (r.get("number") or ""):
            return {"market_price": r.get("market_price"), "tcgapi_image_url": r.get("image_url")}

    best = results[0]
    return {"market_price": best.get("market_price"), "tcgapi_image_url": best.get("image_url")}


def enrich_with_prices(cards: list) -> list:
    if not TCGAPI_KEY:
        print("No TCGAPI_KEY set - skipping price enrichment. Cards will have null prices.", file=sys.stderr)
        for c in cards:
            c["market_price"] = None
        return cards

    print(f"Fetching prices for {len(cards)} cards via tcgapi.dev...", file=sys.stderr)
    for i, c in enumerate(cards):
        price_info = fetch_price(c["name"], c["id"])
        c["market_price"] = price_info.get("market_price")
        # Keep official image as primary; store tcgapi image as fallback only
        if not c.get("image_url") and price_info.get("tcgapi_image_url"):
            c["image_url"] = price_info["tcgapi_image_url"]

        if (i + 1) % 25 == 0:
            print(f"  Priced {i + 1}/{len(cards)}", file=sys.stderr)
        time.sleep(0.3)  # gentle rate limiting

    return cards


# ---- Main ---------------------------------------------------------------

def main():
    cards = scrape_official_cards()

    if not cards:
        print("ERROR: No cards scraped. Site structure may have changed.", file=sys.stderr)
        print(f"A raw HTML sample was saved to {DEBUG_DUMP_PATH} - check it into the repo logs to diagnose.", file=sys.stderr)
        print("Aborting without overwriting existing card data.", file=sys.stderr)
        sys.exit(1)

    cards = enrich_with_prices(cards)

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "card_count": len(cards),
        "sets": sorted(set(c["set_id"] for c in cards)),
        "cards": cards,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(cards)} cards to {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
