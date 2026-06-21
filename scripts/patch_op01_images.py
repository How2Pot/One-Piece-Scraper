#!/usr/bin/env python3
"""
ONE-OFF / THROWAWAY script. Patches image_url for OP-01 cards already
present in data/cards.json, pointing them at the local images already
downloaded by image_test_only.py (in data/images/), instead of the old
broken Bandai-hotlinked URLs.

Makes ZERO network calls - just rewrites existing JSON based on what
image files are already present on disk. Safe to run anytime, costs
nothing, touches no quota.

This is purely to preview the visual result in the live app before
deciding to merge image-downloading into the main scrape_cards.py.
Delete this file once you've confirmed things look right - it's not
meant to be part of the regular pipeline.
"""

import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cards.json")
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "images")
IMAGES_PUBLIC_PREFIX = "data/images"

TARGET_SET = "OP-01"


def main():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    cards = data.get("cards", [])
    updated = 0
    skipped_no_local_file = 0
    untouched = 0

    for c in cards:
        if c.get("set_id") != TARGET_SET:
            untouched += 1
            continue

        card_number = c.get("card_number") or c.get("id", "").split("_")[0]
        local_filename = f"{card_number}.png"
        local_path = os.path.join(IMAGES_DIR, local_filename)

        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            c["image_url"] = f"{IMAGES_PUBLIC_PREFIX}/{local_filename}"
            updated += 1
        else:
            # Leave the old URL alone if we don't actually have a local
            # copy - better to keep the old (broken) link visible as a
            # signal than silently point at a file that doesn't exist.
            skipped_no_local_file += 1

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Updated {updated} {TARGET_SET} cards to use local images")
    print(f"Skipped {skipped_no_local_file} {TARGET_SET} cards (no local image file found)")
    print(f"Left {untouched} cards from other sets untouched")


if __name__ == "__main__":
    main()
