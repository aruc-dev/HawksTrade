"""
HawksTrade - Alpaca API Client
================================
Central wrapper for all Alpaca REST API calls (stocks + crypto).
Reads mode (paper/live) from config and picks the correct keys from .env.
"""

from __future__ import annotations

import os
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest, CryptoBarsRequest,
    StockLatestQuoteRequest, StockLatestTradeRequest, CryptoLatestOrderbookRequest
)
from alpaca.data.enums import DataFeed, Adjustment
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from core.alpaca_errors import (
    call_alpaca,
    classify_alpaca_error,
    exception_status_code,
    exception_text,
    is_not_found_error,
)
from core.config_loader import get_config

# ── Setup ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent

CFG = get_config()

MODE = CFG["mode"].strip().lower()  # "paper" or "live"
if MODE not in {"paper", "live"}:
    raise ValueError("config mode must be 'paper' or 'live'")


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_local_dotenv_files() -> None:
    load_dotenv(BASE_DIR / "config" / ".env")
    load_dotenv(BASE_DIR / ".env", override=True)


def _shm_max_age_seconds() -> int | None:
    raw = os.getenv("HAWKSTRADE_SHM_MAX_AGE_SECONDS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "HAWKSTRADE_SHM_MAX_AGE_SECONDS must be an integer number of seconds"
        ) from exc
    return value if value > 0 else None


def _validate_shm_secret_file(path: Path) -> bool:
    """Return True when shm secrets are usable; return False unless fail-closed is required."""
    require_shm = _env_truthy("HAWKSTRADE_REQUIRE_SHM")
    if not path.exists():
        if require_shm:
            raise EnvironmentError(
                f"HAWKSTRADE_REQUIRE_SHM=1 but shm secret file is missing: {path}"
            )
        return False
    if path.is_symlink():
        if require_shm:
            raise EnvironmentError(f"shm secret path must not be a symlink: {path}")
        return False
    if not path.is_file():
        if require_shm:
            raise EnvironmentError(f"shm secret path is not a regular file: {path}")
        return False
    if not os.access(path, os.R_OK):
        if require_shm:
            raise PermissionError(f"shm secret file is not readable: {path}")
        return False

    max_age_seconds = _shm_max_age_seconds()
    if max_age_seconds is not None:
        age_seconds = max(0.0, time.time() - path.stat().st_mtime)
        if age_seconds > max_age_seconds:
            if require_shm:
                raise EnvironmentError(
                    f"shm secret file is stale: {path} age={age_seconds:.0f}s "
                    f"max_age={max_age_seconds}s"
                )
            return False
    return True


# Load secrets based on secrets_source setting in config.yaml:
#   "local" — load from config/.env then .env (original behaviour)
#   "shm"   — load from /dev/shm/.hawkstrade.env when present. When
#            HAWKSTRADE_REQUIRE_SHM=1, a missing/unreadable/stale shm file
#            fails closed instead of falling back to local dotenv files.
_raw_secrets_source = CFG.get("secrets_source", "local")
if not isinstance(_raw_secrets_source, str):
    raise ValueError("config secrets_source must be a string: 'local' or 'shm'")
_SECRETS_SOURCE = _raw_secrets_source.strip().lower()
if _SECRETS_SOURCE not in {"local", "shm"}:
    raise ValueError("config secrets_source must be 'local' or 'shm'")

if _SECRETS_SOURCE == "shm":
    _SHM_ENV = Path("/dev/shm/.hawkstrade.env")
    if _validate_shm_secret_file(_SHM_ENV):
        load_dotenv(_SHM_ENV)
    else:
        # Fall back to local dotenv files if shm secrets are missing or unusable.
        # This keeps imports safe on CI and developer machines while still
        # allowing EC2 to prefer shm when the boot-time secret loader ran.
        _SECRETS_SOURCE = "local"
        _load_local_dotenv_files()
else:
    # Default local behaviour: config/.env, then .env (root overrides)
    _load_local_dotenv_files()

log = logging.getLogger("alpaca_client")


def _get_keys():
    if MODE == "paper":
        key    = os.getenv("ALPACA_PAPER_API_KEY")
        secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    else:
        key    = os.getenv("ALPACA_LIVE_API_KEY")
        secret = os.getenv("ALPACA_LIVE_SECRET_KEY")

    if not key or not secret:
        source_hint = (
            "/dev/shm/.hawkstrade.env" if _SECRETS_SOURCE == "shm"
            else ".env or config/.env (see config/.env.example)"
        )
        raise EnvironmentError(
            f"Alpaca API keys not found for mode='{MODE}'. "
            f"Check {source_hint}."
        )
    return key, secret


