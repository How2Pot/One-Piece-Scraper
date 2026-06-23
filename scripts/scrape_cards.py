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
import subprocess
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
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "images")
# Public path the app will use, relative to wherever index.html is served
# from (repo root, per the GitHub Pages setup). This stays the same
# regardless of OS path separators used internally.
IMAGES_PUBLIC_PREFIX = "data/images"

REQUEST_DELAY = 0.8  # be polite between requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

DEBUG_DUMP_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "_debug_raw_page.html")

GROUP_A = ["OP-01", "OP-02", "OP-03", "OP-04", "OP-05", "OP-06", "OP-07", "OP-08"]
GROUP_B = [
    "OP-09", "OP-10", "OP-11", "OP-12", "OP-13", "OP14-EB04", "OP15-EB04", "OP-16",
    "EB-01", "EB-02", "EB-03",
    "PRB-01", "PRB-02",
]

import datetime
_day_of_year = datetime.datetime.utcnow().timetuple().tm_yday
ACTIVE_GROUP = GROUP_A if _day_of_year % 2 == 0 else GROUP_B
ACTIVE_GROUP_NAME = "A (OP-01 to OP-08)" if _day_of_year % 2 == 0 else "B (OP-09 to OP-16 + EB/PRB)"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        print(f"    [fetch] {url} -> HTTP {resp.status}, {len(body)} bytes", file=sys.stderr)
        return body


CARD_BLOCK = re.compile(
    r'<dl class="modalCol" id="([^"]+)">.*?'
    r'<div class="infoCol">\s*'
    r'<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*\|\s*<span>([^<]+)</span>\s*'
    r'.*?<div class="cardName">([^<]+)</div>',
    re.DOTALL,
)


def make_img_pattern(unique_id: str):
    """Match the image data-src for a SPECIFIC card variant, using its
    unique id (e.g. 'OP01-001' or 'OP01-001_p1'), not just the shared
    card_number. Using card_number alone would match BOTH the base card
    and any parallel/alt-art variant's image tag, since 'OP01-001' is a
    substring of 'OP01-001_p1' too - this caused base and parallel
    versions to incorrectly download/link to the same image file."""
    escaped = re.escape(unique_id)
    # Anchor with a non-word-char boundary after the id so 'OP01-001'
    # doesn't accidentally match inside 'OP01-001_p1's filename.
    return re.compile(rf'data-src="([^"]*?/card/{escaped}\.png[^"]*)"')


def local_image_path(unique_id: str) -> str:
    """Path is keyed on the unique card id (includes _p1/_p2 suffix for
    parallels), NOT the shared card_number - otherwise every variant of
    a card collapses onto the same image file."""
    safe_id = re.sub(r'[^A-Za-z0-9_\-]', '_', unique_id)
    return os.path.join(IMAGES_DIR, f"{safe_id}.png")


