#!/usr/bin/env python3
"""
ONE-OFF DEBUG script. Makes exactly ONE call to tcgapi.dev's /v1/search
endpoint and prints the full raw JSON response, unfiltered.

Purpose: confirm the real field names in the response and see all
printings for a specific card number before building matching logic.

Costs exactly 1 request against your daily tcgapi.dev quota.
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

    params = urllib.parse.urlencode({
        "q": "Dracule Mihawk",
        "game": "one-piece",
        "per_page": "30",
    })
    url = f"{TCGAPI_BASE}/search?{params}"
    req = urllib.request.Request(url, headers={**HEADERS, "X-API-Key": TCGAPI_KEY})

    print(f"Making ONE request to: {url}", file=sys.stderr)

    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)

    print("\n=== FULL RAW RESPONSE ===", file=sys.stderr)
    print(json.dumps(data, indent=2), file=sys.stderr)

    print("\n=== FIELD NAMES IN FIRST RESULT ===", file=sys.stderr)
    results = data.get("data", [])
    if results:
        for key, val in results[0].items():
            print(f"  {key}: {val!r}", file=sys.stderr)
    else:
        print("  No results returned.", file=sys.stderr)

    print("\n=== ALL RESULTS WHOSE 'number' CONTAINS 'OP14-119' ===", file=sys.stderr)
    matches = [r for r in results if "OP14-119" in (r.get("number") or "")]
    if not matches:
        print("  NONE FOUND in this page of results.", file=sys.stderr)
    for r in matches:
        print(f"  number={r.get('number')!r} printing={r.get('printing')!r} rarity={r.get('rarity')!r} "
              f"market_price={r.get('market_price')!r} set_name={r.get('set_name')!r} "
              f"tcgplayer_id={r.get('tcgplayer_id')!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
