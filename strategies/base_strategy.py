"""
HawksTrade - Base Strategy
===========================
Abstract base class that all strategies must implement.
Ensures consistent interface for the scanner.
"""

from abc import ABC, abstractmethod
from typing import List, Dict


class BaseStrategy(ABC):

    name: str = "base"
    asset_class: str = "stocks"   # "stocks" | "crypto" | "both"

    @staticmethod
    def _missing_symbol_error(exc: Exception) -> bool:
        message = str(exc)
        return isinstance(exc, KeyError) or message.startswith("'No key ") or message.startswith("No key ")

    def _get_symbol_bars(self, bars_data, symbol: str):
        """Safe lookup for bars_data (handles alpaca-py BarSet and dict)."""
        try:
            return bars_data[symbol]
        except Exception as exc:
            if self._missing_symbol_error(exc):
                return None
            raise

    @abstractmethod
    def scan(self, universe: List[str], **kwargs) -> List[Dict]:
        """
        Scan the universe for trading signals.

        Returns a list of signal dicts:
          {
            "symbol":     str,
            "action":     "buy" | "sell",
            "strategy":   str,
            "confidence": float (0-1),
            "reason":     str,
          }
        """
        ...

    @abstractmethod
    def should_exit(self, symbol: str, entry_price: float) -> tuple:
        """
        Check if an open position should be exited by this strategy's rules.
        Returns (should_exit: bool, reason: str).
        """
        ...

    def __repr__(self):
        return f"<Strategy: {self.name} | {self.asset_class}>"