def download_image(unique_id: str, remote_url: str) -> str:
    """Download a card variant's image into the repo if not already
    present, keyed by its unique id (so base and parallel versions of
    the same card_number get separate files). Bandai's site blocks
    hotlinked <img> requests from other origins (confirmed: works fine
    fetched server-side here, but rejects browser requests from any page
    that isn't en.onepiece-cardgame.com itself). Storing a local copy in
    the repo sidesteps that permanently - the app then loads images from
    its own GitHub Pages origin, never from Bandai's CDN directly.

    Returns the PUBLIC relative path to use in cards.json (e.g.
    "data/images/OP01-001_p1.png"), regardless of whether this run
    actually downloaded it or it already existed from a previous run."""
    local_path = local_image_path(unique_id)
    public_path = f"{IMAGES_PUBLIC_PREFIX}/{os.path.basename(local_path)}"

    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return public_path  # already have it, skip re-downloading

    os.makedirs(IMAGES_DIR, exist_ok=True)
    req = urllib.request.Request(remote_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if not data:
            print(f"    WARN: empty image response for {unique_id}", file=sys.stderr)
            return public_path
        with open(local_path, "wb") as f:
            f.write(data)
        return public_path
    except Exception as e:
        print(f"    WARN: image download failed for {unique_id} ({remote_url}): {e}", file=sys.stderr)
        return public_path


def extract_cards_from_html(html: str, set_label: str) -> list:
    cards = []
    for m in CARD_BLOCK.finditer(html):
        dl_id, card_no, rarity, category, name = m.groups()
        unique_id = dl_id.strip()  # e.g. "OP01-001" or "OP01-001_p1" - unique per variant
        img_pattern = make_img_pattern(unique_id)
        img_match = img_pattern.search(html)
        if img_match:
            path = img_match.group(1)
            remote_img_url = path if path.startswith("http") else f"{OFFICIAL_BASE}/{path.lstrip('.').lstrip('/')}"
        else:
            # Fallback: construct the expected URL directly from the unique id,
            # not the shared card_number, so parallels still get their own path
            # even if the regex match fails for some reason.
            remote_img_url = f"{OFFICIAL_BASE}/images/cardlist/card/{unique_id}.png"
        cards.append({
            "id": unique_id,
            "card_number": card_no.strip(),
            "name": name.strip(),
            "rarity": rarity.strip(),
            "category": category.strip().title(),
            "set_label": set_label,
            "_remote_image_url": remote_img_url,  # internal only; resolved to local path before saving
        })
    return cards


def download_all_images(cards: list) -> list:
    """Second pass: download each card variant's image into the repo
    (skipping ones already saved from a previous run), then replace the
    remote Bandai URL with the local public path the app should use.
    Keyed by c['id'] (unique per variant, e.g. 'OP01-001_p1'), NOT
    c['card_number'] (shared across all variants of a card) - using the
    shared number was the bug that caused parallels/alt-arts to silently
    overwrite the base card's image file."""
    print(f"Downloading images for {len(cards)} cards (skipping any already saved)...", file=sys.stderr)
    downloaded = 0
    skipped = 0
    for i, c in enumerate(cards):
        remote_url = c.pop("_remote_image_url", None)
        if not remote_url:
            c["image_url"] = None
            continue

        local_path = local_image_path(c["id"])
        already_had_it = os.path.exists(local_path) and os.path.getsize(local_path) > 0

        c["image_url"] = download_image(c["id"], remote_url)

        if already_had_it:
            skipped += 1
        else:
            downloaded += 1
            time.sleep(0.3)  # only pace actual new downloads, not the (instant) skip path

        if (i + 1) % 50 == 0:
            print(f"  Images: {i + 1}/{len(cards)} processed ({downloaded} downloaded, {skipped} already cached)", file=sys.stderr)

    print(f"Image pass complete: {downloaded} newly downloaded, {skipped} already cached", file=sys.stderr)
    return cards


def discover_series_ids() -> dict:
    try:
        html = fetch(CARDLIST_URL)
    except Exception as e:
        print(f"ERROR: failed to fetch cardlist page at all: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

    os.makedirs(os.path.dirname(DEBUG_DUMP_PATH), exist_ok=True)
    with open(DEBUG_DUMP_PATH, "w", encoding="utf-8") as f:
        f.write(html[:50000])
    print(f"  Wrote debug dump to {DEBUG_DUMP_PATH} ({min(len(html),50000)} bytes)", file=sys.stderr)

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

    print(f"Today's active group: {ACTIVE_GROUP_NAME}", file=sys.stderr)
    relevant = {k: v for k, v in series_map.items() if k in ACTIVE_GROUP}

    missing = [s for s in ACTIVE_GROUP if s not in series_map]
    if missing:
        print(f"  WARN: these sets in the active group were not found on the site: {missing}", file=sys.stderr)

    print(f"Scraping {len(relevant)} of {len(ACTIVE_GROUP)} sets in active group...", file=sys.stderr)
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
            all_cards[c["id"]] = c

        time.sleep(REQUEST_DELAY)

    return list(all_cards.values())


def fetch_price(card_name: str, unique_id: str, card_number: str, card_set_id: str = "", retries: int = 2) -> dict:
    """Match a scraped card variant to its tcgapi.dev price entry.

    Three distinct matching strategies are needed:

    STRATEGY A - Normal/Foil printing distinction (most cards):
    Many cards have a base 'Normal' print and a parallel 'Foil' print.
    Our _p1 suffix = Foil, no suffix = Normal. Works for cards like
    Trafalgar Law (OP01-002): Normal=$4.44, Foil=$436.19.

    STRATEGY B - Name-based distinction (multi-variant SECs):
    Some cards (e.g. Mihawk OP14-119) have 3+ variants where ALL
    printings are 'Foil Only'. tcgapi.dev adds name suffixes:
      - Base:          "Dracule Mihawk - OP14-119"
      - Alternate Art: "Dracule Mihawk - OP14-119 (Alternate Art)"
      - Manga:         "Dracule Mihawk (Manga)"
    clean_name keywords: 'manga' -> _p2, 'alternate/alt' -> _p1

    STRATEGY C - SP reprint detection:
    SP cards appear in a newer set (e.g. OP-16) but keep the original
    card number (e.g. OP10-045). tcgapi.dev lists them as a separate
    entry with clean_name containing 'sp'. Detected by checking whether
    the card's set_id prefix doesn't match its card number prefix.
    e.g. card_set_id='OP-16', card_number='OP10-045' -> SP card."""
    if not TCGAPI_KEY:
        return {}

    # Detect SP reprint: set_id prefix doesn't match card number prefix
    # e.g. card in OP-16 but number is OP10-045 -> it's an SP card
    card_num_set_prefix = re.match(r'^([A-Z]{2,4}-?\d{2,3})', card_number)
    set_id_prefix = re.match(r'^([A-Z]{2,4}-?\d{2,3})', card_set_id)
    is_sp_reprint = (
        card_num_set_prefix and set_id_prefix and
        card_num_set_prefix.group(1).replace("-", "").lower() !=
        set_id_prefix.group(1).replace("-", "").lower()
    )

    # Determine parallel index (0=base, 1=first parallel, etc.)
    parallel_match = re.search(r'_p(\d+)$', unique_id)
    parallel_index = int(parallel_match.group(1)) if parallel_match else 0
    is_parallel = parallel_index > 0
    wanted_printing = "Foil" if is_parallel else "Normal"

    if is_sp_reprint:
        print(f"    [SP reprint detected] {unique_id} is in set {card_set_id} but has number {card_number}", file=sys.stderr)

    params = urllib.parse.urlencode({
        "q": card_name,
        "game": "one-piece",
        "per_page": "30",
    })
    url = f"{TCGAPI_BASE}/search?{params}"
    req = urllib.request.Request(url, headers={**HEADERS, "X-API-Key": TCGAPI_KEY})

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = 5 * (attempt + 1)
                print(f"    WARN: 429 rate limited on '{card_name}', waiting {wait}s before retry...", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"    WARN: price lookup failed for {card_name}: HTTP {e.code}", file=sys.stderr)
            return {}
        except Exception as e:
            print(f"    WARN: price lookup failed for {card_name}: {e}", file=sys.stderr)
            return {}
    else:
        return {}

    results = data.get("data", [])
    if not results:
        return {}

    def build_result(r):
        tcgplayer_id = r.get("tcgplayer_id")
        return {
            "market_price": r.get("market_price"),
            "tcgapi_image_url": r.get("image_url"),
            "tcgplayer_id": tcgplayer_id,
            "tcgplayer_url": f"https://www.tcgplayer.com/product/{tcgplayer_id}" if tcgplayer_id else None,
        }

    # All results whose card number matches ours
    number_matches = [r for r in results if card_number and card_number in (r.get("number") or "")]

    if not number_matches:
        print(f"    WARN: no number match for {unique_id} ({card_number}) - using top result", file=sys.stderr)
        return build_result(results[0])

    # Strategy C: SP reprint - look for clean_name containing 'sp'
    if is_sp_reprint:
        for r in number_matches:
            cn = (r.get("clean_name") or "").lower()
            if " sp" in cn or cn.endswith("sp"):
                return build_result(r)
        print(f"    WARN: SP reprint detected for {unique_id} but no 'sp' clean_name found - falling through", file=sys.stderr)

    # Check if all number-matches share the same printing (multi-variant case)
    printings = set((r.get("printing") or "").strip().lower() for r in number_matches)
    all_same_printing = len(printings) <= 1

    if all_same_printing and len(number_matches) > 1:
        # Strategy B: use clean_name keywords to pick the right variant
        for r in number_matches:
            cn = (r.get("clean_name") or "").lower()
            if parallel_index == 2:
                if "manga" in cn:
                    return build_result(r)
            elif parallel_index == 1:
                if "alternate" in cn or "alt art" in cn or "parallel" in cn:
                    return build_result(r)
            else:
                if "manga" not in cn and "alternate" not in cn and "alt art" not in cn and " sp" not in cn:
                    return build_result(r)
        print(f"    WARN: clean_name matching failed for {unique_id}, trying printing fallback", file=sys.stderr)

    # Strategy A: use printing to disambiguate (Normal vs Foil)
    for r in number_matches:
        if (r.get("printing") or "").strip().lower() == wanted_printing.lower():
            return build_result(r)

    # Final fallback: any number match
    print(f"    WARN: no '{wanted_printing}' printing found for {unique_id} - using first number match", file=sys.stderr)
    return build_result(number_matches[0])


def enrich_with_prices(cards: list, save_every: int = 25) -> list:
    if not TCGAPI_KEY:
        print("No TCGAPI_KEY set - skipping price enrichment. Cards will have null prices.", file=sys.stderr)
        for c in cards:
            c["market_price"] = None
            c["tcgplayer_url"] = None
        save_progress(cards)
        return cards

    print(f"Fetching prices for {len(cards)} cards via tcgapi.dev...", file=sys.stderr)
    for i, c in enumerate(cards):
        price_info = fetch_price(c["name"], c["id"], c["card_number"], c.get("set_id", ""))
        c["market_price"] = price_info.get("market_price")
        c["tcgplayer_url"] = price_info.get("tcgplayer_url")
        # image_url is already set to a local repo path by download_all_images;
        # we no longer fall back to tcgapi's image URL since it's hosted on
        # the same kind of CDN and would hit the same hotlink restrictions.

        if (i + 1) % save_every == 0:
            print(f"  Priced {i + 1}/{len(cards)}", file=sys.stderr)
            # Commit progress so far. GitHub Actions only gives a cancelled
            # job about 10 seconds before a hard kill, and that signal often
            # never reaches this process at all - so writing+pushing
            # periodically (not just once at the very end) is the only
            # reliable way to avoid losing a long run's work if it gets
            # cancelled or the runner is stopped partway through.
            save_progress(cards[:i + 1])
        time.sleep(0.5)

    # Final save covers any remainder not caught by the save_every checkpoint
    save_progress(cards)
    return cards


def load_existing_output() -> dict:
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"WARN: could not read existing cards.json ({e}), starting fresh.", file=sys.stderr)
    return {"generated_at": None, "card_count": 0, "sets": [], "cards": []}


def save_progress(priced_cards: list) -> None:
    """Merge freshly-priced cards into the existing cards.json, write it,
    and commit+push immediately. Called periodically during pricing (not
    just once at the end) so a cancelled run or exhausted API quota never
    loses completed work - only cards not yet reached this run keep
    whatever price they had before."""
    if not priced_cards:
        return

    existing = load_existing_output()
    existing_cards = {c["id"]: c for c in existing.get("cards", [])}
    for c in priced_cards:
        existing_cards[c["id"]] = c

    merged_cards = list(existing_cards.values())

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "card_count": len(merged_cards),
        "sets": sorted(set(c["set_id"] for c in merged_cards)),
        "last_group_refreshed": ACTIVE_GROUP_NAME,
        "cards": merged_cards,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    _git_commit_and_push()


def _git_commit_and_push() -> None:
    """Commit and push data/cards.json (and any new images) if there are
    changes. Silently no-ops if there's nothing new to commit, or if git
    isn't configured (e.g. running locally outside CI)."""
    try:
        subprocess.run(["git", "add", "-A", "data/"], check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True)
        if diff.returncode == 0:
            return  # nothing changed since last commit
        subprocess.run(
            ["git", "commit", "-m", f"Incremental update ({time.strftime('%Y-%m-%d %H:%M UTC')})"],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print("  [checkpoint committed and pushed]", file=sys.stderr)
    except Exception as e:
        print(f"  WARN: checkpoint commit/push failed (continuing anyway): {e}", file=sys.stderr)


def main():
    cards = scrape_official_cards()

    if not cards:
        print("ERROR: No cards scraped. Site structure may have changed.", file=sys.stderr)
        print(f"A raw HTML sample was saved to {DEBUG_DUMP_PATH} - check it into the repo logs to diagnose.", file=sys.stderr)
        print("Aborting without overwriting existing card data.", file=sys.stderr)
        sys.exit(1)

    cards = download_all_images(cards)
    # Commit downloaded images right away too, before pricing starts -
    # images can take a while on a fresh/empty data/images/ folder, and
    # this ensures they're saved even if pricing gets interrupted next.
    save_progress(cards)

    cards = enrich_with_prices(cards)

    # Append today's price to each card's history file.
    # This is a small, non-destructive addition - if the history file
    # doesn't exist yet (not yet backfilled), it creates a new one with
    # just today's entry. If it already exists, it appends today's entry
    # without touching any existing data. Completely safe.
    append_today_to_history(cards)

    print(f"Done. {len(cards)} cards from this run's group were priced and saved incrementally to {OUTPUT_PATH}", file=sys.stderr)


def append_today_to_history(cards: list) -> None:
    """Append today's market_price to each card's history file.
    Called after enrich_with_prices so prices are already set.
    Creates the file if it doesn't exist yet (for cards not yet
    backfilled). Skips cards with no market_price. Never overwrites
    or modifies existing history entries."""
    today = datetime.datetime.utcnow().date().isoformat()
    history_dir = os.path.join(os.path.dirname(__file__), "..", "data", "history")
    os.makedirs(history_dir, exist_ok=True)

    updated = 0
    skipped = 0

    for c in cards:
        if c.get("market_price") is None:
            skipped += 1
            continue

        card_id = c["id"]
        history_path = os.path.join(history_dir, f"{card_id}.json")

        # Load existing file or start fresh
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    history_data = json.load(f)
            except Exception:
                history_data = {"card_id": card_id, "history": []}
        else:
            history_data = {
                "card_id": card_id,
                "card_number": c.get("card_number", card_id),
                "name": c.get("name", ""),
                "history": [],
            }

        # Don't add a duplicate entry for today
        existing_dates = {e["date"] for e in history_data.get("history", [])}
        if today in existing_dates:
            skipped += 1
            continue

        history_data.setdefault("history", []).append({
            "date": today,
            "market_price": c["market_price"],
            "low_price": None,  # not available from the search endpoint
        })

        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=2, ensure_ascii=False)
        updated += 1

    print(f"History append complete: {updated} updated, {skipped} skipped (null price or already today)", file=sys.stderr)


if __name__ == "__main__":
    main()
