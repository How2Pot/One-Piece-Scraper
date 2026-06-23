#!/usr/bin/env python3
"""
ONE-OFF TEST script. Runs the full price matching pipeline on OP-01 only
to verify all three matching strategies work correctly before committing
to a full group run that uses the daily quota budget.

Costs approximately 120-130 tcgapi.dev requests (one per card in OP-01).
Does NOT download images (skips that step entirely to save time).
Does NOT touch the existing cards.json (writes results to a separate
test output file: data/_test_op01_prices.json).

After running, check data/_test_op01_prices.json and look for:
- WARN lines in the logs indicating fallback matches (expected for some)
- SP reprint detected lines (confirms Strategy C is firing)
- Cards with null market_price (some are expected if tcgapi has no listing)
- Spot-check a few known cards (Trafalgar Law, Roronoa Zoro) for correct prices
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import datetime

OFFICIAL_BASE = "https://en.onepiece-cardgame.com"
CARDLIST_URL = f"{OFFICIAL_BASE}/cardlist/"
TCGAPI_BASE = "https://api.tcgapi.dev/v1"
TCGAPI_KEY = os.environ.get("TCGAPI_KEY", "")

TEST_SET = "EB-01"
TEST_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_test_eb01_prices.json")

REQUEST_DELAY = 0.8
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

CARD_BLOCK = re.compile(
    r'<dl class="modalCol" id="([^"]+)">.*?'
    r'<div class="infoCol">\s*'
    r'<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*'
    r'.*?<div class="cardName">([^<]+)</div>',
    re.DOTALL,
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"    [fetch] {url} -> HTTP {resp.status}, {len(body)} bytes", file=sys.stderr)
        return body


def discover_series_id(target_set: str) -> str | None:
    html = fetch(CARDLIST_URL)
    option_pattern = re.compile(
        r'<option\s+value="(\d+)"[^>]*>(.*?)</option>',
        re.IGNORECASE | re.DOTALL,
    )
    code_pattern = re.compile(r'\[([A-Z]{2,4}-?\d{0,2}(?:-EB\d{2})?)\]')
    for m in option_pattern.finditer(html):
        series_num, label = m.groups()
        code_match = code_pattern.search(label)
        if code_match and code_match.group(1).strip() == target_set:
            return series_num
    return None


def extract_cards_from_html(html: str, set_label: str) -> list:
    cards = []
    for m in CARD_BLOCK.finditer(html):
        dl_id, card_no, rarity, category, name = m.groups()
        unique_id = dl_id.strip()
        cards.append({
            "id": unique_id,
            "card_number": card_no.strip(),
            "name": name.strip(),
            "rarity": rarity.strip(),
            "category": category.strip().title(),
            "set_label": set_label,
            "set_id": set_label,
        })
    return cards


def fetch_price(card_name: str, unique_id: str, card_number: str, card_set_id: str = "", retries: int = 2) -> dict:
    if not TCGAPI_KEY:
        return {}

    card_num_set_prefix = re.match(r'^([A-Z]{2,4}-?\d{2,3})', card_number)
    set_id_prefix = re.match(r'^([A-Z]{2,4}-?\d{2,3})', card_set_id)
    is_sp_reprint = (
        card_num_set_prefix and set_id_prefix and
        card_num_set_prefix.group(1).replace("-", "").lower() !=
        set_id_prefix.group(1).replace("-", "").lower()
    )

    parallel_match = re.search(r'_p(\d+)$', unique_id)
    parallel_index = int(parallel_match.group(1)) if parallel_match else 0
    is_parallel = parallel_index > 0
    wanted_printing = "Foil" if is_parallel else "Normal"

    if is_sp_reprint:
        print(f"    [SP reprint] {unique_id} in {card_set_id} has number {card_number}", file=sys.stderr)

    def do_search(query: str) -> list:
        params = urllib.parse.urlencode({
            "q": query,
            "game": "one-piece",
            "per_page": "30",
        })
        url = f"{TCGAPI_BASE}/search?{params}"
        req = urllib.request.Request(url, headers={**HEADERS, "X-API-Key": TCGAPI_KEY})
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return data.get("data", [])
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries:
                    wait = 5 * (attempt + 1)
                    print(f"    WARN: 429 on '{query}', waiting {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"    WARN: price lookup failed for {card_name}: HTTP {e.code}", file=sys.stderr)
                return []
            except Exception as e:
                print(f"    WARN: price lookup failed for {card_name}: {e}", file=sys.stderr)
                return []
        return []

    # Primary search by card name
    results = do_search(card_name)

    # Fallback: use clean_name format - strip ALL non-alphanumeric chars,
    # handle HTML entities, lowercase, append number suffix
    num_suffix_match = re.search(r'-(\d+)$', card_number)
    num_suffix = num_suffix_match.group(1) if num_suffix_match else ""
    decoded_name = card_name
    for entity, char in [("&#039;", ""), ("&quot;", ""), ("&amp;", "and"),
                         ("&lt;", ""), ("&gt;", ""), ("&#39;", "")]:
        decoded_name = decoded_name.replace(entity, char)
    clean_name = re.sub(r'[^a-zA-Z0-9]', '', decoded_name).lower()
    fallback_query = f"{clean_name} {num_suffix}" if num_suffix else clean_name

    if not results or not any(card_number in (r.get("number") or "") for r in results):
        if fallback_query and fallback_query != card_name.lower():
            print(f"    [retry] no number match on '{card_name}', retrying with '{fallback_query}'", file=sys.stderr)
            time.sleep(REQUEST_DELAY)
            results = do_search(fallback_query)

    if not results:
        return {}

    def build_result(r):
        tcgplayer_id = r.get("tcgplayer_id")
        return {
            "market_price": r.get("market_price"),
            "tcgplayer_id": tcgplayer_id,
            "tcgplayer_url": f"https://www.tcgplayer.com/product/{tcgplayer_id}" if tcgplayer_id else None,
            "matched_name": r.get("name"),
            "matched_printing": r.get("printing"),
        }

    number_matches = [r for r in results if card_number and card_number in (r.get("number") or "")]
    if not number_matches:
        print(f"    WARN: no number match for {unique_id} - using top result", file=sys.stderr)
        return build_result(results[0])

    if is_sp_reprint:
        for r in number_matches:
            cn = (r.get("clean_name") or "").lower()
            if " sp" in cn or cn.endswith("sp"):
                return build_result(r)
        print(f"    WARN: SP detected for {unique_id} but no 'sp' clean_name found", file=sys.stderr)

    printings = set((r.get("printing") or "").strip().lower() for r in number_matches)
    all_same_printing = len(printings) <= 1

    if all_same_printing and len(number_matches) > 1:
        for r in number_matches:
            cn = (r.get("clean_name") or "").lower()
            if parallel_index == 2:
                if "manga" in cn: return build_result(r)
            elif parallel_index == 1:
                if "alternate" in cn or "alt art" in cn or "parallel" in cn: return build_result(r)
            else:
                if "manga" not in cn and "alternate" not in cn and "alt art" not in cn and " sp" not in cn:
                    return build_result(r)
        print(f"    WARN: clean_name matching failed for {unique_id}", file=sys.stderr)

    for r in number_matches:
        if (r.get("printing") or "").strip().lower() == wanted_printing.lower():
            return build_result(r)

    print(f"    WARN: no '{wanted_printing}' printing for {unique_id} - using first match", file=sys.stderr)
    return build_result(number_matches[0])


def main():
    if not TCGAPI_KEY:
        print("ERROR: TCGAPI_KEY not set.", file=sys.stderr)
        sys.exit(1)

    print(f"=== TEST RUN: {TEST_SET} only, no image downloads ===", file=sys.stderr)

    series_id = discover_series_id(TEST_SET)
    if not series_id:
        print(f"ERROR: could not find series ID for {TEST_SET}", file=sys.stderr)
        sys.exit(1)
    print(f"Found series ID: {series_id}", file=sys.stderr)
    time.sleep(REQUEST_DELAY)

    url = f"{CARDLIST_URL}?series={series_id}"
    html = fetch(url)
    cards = extract_cards_from_html(html, TEST_SET)
    print(f"Found {len(cards)} cards in {TEST_SET}", file=sys.stderr)
    time.sleep(REQUEST_DELAY)

    print(f"\nFetching prices for {len(cards)} cards...", file=sys.stderr)
    priced = 0
    null_price = 0

    for i, c in enumerate(cards):
        price_info = fetch_price(c["name"], c["id"], c["card_number"], c["set_id"])
        c["market_price"] = price_info.get("market_price")
        c["tcgplayer_url"] = price_info.get("tcgplayer_url")
        c["_matched_name"] = price_info.get("matched_name")
        c["_matched_printing"] = price_info.get("matched_printing")

        if c["market_price"] is not None:
            priced += 1
        else:
            null_price += 1

        if (i + 1) % 25 == 0:
            print(f"  Progress: {i + 1}/{len(cards)} ({priced} priced, {null_price} null)", file=sys.stderr)
        time.sleep(0.5)

    print(f"\n=== RESULTS ===", file=sys.stderr)
    print(f"Total cards: {len(cards)}", file=sys.stderr)
    print(f"Successfully priced: {priced}", file=sys.stderr)
    print(f"Null price (no tcgapi listing): {null_price}", file=sys.stderr)

    print(f"\n=== SPOT CHECK - first 5 cards ===", file=sys.stderr)
    for c in cards[:5]:
        print(f"  {c['id']} {c['name']}: ${c['market_price']} "
              f"(matched: {c['_matched_name']!r}, printing: {c['_matched_printing']!r})", file=sys.stderr)

    # Write results to a separate test file, not cards.json
    os.makedirs(os.path.dirname(TEST_OUTPUT_PATH), exist_ok=True)
    with open(TEST_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "test_set": TEST_SET,
            "generated_at": datetime.datetime.utcnow().isoformat(),
            "total_cards": len(cards),
            "priced": priced,
            "null_price": null_price,
            "cards": cards,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nWrote test results to {TEST_OUTPUT_PATH}", file=sys.stderr)
    print("Check this file to verify prices look correct before running the full scraper.", file=sys.stderr)


if __name__ == "__main__":
    main()
