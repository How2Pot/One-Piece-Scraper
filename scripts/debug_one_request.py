#!/usr/bin/env python3
"""
ONE-OFF DEBUG script. Makes exactly ONE call to tcgapi.dev's /v1/search
endpoint and prints the full raw JSON response, unfiltered.

Purpose: confirm the real field names in the response (e.g. whether
there's a numeric 'id' or 'tcgplayer_id' field we can use to construct
a link to the card's TCGPlayer product page) before building that logic
into the main scraper.

Costs exactly 1 request against your daily tcgapi.dev quota. Delete this
file once you've inspected the output - not meant to be part of the
regular pipeline.
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

    import time as t

    # Test multiple query variations to find what tcgapi.dev actually matches
    # for OP-16 cards which seem to use a different naming convention
    queries_to_test = [
        "Portgas D Ace 001",
        "Portgas D Ace",
        "portgasdace 001",
        "Portgas.D.Ace (001)",
    ]

    for q in queries_to_test:
        params = urllib.parse.urlencode({
            "q": q,
            "game": "one-piece",
            "per_page": "5",
        })
        url = f"{TCGAPI_BASE}/search?{params}"
        req = urllib.request.Request(url, headers={**HEADERS, "X-API-Key": TCGAPI_KEY})
        print(f"\nTesting query: {q!r}", file=sys.stderr)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", [])
            total = data.get("meta", {}).get("total", 0)
            print(f"  total results: {total}", file=sys.stderr)
            for r in results:
                print(f"  number={r.get('number')!r} name={r.get('name')!r} market_price={r.get('market_price')!r}", file=sys.stderr)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
        t.sleep(0.5)


if __name__ == "__main__":
    main()
