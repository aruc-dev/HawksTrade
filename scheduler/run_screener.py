"""
HawksTrade - Nightly Universe Screener
=======================================
Runs the dynamic universe screener and saves the qualified symbol list
to data/universe_YYYY-MM-DD.json for use by the trading strategies.
Run this once before market open each day.
"""

import json
import logging
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from screener.universe_builder import UniverseBuilder
import core.alpaca_client as ac
from core.config_loader import get_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("run_screener")

def main(dry_run=False):
    cfg = get_config()

    if not cfg.get("screener", {}).get("enabled", False):
        log.info("Screener disabled in config. Exiting.")
        return

    builder = UniverseBuilder(cfg, alpaca_client=ac)
    universe = builder.get_universe()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if dry_run:
        log.info(f"[DRY RUN] Would save {len(universe)} symbols for {today}")
        print(f"\nQualified universe ({len(universe)} symbols): {', '.join(universe[:20])}{'...' if len(universe)>20 else ''}")
        return

    out_path = BASE_DIR / "data" / f"universe_{today}.json"
    out_path.parent.mkdir(exist_ok=True)

    with open(out_path, "w") as f:
        json.dump({"date": today, "symbols": universe, "count": len(universe)}, f, indent=2)

    log.info(f"Universe saved: {len(universe)} symbols -> {out_path}")
    print(f"\nQualified universe ({len(universe)} symbols): {', '.join(universe[:20])}{'...' if len(universe)>20 else ''}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HawksTrade Nightly Universe Screener")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print universe without saving to file")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
