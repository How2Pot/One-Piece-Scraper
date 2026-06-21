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

    params = urllib.parse.urlencode({
        "q": "Roronoa Zoro",
        "game": "one-piece",
        "per_page": "3",
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


if __name__ == "__main__":
    main()