# ── Clients (lazy singletons) ────────────────────────────────────────────────

_trading_client: Optional[TradingClient] = None
_stock_data_client: Optional[StockHistoricalDataClient] = None
_crypto_data_client: Optional[CryptoHistoricalDataClient] = None
_crypto_price_increment_cache: dict[str, Decimal] = {}


def get_trading_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        key, secret = _get_keys()
        paper = (MODE == "paper")
        _trading_client = TradingClient(key, secret, paper=paper)
        log.info(f"TradingClient initialised (mode={MODE})")
    return _trading_client


def get_stock_data_client() -> StockHistoricalDataClient:
    global _stock_data_client
    if _stock_data_client is None:
        key, secret = _get_keys()
        _stock_data_client = StockHistoricalDataClient(key, secret)
    return _stock_data_client


def get_crypto_data_client() -> CryptoHistoricalDataClient:
    global _crypto_data_client
    if _crypto_data_client is None:
        key, secret = _get_keys()
        _crypto_data_client = CryptoHistoricalDataClient(key, secret)
    return _crypto_data_client


# ── Account ──────────────────────────────────────────────────────────────────

def get_account():
    return call_alpaca("trading.get_account", lambda: get_trading_client().get_account())


def get_portfolio_value() -> float:
    account = get_account()
    return float(account.portfolio_value)


def get_cash() -> float:
    account = get_account()
    return float(account.cash)


def get_buying_power() -> float:
    account = get_account()
    return float(account.buying_power)


# ── Positions ────────────────────────────────────────────────────────────────

def get_all_positions():
    return call_alpaca("trading.get_all_positions", lambda: get_trading_client().get_all_positions())


def _exception_status_code(exc: Exception) -> int | None:
    """Best-effort HTTP status extraction across Alpaca and requests errors."""
    return exception_status_code(exc)


def _exception_text(exc: Exception) -> str:
    return exception_text(exc).lower()


def _is_position_not_found_error(exc: Exception) -> bool:
    return is_not_found_error(exc)


def _is_duplicate_client_order_id_error(exc: Exception) -> bool:
    text = _exception_text(exc)
    return (
        ("client_order_id" in text or "client order id" in text)
        and ("duplicate" in text or "already" in text or "unique" in text)
    )


def _submit_order(client, req, operation: str, client_order_id: Optional[str] = None):
    if not client_order_id:
        return client.submit_order(req)
    try:
        return call_alpaca(operation, lambda: client.submit_order(req))
    except Exception as exc:
        if not _is_duplicate_client_order_id_error(exc):
            raise
        log.warning(
            "Order submit reported duplicate client_order_id; loading existing broker order: %s",
            client_order_id,
        )
        return call_alpaca(
            f"trading.get_order_by_client_id[{client_order_id}]",
            lambda: client.get_order_by_client_id(client_order_id),
        )


def get_position(symbol: str):
    client = get_trading_client()
    for candidate in _symbol_lookup_variants(symbol):
        try:
            return call_alpaca(
                f"trading.get_open_position[{candidate}]",
                lambda candidate=candidate: client.get_open_position(candidate),
            )
        except Exception as e:
            if _is_position_not_found_error(e):
                log.debug(f"No open position for {candidate}: {e}")
                continue
            raise
    return None


# ── Orders ───────────────────────────────────────────────────────────────────

def place_market_order(
    symbol: str,
    qty: float,
    side: str,
    time_in_force: str = "day",
    strategy: str = "unknown",
    client_order_id: Optional[str] = None,
):
    """Place a market order. side = 'buy' or 'sell'."""
    side = side.lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")
    client = get_trading_client()
    req = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC,
        client_order_id=client_order_id,
    )
    # Attach strategy for backtest mock visibility (bypass Pydantic strictness)
    try:
        object.__setattr__(req, "strategy", strategy)
    except Exception:
        pass

    order = _submit_order(
        client,
        req,
        f"trading.submit_market_order[{side}:{symbol}]",
        client_order_id=client_order_id,
    )
    # Store strategy in mock-friendly way for backtests
    if hasattr(order, "__setitem__"): order["strategy"] = strategy
    elif hasattr(order, "strategy"): order.strategy = strategy
    
    order_id = order.id if hasattr(order, "id") else order.get("order_id")
    log.info(
        f"Market order submitted: {side} {qty} {symbol} | strategy={strategy} "
        f"| id={order_id} | client_order_id={client_order_id or ''}"
    )
    return order


