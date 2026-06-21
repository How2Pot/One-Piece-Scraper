#!/usr/bin/env python3
"""
ONE-OFF TEST script. Re-prices exactly ONE card (and its parallel, if
present) using the NEW printing-aware matching logic from scrape_cards.py,
then writes the corrected price directly into data/cards.json so you can
verify it live on the app without running a full group refresh.

Costs exactly 1 tcgapi.dev request per card tested (1 or 2 total, since
it tests both the base card and its _p1 parallel if both exist in
cards.json). Does NOT re-scrape, re-download images, or touch any other
card's data.

Delete this file once you've confirmed the fix works - not meant to be
part of the regular pipeline.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse

TCGAPI_BASE = "https://api.tcgapi.dev/v1"
TCGAPI_KEY = os.environ.get("TCGAPI_KEY", "")
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cards.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# The specific card to test - change this to whichever card_number you
# want to verify. Tests both the base id and the _p1 variant if present
# in cards.json.
TEST_CARD_NUMBER = "OP01-002"


def fetch_price(card_name: str, unique_id: str, card_number: str) -> dict:
    """Same printing-aware matching logic as the main scraper."""
    is_parallel = "_p" in unique_id
    wanted_printing = "Foil" if is_parallel else "Normal"

    params = urllib.parse.urlencode({
        "q": card_name,
        "game": "one-piece",
        "per_page": "30",
    })
    url = f"{TCGAPI_BASE}/search?{params}"
    req = urllib.request.Request(url, headers={**HEADERS, "X-API-Key": TCGAPI_KEY})

    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = data.get("data", [])
    if not results:
        return {}

    def build_result(r):
        tcgplayer_id = r.get("tcgplayer_id")
        return {
            "market_price": r.get("market_price"),
            "tcgplayer_id": tcgplayer_id,
            "tcgplayer_url": f"https://www.tcgplayer.com/product/{tcgplayer_id}" if tcgplayer_id else None,
            "matched_printing": r.get("printing"),
            "matched_set_name": r.get("set_name"),
        }

    number_matches = [r for r in results if card_number and card_number in (r.get("number") or "")]
    for r in number_matches:
        if (r.get("printing") or "").strip().lower() == wanted_printing.lower():
            return build_result(r)

    if number_matches:
        print(f"  WARN: no '{wanted_printing}' printing found for {unique_id}, using first available", file=sys.stderr)
        return build_result(number_matches[0])

    print(f"  WARN: no number match for {unique_id} at all, using top result", file=sys.stderr)
    return build_result(results[0])


def main():
    if not TCGAPI_KEY:
        print("ERROR: TCGAPI_KEY not set.", file=sys.stderr)
        sys.exit(1)

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    cards = data.get("cards", [])
    targets = [c for c in cards if c.get("card_number") == TEST_CARD_NUMBER]

    if not targets:
        print(f"ERROR: no cards found with card_number == {TEST_CARD_NUMBER!r} in cards.json", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(targets)} variant(s) of {TEST_CARD_NUMBER} to re-price:", file=sys.stderr)
    for c in targets:
        print(f"  - id={c['id']}  current market_price={c.get('market_price')}", file=sys.stderr)

    for c in targets:
        print(f"\nRe-pricing {c['id']}...", file=sys.stderr)
        price_info = fetch_price(c["name"], c["id"], c["card_number"])
        old_price = c.get("market_price")
        c["market_price"] = price_info.get("market_price")
        c["tcgplayer_url"] = price_info.get("tcgplayer_url")
        print(f"  OLD price: {old_price}", file=sys.stderr)
        print(f"  NEW price: {c['market_price']} (matched printing={price_info.get('matched_printing')!r}, "
              f"set={price_info.get('matched_set_name')!r})", file=sys.stderr)
        time.sleep(0.5)

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nWrote updated prices for {TEST_CARD_NUMBER} back to {DATA_PATH}", file=sys.stderr)
    print("Refresh the live app and search for this card to verify.", file=sys.stderr)


if __name__ == "__main__":
    main()
