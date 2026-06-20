name: Refresh One Piece Card Data

on:
  schedule:
    # Runs once every 2 days at 09:00 UTC. Change to "0 9 * * *" for daily.
    - cron: "0 9 */2 * *"
  workflow_dispatch: {}  # lets you trigger it manually from the Actions tab

permissions:
  contents: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run scraper
        env:
          TCGAPI_KEY: ${{ secrets.TCGAPI_KEY }}
        run: python scripts/scrape_cards.py

      - name: Commit updated data
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/cards.json
          git diff --staged --quiet || git commit -m "Auto-refresh card data $(date -u +%Y-%m-%d)"
          git push