def place_limit_order(symbol: str, qty: float, side: str, limit_price: float,
                      time_in_force: str = "gtc", strategy: str = "unknown",
                      asset_class: Optional[str] = None,
                      client_order_id: Optional[str] = None):
    """Place a limit order."""
    side = side.lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")
    client = get_trading_client()
    normalized_limit_price = normalize_limit_price(symbol, limit_price, asset_class=asset_class)
    normalized_time_in_force = normalize_time_in_force(
        symbol,
        qty,
        time_in_force,
        asset_class=asset_class,
    )
    req = LimitOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        limit_price=normalized_limit_price,
        time_in_force=normalized_time_in_force,
        client_order_id=client_order_id,
    )
    # Attach strategy for backtest mock visibility (bypass Pydantic strictness)
    try:
        object.__setattr__(req, "strategy", strategy)
    except Exception:
        pass

    order = _submit_order(
        client,
        req,
        f"trading.submit_limit_order[{side}:{symbol}]",
        client_order_id=client_order_id,
    )
    # Store strategy in mock-friendly way for backtests
    if hasattr(order, "__setitem__"): order["strategy"] = strategy
    elif hasattr(order, "strategy"): order.strategy = strategy

    order_id = order.id if hasattr(order, "id") else order.get("order_id")
    log.info(
        f"Limit order submitted: {side} {qty} {symbol} @ {normalized_limit_price} "
        f"| strategy={strategy} | id={order_id} | client_order_id={client_order_id or ''}"
    )
    return order


def cancel_order(order_id: str):
    call_alpaca(
        f"trading.cancel_order[{order_id}]",
        lambda: get_trading_client().cancel_order_by_id(order_id),
    )
    log.info(f"Order cancelled: {order_id}")


def get_open_orders():
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    return call_alpaca("trading.get_open_orders", lambda: get_trading_client().get_orders(filter=req))


def get_closed_orders(limit: int = 200):
    req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=limit, nested=True)
    return call_alpaca("trading.get_closed_orders", lambda: get_trading_client().get_orders(filter=req))


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def to_crypto_pair_symbol(symbol: str) -> str:
    """Return Alpaca crypto data symbol format, e.g. DOGEUSD -> DOGE/USD."""
    cleaned = symbol.strip().upper()
    if "/" in cleaned:
        return cleaned
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if cleaned.endswith(quote) and len(cleaned) > len(quote):
            return f"{cleaned[:-len(quote)]}/{quote}"
    return cleaned


def normalize_limit_price(symbol: str, limit_price: float, asset_class: Optional[str] = None) -> float:
    """
    Normalize limit prices to Alpaca increment rules.

    Stocks priced at or above $1 may use two decimals, while sub-dollar
    stocks may use four. Crypto pairs use the asset price_increment when
    Alpaca exposes it, falling back to 9 decimal places.
    """
    asset_class = (asset_class or "").lower()
    is_crypto = asset_class == "crypto" or "/" in symbol
    if is_crypto:
        increment = _get_crypto_price_increment(symbol)
        if increment:
            return _round_price_to_increment(limit_price, increment)
        return _round_price(limit_price, 9)
    if float(limit_price) >= 1:
        return _round_price(limit_price, 2)
    return _round_price(limit_price, 4)


def normalize_time_in_force(
    symbol: str,
    qty: float,
    time_in_force: str,
    asset_class: Optional[str] = None,
) -> TimeInForce:
    """Return Alpaca-compatible time-in-force for the requested order."""
    time_in_force = (time_in_force or "gtc").lower()
    requested = TimeInForce.GTC if time_in_force == "gtc" else TimeInForce.DAY
    if _is_fractional_stock_order(symbol, qty, asset_class):
        return TimeInForce.DAY
    return requested


def _is_fractional_stock_order(symbol: str, qty: float, asset_class: Optional[str] = None) -> bool:
    asset_class = (asset_class or "").lower()
    is_crypto = asset_class == "crypto" or "/" in symbol
    if is_crypto:
        return False
    quantity = Decimal(str(qty))
    return quantity != quantity.to_integral_value()


