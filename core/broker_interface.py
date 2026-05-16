"""
HawksTrade - Broker Interface Contracts
======================================
Protocol definitions for broker modules that can support HawksTrade runtime
workflows. These contracts document the Alpaca-compatible surface used by the
scanner, executor, risk checks, reports, and broker stop sync.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AccountBroker(Protocol):
    def get_account(self) -> Any:
        ...

    def get_portfolio_value(self) -> float:
        ...

    def get_cash(self) -> float:
        ...

    def get_buying_power(self) -> float:
        ...


@runtime_checkable
class PositionBroker(Protocol):
    def get_all_positions(self) -> list[Any]:
        ...

    def get_position(self, symbol: str) -> Any:
        ...


@runtime_checkable
class OrderBroker(Protocol):
    def get_open_orders(self) -> list[Any]:
        ...

    def get_closed_orders(self, limit: int = 200) -> list[Any]:
        ...

    def place_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        time_in_force: str = "day",
        strategy: str = "unknown",
        asset_class: str | None = None,
        client_order_id: str | None = None,
    ) -> Any:
        ...

    def place_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        time_in_force: str = "gtc",
        strategy: str = "unknown",
        asset_class: str | None = None,
        client_order_id: str | None = None,
    ) -> Any:
        ...

    def place_stop_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_price: float,
        time_in_force: str = "gtc",
        strategy: str = "unknown",
        asset_class: str | None = None,
        client_order_id: str | None = None,
    ) -> Any:
        ...

    def place_stop_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_price: float,
        limit_price: float,
        time_in_force: str = "gtc",
        strategy: str = "unknown",
        asset_class: str | None = None,
        client_order_id: str | None = None,
    ) -> Any:
        ...


@runtime_checkable
class MarketDataBroker(Protocol):
    def get_stock_bars(
        self,
        symbols: list[str],
        timeframe: str = "1Day",
        limit: int = 60,
        start: Any = None,
        end: Any = None,
    ) -> dict[str, list[Any]]:
        ...

    def get_crypto_bars(
        self,
        symbols: list[str],
        timeframe: str = "1Day",
        limit: int = 60,
    ) -> dict[str, list[Any]]:
        ...

    def get_stock_latest_price(self, symbol: str) -> float:
        ...

    def get_crypto_latest_price(self, symbol: str) -> float:
        ...

    def is_market_open(self) -> bool:
        ...


@runtime_checkable
class BrokerInterface(AccountBroker, PositionBroker, OrderBroker, MarketDataBroker, Protocol):
    """Full broker contract expected by current HawksTrade runtime modules."""
