#!/usr/bin/env python3
"""
Backfill price history for all cards in cards.json using tcgapi.dev's
/cards/{id}/history endpoint (available on Starter plan, 30-day history).

DESIGN:
- Only fetches history for cards that don't already have a history file
  (so running this multiple days gradually fills the catalog without
  re-fetching cards already done)
- Caps the number of history fetches per run (default 200) so we never
  exceed the 2,500/day Starter quota even accounting for the daily
  scraper run also happening that day
- Each card costs 2 requests: 1 search (to get the tcgapi numeric id)
  + 1 history call. With a cap of 200 cards = 400 requests max per run.
- After 5-6 days the whole catalog is backfilled and this script becomes
  a no-op (all files already exist, nothing to do)

SAFE TO RUN ALONGSIDE THE MAIN SCRAPER:
- Writes to data/history/{card_id}.json only
- Does NOT touch data/cards.json or data/images/
- The main scraper's append logic (added separately) reads these same
  files and appends today's price - the two scripts complement each other

RUN VIA: .github/workflows/backfill_history.yml (manual trigger only,
no schedule - run it daily until all cards are backfilled, then stop)
"""

import json
import os
import sys
import time
import datetime
import subprocess
import urllib.request
import urllib.parse

TCGAPI_BASE = "https://api.tcgapi.dev/v1"
TCGAPI_KEY = os.environ.get("TCGAPI_KEY", "")

CARDS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cards.json")
HISTORY_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "history")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# Max cards to backfill per run. Each card costs 2 requests (search + history).
# 200 cards = 400 requests, leaving ~2,100 for the daily scraper run.
# Increase this if you are NOT running the main scraper on the same day.
MAX_CARDS_PER_RUN = 200

# How long to wait between requests (seconds)
REQUEST_DELAY = 0.5


def fetch_json(url: str, extra_headers: dict = None) -> dict:
    headers = {**HEADERS, "X-API-Key": TCGAPI_KEY}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_tcgapi_id(card_name: str, card_number: str, is_parallel: bool) -> int | None:
    """Search tcgapi.dev to find the numeric id for this specific card
    and printing. Returns the id, or None if not found.
    Uses the same two-pass search strategy as the main scraper:
    1. Primary: search by card name
    2. Fallback: search by clean_name format (strip non-alphanumeric, lowercase)
       e.g. "Tony Tony.Chopper 006" for cards stored as "Tony Tony.Chopper (006)"
    """
    import re as _re
    wanted_printing = "Foil" if is_parallel else "Normal"

    def do_search(query: str) -> list:
        params = urllib.parse.urlencode({
            "q": query,
            "game": "one-piece",
            "per_page": "30",
        })
        try:
            data = fetch_json(f"{TCGAPI_BASE}/search?{params}")
            time.sleep(REQUEST_DELAY)
            return data.get("data", [])
        except Exception as e:
            print(f"    WARN: search failed for {card_number} ('{query}'): {e}", file=sys.stderr)
            return []

    def find_match(results: list) -> int | None:
        number_matches = [r for r in results if card_number in (r.get("number") or "")]
        for r in number_matches:
            if (r.get("printing") or "").strip().lower() == wanted_printing.lower():
                return r.get("id")
        if number_matches:
            return number_matches[0].get("id")
        return None

    # Primary search by card name
    results = do_search(card_name)
    match = find_match(results)
    if match:
        return match

    # Fallback: clean_name format - strip all non-alphanumeric chars,
    # decode HTML entities, lowercase, append number suffix
    decoded = card_name
    for entity, char in [("&#039;", ""), ("&quot;", ""), ("&amp;", "and"),
                         ("&lt;", ""), ("&gt;", ""), ("&#39;", "")]:
        decoded = decoded.replace(entity, char)
    num_suffix_match = _re.search(r'-(\d+)$', card_number)
    num_suffix = num_suffix_match.group(1) if num_suffix_match else ""
    clean = _re.sub(r'[^a-zA-Z0-9]', '', decoded).lower()
    fallback_query = f"{clean} {num_suffix}" if num_suffix else clean

    if fallback_query and fallback_query != card_name.lower():
        print(f"    [retry] no match on '{card_name}', retrying with '{fallback_query}'", file=sys.stderr)
        results = do_search(fallback_query)
        match = find_match(results)
        if match:
            return match

    return None


