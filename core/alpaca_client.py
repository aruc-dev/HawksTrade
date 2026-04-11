"""
HawksTrade - Alpaca API Client
================================
Central wrapper for all Alpaca REST API calls (stocks + crypto).
Reads mode (paper/live) from config and picks the correct keys from .env.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest, CryptoBarsRequest,
    StockLatestQuoteRequest, CryptoLatestOrderbookRequest
)
from alpaca.data.enums import DataFeed, Adjustment
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# ── Setup ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / "config" / ".env")
load_dotenv(BASE_DIR / ".env", override=True)

with open(BASE_DIR / "config" / "config.yaml") as f:
    CFG = yaml.safe_load(f)

MODE = CFG["mode"].strip().lower()  # "paper" or "live"
if MODE not in {"paper", "live"}:
    raise ValueError("config mode must be 'paper' or 'live'")

log = logging.getLogger("alpaca_client")


def _get_keys():
    if MODE == "paper":
        key    = os.getenv("ALPACA_PAPER_API_KEY")
        secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    else:
        key    = os.getenv("ALPACA_LIVE_API_KEY")
        secret = os.getenv("ALPACA_LIVE_SECRET_KEY")

    if not key or not secret:
        raise EnvironmentError(
            f"Alpaca API keys not found for mode='{MODE}'. "
            f"Please fill in .env or config/.env (see config/.env.example)."
        )
    return key, secret


# ── Clients (lazy singletons) ────────────────────────────────────────────────

_trading_client: Optional[TradingClient] = None
_stock_data_client: Optional[StockHistoricalDataClient] = None
_crypto_data_client: Optional[CryptoHistoricalDataClient] = None


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
    return get_trading_client().get_account()


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
    return get_trading_client().get_all_positions()


def get_position(symbol: str):
    client = get_trading_client()
    for candidate in _symbol_lookup_variants(symbol):
        try:
            return client.get_open_position(candidate)
        except Exception:
            continue
    return None


# ── Orders ───────────────────────────────────────────────────────────────────

def place_market_order(symbol: str, qty: float, side: str, time_in_force: str = "day"):
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
    )
    order = client.submit_order(req)
    log.info(f"Market order submitted: {side} {qty} {symbol} | id={order.id}")
    return order


def place_limit_order(symbol: str, qty: float, side: str, limit_price: float,
                      time_in_force: str = "gtc"):
    """Place a limit order."""
    side = side.lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")
    client = get_trading_client()
    req = LimitOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        limit_price=round(limit_price, 4),
        time_in_force=TimeInForce.GTC if time_in_force == "gtc" else TimeInForce.DAY,
    )
    order = client.submit_order(req)
    log.info(f"Limit order submitted: {side} {qty} {symbol} @ {limit_price} | id={order.id}")
    return order


def cancel_order(order_id: str):
    get_trading_client().cancel_order_by_id(order_id)
    log.info(f"Order cancelled: {order_id}")


def get_open_orders():
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    return get_trading_client().get_orders(filter=req)


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def _symbol_lookup_variants(symbol: str) -> list:
    normalized = normalize_symbol(symbol)
    variants = [symbol]
    if normalized != symbol:
        variants.append(normalized)
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
    """Fetch OHLCV bars for a list of stock symbols. Always split-adjusted."""
    tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1Day": TimeFrame.Day,
    }
    tf = tf_map.get(timeframe, TimeFrame.Day)
    end = datetime.now(timezone.utc)
    start = end - _lookback_delta(timeframe, limit, market="stock")
    
    # Use SIP feed for live, IEX for paper (default)
    feed = DataFeed.SIP if MODE == "live" else DataFeed.IEX
    
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=tf,
        start=start,
        end=end,
        feed=feed,
        adjustment=Adjustment.ALL
    )
    return get_stock_data_client().get_stock_bars(req)


def get_stock_latest_quote(symbol: str):
    req = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
    data = get_stock_data_client().get_stock_latest_quote(req)
    return data.get(symbol)


def get_stock_latest_price(symbol: str) -> float:
    quote = get_stock_latest_quote(symbol)
    if quote:
        return float((quote.ask_price + quote.bid_price) / 2)
    return 0.0


# ── Market Data: Crypto ──────────────────────────────────────────────────────

def get_crypto_bars(symbols: list, timeframe: str = "1Day", limit: int = 60):
    """Fetch OHLCV bars for a list of crypto pairs (e.g. ['BTC/USD'])."""
    tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "4Hour": TimeFrame(4, TimeFrameUnit.Hour),
        "1Day": TimeFrame.Day,
    }
    tf = tf_map.get(timeframe, TimeFrame.Day)
    end = datetime.now(timezone.utc)
    start = end - _lookback_delta(timeframe, limit, market="crypto")
    req = CryptoBarsRequest(symbol_or_symbols=symbols, timeframe=tf, start=start, end=end)
    return get_crypto_data_client().get_crypto_bars(req)


def get_crypto_latest_price(symbol: str) -> float:
    """symbol e.g. 'BTC/USD'"""
    try:
        req = CryptoLatestOrderbookRequest(symbol_or_symbols=[symbol])
        data = get_crypto_data_client().get_crypto_latest_orderbook(req)
        ob = data.get(symbol)
        if ob and ob.bids and ob.asks:
            best_bid = float(ob.bids[0].price)
            best_ask = float(ob.asks[0].price)
            return (best_bid + best_ask) / 2
    except Exception as e:
        log.warning(f"Could not get crypto price for {symbol}: {e}")
    return 0.0


# ── Market Hours ─────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    clock = get_trading_client().get_clock()
    return clock.is_open