def _get_crypto_price_increment(symbol: str) -> Optional[Decimal]:
    pair_symbol = to_crypto_pair_symbol(symbol)
    if pair_symbol in _crypto_price_increment_cache:
        return _crypto_price_increment_cache[pair_symbol]
    try:
        asset = call_alpaca(
            f"trading.get_asset[{pair_symbol}]",
            lambda: get_trading_client().get_asset(pair_symbol),
        )
        price_increment = getattr(asset, "price_increment", None)
        if price_increment:
            increment = Decimal(str(price_increment))
            if increment > 0:
                _crypto_price_increment_cache[pair_symbol] = increment
                return increment
    except Exception as e:
        log.debug(f"Could not load crypto price increment for {pair_symbol}: {e}")
    return None


def _round_price(value: float, places: int) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _round_price_to_increment(value: float, increment: Decimal) -> float:
    units = Decimal(str(value)) / increment
    rounded_units = units.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(rounded_units * increment)


def _symbol_lookup_variants(symbol: str) -> list:
    normalized = normalize_symbol(symbol)
    variants = [normalized]
    if normalized != symbol:
        variants.append(symbol)
    paired = to_crypto_pair_symbol(normalized)
    if paired not in variants:
        variants.append(paired)
    return variants


def _lookback_delta(timeframe: str, limit: int, market: str) -> timedelta:
    """Return enough calendar lookback for strategies to receive recent bars."""
    multiplier = 3 if market == "stock" else 2
    if timeframe == "1Min":
        return timedelta(minutes=max(limit * multiplier, 60))
    if timeframe == "5Min":
        return timedelta(minutes=max(limit * 5 * multiplier, 180))
    if timeframe == "15Min":
        return timedelta(minutes=max(limit * 15 * multiplier, 360))
    if timeframe == "1Hour":
        return timedelta(hours=max(limit * multiplier, 48))
    if timeframe == "4Hour":
        return timedelta(hours=max(limit * 4 * multiplier, 96))
    return timedelta(days=max(limit * multiplier, 30))


# ── Market Data: Stocks ──────────────────────────────────────────────────────

def get_stock_bars(symbols: list, timeframe: str = "1Day", limit: int = 60):
    """
    Fetch OHLCV bars for a list of stock symbols. Always split-adjusted.
    Returns a plain dict: { symbol: [Bar, Bar, ...] }.
    Chunks large requests to avoid Alpaca's 10,000-bar-per-call limit.
    """
    if not symbols:
        return {}

    tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1Day": TimeFrame.Day,
    }
    tf = tf_map.get(timeframe, TimeFrame.Day)
    
    # Chunking logic: Alpaca limits total bars per response (usually 10k).
    # We chunk symbols to stay safely below that limit given the requested limit per symbol.
    # 10 symbols per chunk is safe even if each has 800+ bars in the date range.
    chunk_size = 10
    
    all_bars = {}
    feed = DataFeed.SIP if MODE == "live" else DataFeed.IEX
    
    for i in range(0, len(symbols), chunk_size):
        batch = symbols[i : i + chunk_size]
        end = datetime.now(timezone.utc)
        start = end - _lookback_delta(timeframe, limit, market="stock")
        
        # Use Alpaca's maximum allowed limit per request to avoid truncation.
        # We handle symbols in chunks anyway to stay below this 10k limit.
        request_limit = 10000
        
        req = StockBarsRequest(
            symbol_or_symbols=batch,
            timeframe=tf,
            start=start,
            end=end,
            feed=feed,
            adjustment=Adjustment.ALL,
            limit=request_limit,
        )
        
        batch_res = call_alpaca(
            f"stock_data.get_stock_bars[{len(batch)}:{timeframe}]",
            lambda: get_stock_data_client().get_stock_bars(req),
        )
        
        # Merge BarSet.data (dict) into our master result
        if hasattr(batch_res, "data"):
            all_bars.update(batch_res.data)
        elif isinstance(batch_res, dict):
            all_bars.update(batch_res)

    return all_bars


def get_stock_latest_quote(symbol: str):
    req = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
    data = call_alpaca(
        f"stock_data.get_latest_quote[{symbol}]",
        lambda: get_stock_data_client().get_stock_latest_quote(req),
    )
    return data.get(symbol)


def get_stock_latest_trade(symbol: str):
    req = StockLatestTradeRequest(symbol_or_symbols=[symbol])
    data = call_alpaca(
        f"stock_data.get_latest_trade[{symbol}]",
        lambda: get_stock_data_client().get_stock_latest_trade(req),
    )
    return data.get(symbol)


