"""
HawksTrade - Sector Lookup Utility
==================================
Provides GICS sector mapping for stocks. 
Loads from data/sectors.json with a fallback for unknown symbols.
"""

import json
import logging
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent
SECTORS_FILE = BASE_DIR / "config" / "sectors.json"

log = logging.getLogger("core.sector_lookup")

_SECTOR_MAP: Dict[str, str] = {}


def _load_sectors():
    global _SECTOR_MAP
    if not SECTORS_FILE.exists():
        log.warning(f"Sectors file missing: {SECTORS_FILE}. Using fallback only.")
        return
    try:
        with open(SECTORS_FILE, "r") as f:
            _SECTOR_MAP = json.load(f)
        log.debug(f"Loaded {len(_SECTOR_MAP)} sectors from {SECTORS_FILE}")
    except Exception as e:
        log.error(f"Failed to load sectors from {SECTORS_FILE}: {e}")


def get_sector(symbol: str) -> str:
    """Return GICS sector for symbol; unknown symbols get a unique pseudo-sector."""
    if not _SECTOR_MAP:
        _load_sectors()
    return _SECTOR_MAP.get(symbol, f"Unknown_{symbol}")


# Initialize on import
_load_sectors()
