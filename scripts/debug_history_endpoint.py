#!/usr/bin/env python3
"""
ONE-OFF DEBUG script: tests the tcgapi.dev /cards/{id}/history endpoint
for OP01-002 (Trafalgar Law) to confirm it works on the Starter plan and
see the real response shape before building anything.

Costs exactly 1 tcgapi.dev request.

The tcgapi.dev internal card ID is NOT the same as the One Piece card
number (OP01-002). We need the numeric tcgapi.dev id from a search result
first, then use that to call the history endpoint.

So this script makes 1 search call to get the tcgapi.dev id for OP01-002
Normal printing, then calls the history endpoint with that id.
Total: 2 requests (search + history).
"""

import json
import os
import sys
import urllib.request
import urllib.parse

TCGAPI_BASE = "https://api.tcgapi.dev/v1"
TCGAPI_KEY = os.environ.get("TCGAPI_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def main():
    if not TCGAPI_KEY:
        print("ERROR: TCGAPI_KEY not set.", file=sys.stderr)
        sys.exit(1)

    # Step 1: search for OP01-002 Normal to get the tcgapi.dev numeric id
    print("Step 1: searching for OP01-002 Normal printing...", file=sys.stderr)
    params = urllib.parse.urlencode({
        "q": "Trafalgar Law",
        "game": "one-piece",
        "per_page": "30",
    })
    req = urllib.request.Request(
        f"{TCGAPI_BASE}/search?{params}",
        headers={**HEADERS, "X-API-Key": TCGAPI_KEY}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        search_data = json.loads(resp.read().decode("utf-8"))

    results = search_data.get("data", [])
    number_matches = [r for r in results if "OP01-002" in (r.get("number") or "")]
    normal_match = next(
        (r for r in number_matches if (r.get("printing") or "").lower() == "normal"),
        None
    )

    if not normal_match:
        print("ERROR: could not find OP01-002 Normal in search results", file=sys.stderr)
        print("All number matches found:", file=sys.stderr)
        for r in number_matches:
            print(f"  id={r.get('id')} number={r.get('number')} printing={r.get('printing')}", file=sys.stderr)
        sys.exit(1)

    tcgapi_id = normal_match.get("id")
    tcgplayer_id = normal_match.get("tcgplayer_id")
    print(f"Found: tcgapi id={tcgapi_id}, tcgplayer_id={tcgplayer_id}, "
          f"market_price={normal_match.get('market_price')}", file=sys.stderr)

    # Step 2: call the history endpoint with the tcgapi numeric id
    print(f"\nStep 2: fetching price history for tcgapi id {tcgapi_id}...", file=sys.stderr)
    history_url = f"{TCGAPI_BASE}/cards/{tcgapi_id}/history"
    req2 = urllib.request.Request(
        history_url,
        headers={**HEADERS, "X-API-Key": TCGAPI_KEY}
    )

    try:
        with urllib.request.urlopen(req2, timeout=20) as resp:
            history_data = json.loads(resp.read().decode("utf-8"))

        print("\n=== FULL HISTORY RESPONSE ===", file=sys.stderr)
        print(json.dumps(history_data, indent=2), file=sys.stderr)

        # Summarise what we got
        history = history_data.get("data") or history_data.get("history") or []
        print(f"\n=== SUMMARY ===", file=sys.stderr)
        print(f"Number of data points: {len(history)}", file=sys.stderr)
        if history:
            print(f"Field names in first entry: {list(history[0].keys())}", file=sys.stderr)
            print(f"First entry: {history[0]}", file=sys.stderr)
            print(f"Last entry:  {history[-1]}", file=sys.stderr)
        else:
            print("No history data returned - may not be available on Starter plan", file=sys.stderr)

    except urllib.error.HTTPError as e:
        print(f"\nHTTP ERROR {e.code} calling history endpoint", file=sys.stderr)
        print(f"URL: {history_url}", file=sys.stderr)
        body = e.read().decode("utf-8", errors="replace")
        print(f"Response body: {body}", file=sys.stderr)
        if e.code == 403:
            print("\nNOTE: 403 likely means this endpoint requires a higher plan tier", file=sys.stderr)
        elif e.code == 404:
            print("\nNOTE: 404 may mean the endpoint path is different - check tcgapi.dev docs", file=sys.stderr)


if __name__ == "__main__":
    main()
