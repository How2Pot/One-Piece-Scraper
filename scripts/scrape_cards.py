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

# Known set series IDs on the official site (?series=XXXXXX).
# Update this dict as new sets release — the site adds a new series id
# per set. If a set is missing, the scraper will still pick up "ALL".
SERIES_IDS = {
    "OP-01": "569101", "OP-02": "569102", "OP-03": "569103", "OP-04": "569104",
    "OP-05": "569105", "OP-06": "569106", "OP-07": "569107", "OP-08": "569108",
    "OP-09": "569109", "OP-10": "569110", "OP-11": "569111", "OP-12": "569112",
    "OP-13": "569113",
    "EB-01": "569201", "EB-02": "569202",
}

REQUEST_DELAY = 1.0  # be polite between requests
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OPTCGTrackerBot/1.0)"}


# ---- Official site scraping -------------------------------------------

class CardListParser(HTMLParser):
    """Minimal HTML parser pulling card blocks from the official cardlist page.

    The page structure (as of writing) repeats blocks like:
      <div class="resultCol"> ... img src="..." ... <span class="cardName">NAME</span>
      <div class="infoCol"><span>OP01-001</span><span>L</span><span>LEADER</span></div>
    Bandai may change markup over time; this parser is intentionally
    forgiving and falls back to regex extraction if needed.
    """
    pass


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_cards_from_html(html: str, set_label: str) -> list:
    """Extract card entries using regex — more robust than full HTML
    parsing against a site that may change its DOM structure slightly."""
    cards = []

    # Each card "dl" block roughly contains:
    #   data-src="...IMAGE.png" ... <div class="cardName">NAME</div>
    #   ...<div class="getCardNo">OP01-001 | L | LEADER</div>...
    # We scan for the number/rarity/category line and the nearest name + image.
    block_pattern = re.compile(
        r'data-src="([^"]+\.(?:png|jpg))".*?'
        r'class="cardName">([^<]+)<.*?'
        r'class="getCardNo">\s*([A-Z]{1,4}\d{2}(?:-EB\d{2})?-\d{3}(?:_p\d+)?)\s*\|\s*([A-Z]{1,3})\s*\|\s*([A-Z\s]+?)\s*<',
        re.DOTALL,
    )

    for m in block_pattern.finditer(html):
        img_path, name, card_id, rarity, category = m.groups()
        img_url = img_path if img_path.startswith("http") else f"{OFFICIAL_BASE}{img_path}"
        cards.append({
            "id": card_id.strip(),
            "name": name.strip(),
            "rarity": rarity.strip(),
            "category": category.strip().title(),
            "set_label": set_label,
            "image_url": img_url,
        })

    return cards


def scrape_official_cards() -> list:
    all_cards = {}
    print(f"Scraping official card list ({len(SERIES_IDS)} known sets)...", file=sys.stderr)

    for set_id, series in SERIES_IDS.items():
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
        print("No TCGAPI_KEY set — skipping price enrichment. Cards will have null prices.", file=sys.stderr)
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
        print("ERROR: No cards scraped. Site structure may have changed. Aborting without overwriting existing data.", file=sys.stderr)
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
