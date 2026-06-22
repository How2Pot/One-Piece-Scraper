#!/usr/bin/env python3
"""
ONE-OFF script: seeds price history for OP01-002 and OP01-002_p1
(Trafalgar Law base and parallel) as a test of the history pipeline.

Costs exactly 2 tcgapi.dev requests. Writes to:
  data/history/OP01-002.json
  data/history/OP01-002_p1.json

If a history file already exists, APPENDS today's entry rather than
overwriting - so running this multiple days in a row builds up real
history data. Will not add a duplicate entry if today's date is already
present in the file.

Once this test is confirmed working end-to-end (scraper writes history,
app reads and charts it correctly), the same logic gets added to the
main scrape_cards.py for the full catalog.
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.parse

TCGAPI_BASE = "https://api.tcgapi.dev/v1"
TCGAPI_KEY = os.environ.get("TCGAPI_KEY", "")
HISTORY_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "history")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# The two cards to test with - confirmed correct Normal/Foil matching
TEST_CARDS = [
    {"id": "OP01-002",    "card_number": "OP01-002", "name": "Trafalgar Law", "is_parallel": False},
    {"id": "OP01-002_p1", "card_number": "OP01-002", "name": "Trafalgar Law", "is_parallel": True},
]


def fetch_price(card_name: str, card_number: str, is_parallel: bool) -> float | None:
    """Fetch market_price using the same printing-aware logic as the
    main scraper - Normal for base cards, Foil for parallels."""
    if not TCGAPI_KEY:
        print("ERROR: TCGAPI_KEY not set.", file=sys.stderr)
        sys.exit(1)

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
    number_matches = [r for r in results if card_number in (r.get("number") or "")]

    for r in number_matches:
        if (r.get("printing") or "").strip().lower() == wanted_printing.lower():
            price = r.get("market_price")
            print(f"  Matched {wanted_printing} printing: ${price}", file=sys.stderr)
            return price

    if number_matches:
        price = number_matches[0].get("market_price")
        print(f"  WARN: no {wanted_printing} match, using first result: ${price}", file=sys.stderr)
        return price

    print(f"  WARN: no number match at all for {card_number}", file=sys.stderr)
    return None


def load_history(card_id: str) -> dict:
    path = os.path.join(HISTORY_DIR, f"{card_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"card_id": card_id, "history": []}


def save_history(card_id: str, data: dict) -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"{card_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved history to {path}", file=sys.stderr)


def main():
    today = datetime.date.today().isoformat()
    print(f"Seeding price history for {len(TEST_CARDS)} test cards (date: {today})", file=sys.stderr)
    print(f"This will use exactly {len(TEST_CARDS)} tcgapi.dev requests.", file=sys.stderr)

    for card in TEST_CARDS:
        print(f"\nFetching price for {card['id']}...", file=sys.stderr)
        price = fetch_price(card["name"], card["card_number"], card["is_parallel"])

        history_data = load_history(card["id"])

        # Add card metadata if missing (first run)
        history_data["card_id"] = card["id"]
        history_data["card_number"] = card["card_number"]
        history_data["name"] = card["name"]

        # Check if today's entry already exists - don't add duplicates
        existing_dates = [e["date"] for e in history_data.get("history", [])]
        if today in existing_dates:
            print(f"  Entry for {today} already exists - skipping duplicate", file=sys.stderr)
        else:
            history_data.setdefault("history", []).append({
                "date": today,
                "price": price,
            })
            print(f"  Appended entry: date={today}, price={price}", file=sys.stderr)

        save_history(card["id"], history_data)
        time.sleep(0.5)

    print(f"\nDone. History files written to {HISTORY_DIR}/", file=sys.stderr)
    print("Next step: verify the app reads and charts this data correctly.", file=sys.stderr)


if __name__ == "__main__":
    main()