def get_stock_latest_price(symbol: str) -> float:
    quote = get_stock_latest_quote(symbol)
    if quote:
        bid = float(getattr(quote, "bid_price", 0) or 0)
        ask = float(getattr(quote, "ask_price", 0) or 0)
        if bid > 0 and ask > 0:
            return (ask + bid) / 2

    try:
        trade = get_stock_latest_trade(symbol)
        price = float(getattr(trade, "price", 0) or 0) if trade else 0.0
        if price > 0:
            return price
    except Exception as e:
        log.warning(f"Could not get latest trade for {symbol}: {e}")

    if quote:
        if bid > 0:
            return bid
        if ask > 0:
            return ask
    return 0.0


# ── Market Data: Crypto ──────────────────────────────────────────────────────

def get_crypto_bars(symbols: list, timeframe: str = "1Day", limit: int = 60):
    """
    Fetch OHLCV bars for a list of crypto pairs (e.g. ['BTC/USD']).
    Returns a plain dict: { symbol: [Bar, Bar, ...] }.
    Chunks requests to avoid Alpaca limits.
    """
    if not symbols:
        return {}

    request_symbols = [to_crypto_pair_symbol(symbol) for symbol in symbols]
    tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "4Hour": TimeFrame(4, TimeFrameUnit.Hour),
        "1Day": TimeFrame.Day,
    }
    tf = tf_map.get(timeframe, TimeFrame.Day)
    
    chunk_size = 10
    
    all_bars = {}
    
    for i in range(0, len(request_symbols), chunk_size):
        batch = request_symbols[i : i + chunk_size]
        end = datetime.now(timezone.utc)
        start = end - _lookback_delta(timeframe, limit, market="crypto")
        
        # Use safe high limit
        request_limit = 10000
        
        req = CryptoBarsRequest(
            symbol_or_symbols=batch,
            timeframe=tf,
            start=start,
            end=end,
            limit=request_limit,
        )
        batch_res = call_alpaca(
            f"crypto_data.get_crypto_bars[{len(batch)}:{timeframe}]",
            lambda: get_crypto_data_client().get_crypto_bars(req),
        )
        
        if hasattr(batch_res, "data"):
            all_bars.update(batch_res.data)
        elif isinstance(batch_res, dict):
            all_bars.update(batch_res)
            
    return all_bars


def get_crypto_latest_price(symbol: str) -> float:
    """symbol e.g. 'BTC/USD'"""
    request_symbol = to_crypto_pair_symbol(symbol)
    try:
        req = CryptoLatestOrderbookRequest(symbol_or_symbols=[request_symbol])
        data = call_alpaca(
            f"crypto_data.get_latest_orderbook[{request_symbol}]",
            lambda: get_crypto_data_client().get_crypto_latest_orderbook(req),
        )
        ob = data.get(request_symbol) or data.get(symbol)
        if ob and ob.bids and ob.asks:
            best_bid = float(ob.bids[0].price)
            best_ask = float(ob.asks[0].price)
            return (best_bid + best_ask) / 2
    except Exception as e:
        log.warning(f"Could not get crypto price for {symbol}: {e}")
    return 0.0


# ── Market Hours ─────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    clock = call_alpaca("trading.get_clock", lambda: get_trading_client().get_clock())
    return clock.is_open


# ── Asset Discovery ─────────────────────────────────────────────────────────

def get_all_tradable_assets(asset_class: str = "us_equity") -> list:
    """
    Returns a list of all active, tradable symbols for the given asset class.
    Filters out OTC symbols (containing '.') and long tickers (>5 chars).
    Used by the dynamic universe screener.
    """
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus
    asset_class_map = {
        "us_equity": AssetClass.US_EQUITY,
        "crypto": AssetClass.CRYPTO,
    }
    client = get_trading_client()
    request = GetAssetsRequest(
        asset_class=asset_class_map.get(asset_class, AssetClass.US_EQUITY),
        status=AssetStatus.ACTIVE,
    )
    assets = call_alpaca(
        f"trading.get_all_assets[{asset_class}]",
        lambda: client.get_all_assets(request),
    )
    symbols = [
        a.symbol for a in assets
        if a.tradable
        and a.status.value == "active"
        and "." not in a.symbol   # exclude OTC/foreign (dotted symbols like BRK.B)
        and len(a.symbol) <= 5     # exclude most ETNs/structured products
    ]
    return sorted(set(symbols))