def fetch_history(tcgapi_id: int) -> list:
    """Fetch price history for a tcgapi.dev numeric card id."""
    try:
        data = fetch_json(f"{TCGAPI_BASE}/cards/{tcgapi_id}/history")
        time.sleep(REQUEST_DELAY)
        return data.get("data", [])
    except Exception as e:
        print(f"    WARN: history fetch failed for id {tcgapi_id}: {e}", file=sys.stderr)
        return []


def history_path(card_id: str) -> str:
    safe_id = card_id.replace("/", "_")
    return os.path.join(HISTORY_DIR, f"{safe_id}.json")


def already_backfilled(card_id: str) -> bool:
    path = history_path(card_id)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Consider backfilled if it has at least 5 entries
        # (distinguishes real backfill from a single-day append)
        return len(data.get("history", [])) >= 5
    except Exception:
        return False


def save_history(card_id: str, card_data: dict, history_entries: list) -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()

    # Convert API response entries to our standard format
    formatted = []
    seen_dates = set()
    for entry in history_entries:
        date = entry.get("date")
        if not date or date in seen_dates:
            continue
        seen_dates.add(date)
        formatted.append({
            "date": date,
            "market_price": entry.get("market_price"),
            "low_price": entry.get("low_price"),
        })

    # Sort by date ascending
    formatted.sort(key=lambda x: x["date"])

    output = {
        "card_id": card_id,
        "card_number": card_data.get("card_number", card_id),
        "name": card_data.get("name", ""),
        "backfilled_at": today,
        "history": formatted,
    }

    with open(history_path(card_id), "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def git_commit_progress(count: int) -> None:
    """Commit progress so far - if the run gets cancelled we keep
    whatever history files were already written."""
    try:
        subprocess.run(["git", "add", "-A", "data/history/"], check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True)
        if diff.returncode == 0:
            return
        subprocess.run(
            ["git", "commit", "-m", f"Backfill history checkpoint ({count} cards)"],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print(f"  [checkpoint committed - {count} cards backfilled so far]", file=sys.stderr)
    except Exception as e:
        print(f"  WARN: checkpoint commit failed: {e}", file=sys.stderr)


def main():
    if not TCGAPI_KEY:
        print("ERROR: TCGAPI_KEY not set.", file=sys.stderr)
        sys.exit(1)

    # Load cards.json to get the full card catalog
    with open(CARDS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_cards = data.get("cards", [])
    print(f"Total cards in catalog: {len(all_cards)}", file=sys.stderr)

    # Find cards that still need backfilling
    needs_backfill = [c for c in all_cards if not already_backfilled(c["id"])]
    print(f"Cards still needing backfill: {len(needs_backfill)}", file=sys.stderr)

    if not needs_backfill:
        print("All cards already backfilled - nothing to do.", file=sys.stderr)
        return

    # Cap how many we process this run
    to_process = needs_backfill[:MAX_CARDS_PER_RUN]
    print(f"Processing {len(to_process)} cards this run (cap: {MAX_CARDS_PER_RUN})", file=sys.stderr)
    print(f"Estimated requests: {len(to_process) * 2} (2 per card: search + history)", file=sys.stderr)

    done = 0
    skipped = 0

    for i, card in enumerate(to_process):
        card_id = card["id"]
        card_name = card.get("name", "")
        card_number = card.get("card_number", card_id)
        is_parallel = "_p" in card_id

        print(f"  [{i+1}/{len(to_process)}] {card_id} ({card_name})...", file=sys.stderr)

        # Step 1: get tcgapi numeric id
        tcgapi_id = get_tcgapi_id(card_name, card_number, is_parallel)
        if not tcgapi_id:
            print(f"    SKIP: could not find tcgapi id", file=sys.stderr)
            skipped += 1
            continue

        # Step 2: fetch history
        history_entries = fetch_history(tcgapi_id)
        if not history_entries:
            print(f"    SKIP: no history data returned", file=sys.stderr)
            skipped += 1
            continue

        # Step 3: save to file
        save_history(card_id, card, history_entries)
        done += 1
        print(f"    Saved {len(history_entries)} entries", file=sys.stderr)

        # Commit every 50 cards so progress survives a cancellation
        if done % 50 == 0:
            git_commit_progress(done)

    # Final commit
    git_commit_progress(done)

    remaining = len(needs_backfill) - len(to_process)
    print(f"\nDone: {done} backfilled, {skipped} skipped", file=sys.stderr)
    if remaining > 0:
        print(f"{remaining} cards still need backfilling - run this script again tomorrow", file=sys.stderr)
    else:
        print("All cards have been backfilled!", file=sys.stderr)


if __name__ == "__main__":
    main()
