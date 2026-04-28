"""
HawksTrade - Historical Backtest Simulation
===========================================
Backtests the current strategies and risk management settings
over a specified period with a custom starting fund.
"""

from __future__ import annotations

import os
import sys
import logging
import argparse
import tempfile
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core import alpaca_client as ac
from core import risk_manager as rm
from core import order_executor as oe
from core.config_loader import get_config
from core.exit_policy import (
    VALID_MOMENTUM_EXIT_POLICIES,
    normalize_momentum_exit_policy,
    should_exit_for_hold,
    update_high_water_price,
)
import strategies.momentum as momentum_module
import strategies.rsi_reversion as rsi_module
import strategies.gap_up as gap_up_module
import strategies.ma_crossover as ma_crossover_module
import strategies.range_breakout as range_breakout_module
from strategies.momentum import MomentumStrategy
from strategies.rsi_reversion import RSIReversionStrategy
from strategies.gap_up import GapUpStrategy
from strategies.ma_crossover import MACrossoverStrategy
from strategies.range_breakout import RangeBreakoutStrategy
from screener.universe_builder import UniverseBuilder

# Extended pool of ~110 high-liquidity symbols covering major sectors
EXTENDED_POOL = [
    # Tech
    "CRM", "ORCL", "ADBE", "INTC", "QCOM", "TXN", "AVGO", "MU", "AMAT", "LRCX",
    "NOW", "SNOW", "PANW", "CRWD", "ZS", "NET", "DDOG", "MDB", "UBER",
    # AI Infrastructure (2025/2026 leaders)
    "SMCI", "ARM", "ANET", "MRVL",
    # Defence / Aerospace (tariff-immune)
    "LMT", "RTX", "NOC", "GD", "BA",
    # Energy (commodity surge)
    "XOM", "CVX", "COP", "SLB", "HAL", "OXY",
    # Healthcare / Biotech
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "BMY", "GILD", "AMGN", "REGN",
    "MDT", "ISRG", "ELV",
    # Financials (resilience)
    "JPM", "GS", "MS", "WFC", "C", "BLK", "SCHW", "AXP", "V", "MA",
    "PYPL", "SQ",
    # Consumer staples (defensive)
    "PG", "KO", "PEP", "WMT", "COST",
    # Consumer discretionary
    "AMZN", "HD", "TGT", "NKE", "SBUX", "MCD", "DIS", "CMCSA",
    # Industrials
    "LLY", "CAT", "DE", "HON", "GE", "UPS", "FDX",
    # Real estate / utilities / telecom
    "AMT", "PLD", "CCI", "NEE", "DUK", "SO", "T", "VZ", "TMUS",
    # International ADRs
    "TSM", "ASML", "SAP", "TM",
    # High-momentum / popular
    "MSTR", "HOOD", "BABA", "JD", "PDD", "SHOP", "SPOT",
    "RBLX", "SNAP", "PINS", "ABNB", "DASH",
]

# ── Setup Logging ─────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backtest")

# ── Lightweight bar object (replaces MagicMock for speed) ────────────────────

class SimpleBar:
    __slots__ = ('open', 'high', 'low', 'close', 'volume', 'timestamp')
    def __init__(self, open_price, high_price, low_price, close_price, volume, timestamp):
        self.open = open_price; self.high = high_price; self.low = low_price
        self.close = close_price; self.volume = volume; self.timestamp = timestamp

def _make_bar_fetcher(sim: "BacktestSimulator"):
    """Return a mock_get_bars function bound to the given simulator.

    Extracting this factory to module level keeps the nesting depth inside
    run_backtest() within CPython's 20-block compile-time limit.
    """
    class MockBarSet:
        def __init__(self): self.data = {}; self.df = pd.DataFrame()
        def __getitem__(self, key): return self.data.get(key)

    def mock_get_bars(symbols, timeframe="1Day", limit=60):
        res = MockBarSet(); dfs = []
        for s in symbols:
            if s in sim.historical_data:
                df = sim.historical_data[s]
                mask = df.index <= sim.current_date
                hist_df = df[mask].tail(limit)
                bars_list = [
                    SimpleBar(
                        open_price=float(row["open"]),
                        high_price=float(row["high"]),
                        low_price=float(row["low"]),
                        close_price=float(row["close"]),
                        volume=float(row["volume"]),
                        timestamp=idx,
                    )
                    for idx, row in hist_df.iterrows()
                ]
                res.data[s] = bars_list
                temp_df = hist_df.copy()
                temp_df.index = pd.MultiIndex.from_product(
                    [[s], temp_df.index], names=["symbol", "timestamp"]
                )
                dfs.append(temp_df)
        if dfs:
            res.df = pd.concat(dfs)
        return res

    return mock_get_bars


# ── Simulation State ─────────────────────────────────────────────────────────

class BacktestSimulator:
    def __init__(self, initial_fund=10000.0):
        self.portfolio_value = initial_fund
        self.cash = initial_fund
        self.positions = {}  # symbol -> {qty, entry_price, entry_date, asset_class, strategy}
        self.trades_log = []
        self.current_date = None
        self.historical_data = {}  # symbol -> df
        self.equity_curve = []

    def get_portfolio_value(self):
        pos_value = 0
        for symbol, pos in self.positions.items():
            price = self.get_current_price(symbol)
            pos_value += pos["qty"] * price
        return self.cash + pos_value

    def get_cash(self):
        return self.cash

    def get_all_positions(self):
        pos_list = []
        for symbol, p in self.positions.items():
            mock_pos = MagicMock()
            mock_pos.symbol = symbol
            mock_pos.qty = p["qty"]
            mock_pos.avg_entry_price = p["entry_price"]
            mock_pos.market_value = p["qty"] * self.get_current_price(symbol)
            pos_list.append(mock_pos)
        return pos_list

    def get_position(self, symbol):
        if symbol in self.positions:
            p = self.positions[symbol]
            mock_pos = MagicMock()
            mock_pos.symbol = symbol
            mock_pos.qty = p["qty"]
            mock_pos.avg_entry_price = p["entry_price"]
            return mock_pos
        return None

    def get_open_trades_for_backtest(self):
        """Return open positions in the shape expected by get_open_trades() / get_trade_age_days()."""
        trades = []
        for symbol, pos in self.positions.items():
            trades.append({
                "symbol": symbol,
                "strategy": pos.get("strategy", "unknown"),
                "asset_class": pos.get("asset_class", "stock"),
                "entry_price": pos["entry_price"],
                "side": "buy",
                "status": "open",
                "timestamp": pos["entry_date"].isoformat() if hasattr(pos["entry_date"], "isoformat") else str(pos["entry_date"]),
            })
        return trades

    def get_trade_age_days(self, symbol):
        """Return trade age in days. Stocks use business days; crypto uses calendar days.

        Always normalises to ``datetime.date`` before arithmetic so that
        timezone-aware ``datetime`` objects and plain ``date`` objects are
        handled consistently (``np.busday_count`` requires date-like inputs,
        not full datetimes).
        """
        if symbol not in self.positions:
            return 0.0
        pos = self.positions[symbol]
        entry_d = pos["entry_date"]
        curr_d  = self.current_date
        # Unconditionally extract .date() — works for datetime, date, and
        # timezone-aware datetime alike; raises early if something unexpected arrives.
        entry_date = entry_d.date() if isinstance(entry_d, datetime) else entry_d
        curr_date  = curr_d.date()  if isinstance(curr_d,  datetime) else curr_d
        if pos.get("asset_class") == "crypto":
            return float(max((curr_date - entry_date).days, 0))
        return float(np.busday_count(entry_date, curr_date))

    def get_current_price(self, symbol):
        df = self.historical_data.get(symbol)
        if df is None or df.empty: return 0.0
        mask = df.index <= self.current_date
        valid_bars = df[mask]
        if valid_bars.empty: return 0.0
        return float(valid_bars.iloc[-1]["close"])

    def submit_order(self, req):
        symbol = req.symbol
        side = req.side.value.lower()
        qty = float(req.qty)
        price = self.get_current_price(symbol)
        
        # Pull strategy from the request object (Alpaca SDK Request mock)
        strategy = getattr(req, "strategy", "unknown")

        if side == "buy":
            cost = qty * price
            if cost > self.cash: return MagicMock(id="failed")
            self.cash -= cost
            if symbol in self.positions:
                old_pos = self.positions[symbol]
                total_qty = old_pos["qty"] + qty
                avg_price = (old_pos["qty"] * old_pos["entry_price"] + cost) / total_qty
                self.positions[symbol]["qty"] = total_qty
                self.positions[symbol]["entry_price"] = avg_price
                self.positions[symbol]["high_water_price"] = max(
                    old_pos.get("high_water_price", old_pos["entry_price"]),
                    price,
                )
            else:
                self.positions[symbol] = {
                    "qty": qty, 
                    "entry_price": price, 
                    "high_water_price": price,
                    "entry_date": self.current_date, 
                    "asset_class": "stock" if "/" not in symbol else "crypto", 
                    "strategy": strategy
                }
        else:
            if symbol not in self.positions: return MagicMock(id="failed")
            pos = self.positions[symbol]
            sell_qty = min(qty, pos["qty"])
            proceeds = sell_qty * price
            self.cash += proceeds
            pnl = (price - pos["entry_price"]) * sell_qty
            pnl_pct = (price / pos["entry_price"]) - 1
            self.trades_log.append({
                "symbol": symbol, "entry_date": pos["entry_date"], "exit_date": self.current_date,
                "entry_price": pos["entry_price"], "exit_price": price, "qty": sell_qty,
                "pnl": pnl, "pnl_pct": pnl_pct, "strategy": pos.get("strategy", "unknown")
            })
            if sell_qty >= pos["qty"]: del self.positions[symbol]
            else: self.positions[symbol]["qty"] -= sell_qty
        
        # Return a mock order object
        order = MagicMock()
        order.id = "order_id"
        order.order_id = "order_id"
        order.status = "filled"
        order.strategy = strategy
        return order

# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_all_data(symbols, start_date, end_date):
    log.info(f"Fetching historical data for {len(symbols)} symbols...")
    data = {}
    from alpaca.data.enums import Adjustment
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    # Separate stock vs crypto symbols
    stock_symbols = [s for s in symbols if "/" not in s]
    crypto_symbols = [s for s in symbols if "/" in s]

    # Batch-fetch stocks in groups of 50 (much faster than 1-at-a-time)
    BATCH = 50
    for i in range(0, len(stock_symbols), BATCH):
        batch = stock_symbols[i:i+BATCH]
        try:
            req = StockBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame.Day, start=start_date, end=end_date, adjustment=Adjustment.ALL)
            bars = ac.get_stock_data_client().get_stock_bars(req)
            for s in batch:
                if s in bars.data:
                    data[s] = bars.df.loc[s]
        except Exception as e:
            log.error(f"Batch stock fetch failed ({i}-{i+BATCH}): {e}")
            # Fallback: fetch individually
            for s in batch:
                try:
                    req = StockBarsRequest(symbol_or_symbols=[s], timeframe=TimeFrame.Day, start=start_date, end=end_date, adjustment=Adjustment.ALL)
                    bars = ac.get_stock_data_client().get_stock_bars(req)
                    if s in bars.data: data[s] = bars.df.loc[s]
                except Exception as e2: log.error(f"Failed to fetch {s}: {e2}")
        log.info(f"  Fetched stocks batch {i+1}-{min(i+BATCH, len(stock_symbols))} of {len(stock_symbols)}")

    # Fetch crypto individually (usually only 6 symbols)
    for s in crypto_symbols:
        try:
            req = CryptoBarsRequest(symbol_or_symbols=[s], timeframe=TimeFrame.Day, start=start_date, end=end_date)
            bars = ac.get_crypto_data_client().get_crypto_bars(req)
            if s in bars.data: data[s] = bars.df.loc[s]
        except Exception as e: log.error(f"Failed to fetch {s}: {e}")

    log.info(f"Fetched data for {len(data)} of {len(symbols)} symbols")
    return data

# ── Main Backtest Loop ────────────────────────────────────────────────────────

STRATEGY_MODULES = {
    "momentum": momentum_module,
    "rsi_reversion": rsi_module,
    "gap_up": gap_up_module,
    "ma_crossover": ma_crossover_module,
    "range_breakout": range_breakout_module,
}


def _coerce_override_value(raw: str):
    """Convert CLI override strings to bool/int/float where possible."""
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"none", "null"}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _apply_override(cfg: dict, assignment: str) -> None:
    """Apply a dotted-path config assignment, e.g. strategies.momentum.top_n=3."""
    if "=" not in assignment:
        raise ValueError(f"Invalid override '{assignment}'. Use key.path=value.")
    key, raw_value = assignment.split("=", 1)
    parts = [p for p in key.split(".") if p]
    if not parts:
        raise ValueError(f"Invalid override '{assignment}'. Use key.path=value.")

    target = cfg
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = _coerce_override_value(raw_value)


def _apply_runtime_strategy_config(cfg: dict) -> None:
    """Keep strategy module globals in sync with backtest-time config overrides."""
    for name, module in STRATEGY_MODULES.items():
        if name in cfg.get("strategies", {}):
            module.SCFG = cfg["strategies"][name]
    gap_up_module.INTRADAY_ON = cfg.get("intraday", {}).get("enabled", False)


def _patch_runtime_risk_config(stack: contextlib.ExitStack, cfg: dict) -> None:
    """Apply backtest-only trading config overrides to risk manager globals."""
    stack.enter_context(patch("core.risk_manager.T", cfg["trading"]))
    stack.enter_context(patch(
        "core.risk_manager.INTRADAY_ENABLED",
        cfg.get("intraday", {}).get("enabled", False),
    ))


def _enabled_strategy_names(cfg: dict) -> list:
    return [
        name
        for name, strat_cfg in cfg.get("strategies", {}).items()
        if strat_cfg.get("enabled", False)
    ]


def _compute_max_drawdown(df_curve: pd.DataFrame) -> float:
    if df_curve.empty or "value" not in df_curve:
        return 0.0
    values = df_curve["value"].astype(float)
    running_max = values.cummax()
    drawdowns = (values / running_max) - 1.0
    return float(drawdowns.min()) if not drawdowns.empty else 0.0

def run_backtest(
    days=365,
    initial_fund=10000.0,
    output_file=None,
    graph_file=None,
    end_date=None,
    exit_policy=None,
    use_screener=None,
    enabled_strategies=None,
    config_overrides=None,
):
    cfg = get_config()

    if config_overrides:
        for assignment in config_overrides:
            _apply_override(cfg, assignment)

    if exit_policy:
        cfg["strategies"]["momentum"]["exit_policy"] = normalize_momentum_exit_policy(exit_policy)
    else:
        cfg["strategies"]["momentum"]["exit_policy"] = normalize_momentum_exit_policy(
            cfg["strategies"]["momentum"].get("exit_policy")
        )

    if enabled_strategies:
        selected = set(enabled_strategies)
        unknown = selected - set(cfg.get("strategies", {}))
        if unknown:
            raise ValueError(f"Unknown strategy name(s): {', '.join(sorted(unknown))}")
        for name, strategy_cfg in cfg["strategies"].items():
            strategy_cfg["enabled"] = name in selected

    _apply_runtime_strategy_config(cfg)

    stock_strategy_enabled = any(
        strat_cfg.get("enabled", False) and strat_cfg.get("asset_class") in {"stocks", "both"}
        for strat_cfg in cfg.get("strategies", {}).values()
    )
    crypto_strategy_enabled = any(
        strat_cfg.get("enabled", False) and strat_cfg.get("asset_class") in {"crypto", "both"}
        for strat_cfg in cfg.get("strategies", {}).values()
    )

    screener_enabled = cfg.get("screener", {}).get("enabled", False) if use_screener is None else bool(use_screener)
    screener_enabled = screener_enabled and stock_strategy_enabled

    # Build extended symbol pool (legacy + extended, deduped)
    # When the screener is disabled, backtest the fixed configured universe only.
    all_stock_symbols = []
    if stock_strategy_enabled:
        all_stock_symbols = list(dict.fromkeys(
            cfg["stocks"]["scan_universe"] + (EXTENDED_POOL if screener_enabled else [])
        ))
    crypto_symbols = cfg["crypto"]["scan_universe"] if crypto_strategy_enabled else []
    symbols = all_stock_symbols + crypto_symbols
    
    if end_date:
        # Expected format: MM/DD/YYYY (e.g. 12/31/2025)
        try:
            end_dt = datetime.strptime(end_date, "%m/%d/%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            log.error(f"Invalid date format: {end_date}. Use MM/DD/YYYY")
            return "Invalid date format."
    else:
        end_dt = datetime.now(timezone.utc) - timedelta(days=2)
        
    start_dt = end_dt - timedelta(days=days + 210) # 210 for SMA200
    
    historical_data = fetch_all_data(symbols, start_dt, end_dt)
    sim = BacktestSimulator(initial_fund)
    sim.historical_data = historical_data

    # Initialize screener with backtest bars for point-in-time accuracy only when enabled.
    screener = None
    if screener_enabled:
        screener = UniverseBuilder(cfg)
        screener.preload_historical_bars(historical_data)
    
    sim_start_date = end_dt - timedelta(days=days)
    curr = sim_start_date
    all_dates = []
    while curr <= end_dt:
        all_dates.append(curr)
        curr += timedelta(days=1)
    
    strategies = [
        strat for strat in [
            MomentumStrategy(),
            RSIReversionStrategy(),
            GapUpStrategy(),
            MACrossoverStrategy(),
            RangeBreakoutStrategy(),
        ]
        if cfg["strategies"].get(strat.name, {}).get("enabled", False)
    ]
    
    mock_get_bars = _make_bar_fetcher(sim)

    # Use ExitStack so all patches share one block-stack entry instead of 16,
    # keeping the total nesting depth inside run_backtest() within CPython's
    # CO_MAXBLOCKS=20 compile-time limit.
    with contextlib.ExitStack() as stack:
        baseline_dir = tempfile.TemporaryDirectory()
        stack.callback(baseline_dir.cleanup)
        stack.enter_context(patch(
            "core.risk_manager.DAILY_BASELINE_FILE",
            Path(baseline_dir.name) / "daily_loss_baseline.json",
        ))
        _patch_runtime_risk_config(stack, cfg)
        stack.enter_context(patch("core.risk_manager._session_start_value", None))
        stack.enter_context(patch("core.risk_manager._session_date", None))
        stack.enter_context(patch("core.alpaca_client.get_portfolio_value", side_effect=sim.get_portfolio_value))
        stack.enter_context(patch("core.alpaca_client.get_cash", side_effect=sim.get_cash))
        stack.enter_context(patch("core.alpaca_client.get_all_positions", side_effect=sim.get_all_positions))
        stack.enter_context(patch("core.alpaca_client.get_position", side_effect=lambda s: sim.get_position(s)))
        stack.enter_context(patch("core.alpaca_client.get_stock_latest_price", side_effect=sim.get_current_price))
        stack.enter_context(patch("core.alpaca_client.get_crypto_latest_price", side_effect=sim.get_current_price))
        mock_trading_client = stack.enter_context(patch("core.alpaca_client.get_trading_client"))
        stack.enter_context(patch("core.alpaca_client.is_market_open", side_effect=lambda: sim.current_date.weekday() < 5))
        stack.enter_context(patch("tracking.trade_log.get_open_trades", side_effect=lambda: sim.get_open_trades_for_backtest()))
        stack.enter_context(patch("tracking.trade_log.get_trade_age_days", side_effect=lambda s: sim.get_trade_age_days(s)))
        stack.enter_context(patch("tracking.trade_log.log_trade"))
        stack.enter_context(patch("tracking.trade_log.mark_trade_closed"))
        stack.enter_context(patch("core.order_executor.log_trade", lambda *a, **kw: None))
        stack.enter_context(patch("core.order_executor.mark_trade_closed", lambda *a, **kw: None))
        stack.enter_context(patch("core.order_executor.MODE", "backtest"))
        stack.enter_context(patch("core.order_executor.ORDER_TYPE", "market"))
        stack.enter_context(patch("core.alpaca_client.get_stock_bars", side_effect=mock_get_bars))
        stack.enter_context(patch("core.alpaca_client.get_crypto_bars", side_effect=mock_get_bars))
        mock_trading_client.return_value.submit_order.side_effect = sim.submit_order
        
        for dt in all_dates:
            sim.current_date = dt
            regime_bars = {
                s: [
                    SimpleBar(
                        open_price=float(row["open"]),
                        high_price=float(row["high"]),
                        low_price=float(row["low"]),
                        close_price=float(row["close"]),
                        volume=float(row["volume"]),
                        timestamp=idx,
                    )
                    for idx, row in (
                        sim.historical_data[s][sim.historical_data[s].index <= sim.current_date].tail(60).iterrows()
                    )
                ]
                for s in ("SPY", "BTC/USD")
                if s in sim.historical_data
            }
            # Risk Check
            for symbol in list(sim.positions.keys()):
                pos = sim.positions[symbol]; price = sim.get_current_price(symbol)
                if price <= 0: continue
                update_high_water_price(pos, price)
                should_exit, reason = rm.should_exit_position(
                    symbol, pos["entry_price"], price,
                    custom_stop_price=pos.get("custom_stop_price"),
                )
                if should_exit:
                    oe.exit_position(symbol, reason, pos["asset_class"], open_trades_callback=sim.get_open_trades_for_backtest)
            # Scan
            for strat in strategies:
                if strat.asset_class == "stocks":
                    universe = screener.get_universe(as_of_date=dt) if screener_enabled else cfg["stocks"]["scan_universe"]
                else:
                    universe = cfg["crypto"]["scan_universe"]
                signals = strat.scan(universe, current_time=dt, regime_bars=regime_bars)
                for sig in signals:
                    if sig["symbol"] not in sim.positions:
                        order = oe.enter_position(
                            sig["symbol"],
                            strat.name,
                            strat.asset_class,
                            suggested_qty=sig.get("atr_risk_qty"),
                            atr_stop_price=sig.get("atr_stop_price"),
                        )
                        # enter_position returns status="open" (not "filled") — update strategy name on any non-None return
                        if order and sig["symbol"] in sim.positions:
                            sim.positions[sig["symbol"]]["strategy"] = strat.name
                            # Store ATR stop so the risk-check loop uses it instead of the
                            # global fixed-percentage stop for this position.
                            if sig.get("atr_stop_price") is not None:
                                sim.positions[sig["symbol"]]["custom_stop_price"] = sig["atr_stop_price"]
            # Hold Day Check — delegates to sim.get_trade_age_days() (stocks=business days, crypto=calendar days)
            for symbol in list(sim.positions.keys()):
                pos = sim.positions[symbol]
                strat_name = pos.get("strategy")
                strategy_cfg = cfg["strategies"].get(strat_name, {})
                if strategy_cfg.get("hold_days"):
                    age = sim.get_trade_age_days(symbol)
                    price = sim.get_current_price(symbol)
                    if price <= 0:
                        continue
                    peak = update_high_water_price(pos, price)
                    should_exit, reason = should_exit_for_hold(
                        strategy=strat_name,
                        age_days=age,
                        entry_price=pos["entry_price"],
                        current_price=price,
                        peak_price=peak,
                        strategy_cfg=strategy_cfg,
                    )
                    if should_exit:
                        oe.exit_position(symbol, reason, pos["asset_class"], open_trades_callback=sim.get_open_trades_for_backtest)
            sim.equity_curve.append({"date": dt, "value": sim.get_portfolio_value()})

    # --- Reporting ---
    df_curve = pd.DataFrame(sim.equity_curve)
    if graph_file:
        plt.figure(figsize=(10, 6))
        plt.plot(df_curve["date"], df_curve["value"], label="Equity Curve")
        plt.title(f"HawksTrade Backtest ({days} Days)")
        plt.xlabel("Date")
        plt.ylabel("Portfolio Value ($)")
        plt.grid(True)
        plt.savefig(graph_file)
        plt.close()

    if sim.trades_log:
        df = pd.DataFrame(sim.trades_log)
        strat_perf = df.groupby("strategy").agg({"pnl": ["sum", "count"], "pnl_pct": ["mean", "max", "min"]})
        strat_wins = df[df["pnl"] > 0].groupby("strategy").size()
        strat_total = df.groupby("strategy").size()
        strat_win_rate = (strat_wins / strat_total).fillna(0)

        summary = pd.DataFrame({
            "Trades": strat_total, "Win Rate": strat_win_rate.map(lambda x: f"{x:.1%}"),
            "Avg P&L %": strat_perf[("pnl_pct", "mean")].map(lambda x: f"{x:+.2%}"),
            "Total P&L $": strat_perf[("pnl", "sum")].map(lambda x: f"${x:,.2f}"),
            "Best": strat_perf[("pnl_pct", "max")].map(lambda x: f"{x:+.2%}"),
            "Worst": strat_perf[("pnl_pct", "min")].map(lambda x: f"{x:+.2%}")
        })

        final_val = sim.get_portfolio_value()
        total_win_rate = (df["pnl"] > 0).mean()
        max_drawdown = _compute_max_drawdown(df_curve)
        report = f"### Backtest Results ({days} Days)\n"
        report += f"- **Final Value**: ${final_val:,.2f} ({ (final_val/initial_fund-1):+.2%})\n"
        report += f"- **Total Trades**: {len(df)}\n\n"
        report += f"- **Win Rate**: {total_win_rate:.1%}\n"
        report += f"- **Max Drawdown**: {max_drawdown:.2%}\n"
        report += f"- **Momentum Exit Policy**: {cfg['strategies']['momentum']['exit_policy']}\n"
        report += f"- **Screener**: {'enabled' if screener_enabled else 'disabled'}\n\n"
        report += f"- **Enabled Strategies**: {', '.join(_enabled_strategy_names(cfg))}\n\n"
        report += summary.to_markdown() + "\n\n"
        if graph_file: report += f"![Equity Curve]({graph_file})\n\n"

        # --- Quarterly Performance Breakdown ---
        quarterly_data = _compute_quarterly_performance(sim, df_curve)
        if quarterly_data:
            report += "### Quarterly Performance\n"
            report += "| Quarter | Start Value | End Value | Return | Trades | Win Rate |\n"
            report += "|---------|-------------|-----------|--------|--------|----------|\n"
            for q in quarterly_data:
                report += f"| {q['quarter']} | ${q['start_value']:,.2f} | ${q['end_value']:,.2f} | {q['return_pct']:+.2%} | {q['trades']} | {q['win_rate']:.1%} |\n"
            report += "\n"
            # Save quarterly CSV
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            q_csv_path = BASE_DIR / "data" / f"quarterly_{ts}.csv"
            q_csv_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(quarterly_data).to_csv(q_csv_path, index=False)
            log.info(f"Quarterly report saved to {q_csv_path}")

        if output_file:
            with open(output_file, "a") as f: f.write(report)
        return report
    return "No trades executed."


def _compute_quarterly_performance(sim, df_curve):
    """Compute quarterly breakdowns from equity curve and trades log."""
    if df_curve.empty or not sim.trades_log:
        return []

    # Build a date->value lookup from equity curve
    equity_by_date = {row["date"]: row["value"] for _, row in df_curve.iterrows()}
    sorted_dates = sorted(equity_by_date.keys())
    if not sorted_dates:
        return []

    # Determine quarters spanned
    first_date = sorted_dates[0]
    last_date = sorted_dates[-1]

    quarters = []
    # Start from the quarter containing first_date
    q_month = ((first_date.month - 1) // 3) * 3 + 1
    q_start = first_date.replace(month=q_month, day=1, hour=0, minute=0, second=0, microsecond=0)

    while q_start <= last_date:
        q_year = q_start.year
        q_num = (q_start.month - 1) // 3 + 1
        # Quarter end: first day of next quarter
        if q_num == 4:
            q_end = q_start.replace(year=q_year + 1, month=1, day=1)
        else:
            q_end = q_start.replace(month=q_start.month + 3, day=1)

        # Find dates within this quarter
        q_dates = [d for d in sorted_dates if q_start <= d < q_end]
        if q_dates:
            start_val = equity_by_date[q_dates[0]]
            end_val = equity_by_date[q_dates[-1]]
            ret = (end_val - start_val) / start_val if start_val > 0 else 0

            # Count trades closed in this quarter
            q_trades = [t for t in sim.trades_log if q_start <= t["exit_date"] < q_end]
            n_trades = len(q_trades)
            n_wins = sum(1 for t in q_trades if t["pnl"] > 0)
            win_rate = n_wins / n_trades if n_trades > 0 else 0

            quarters.append({
                "quarter": f"Q{q_num} {q_year}",
                "start_value": start_val,
                "end_value": end_val,
                "return_pct": ret,
                "trades": n_trades,
                "win_rate": win_rate,
            })

        q_start = q_end

    return quarters

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--fund", type=float, default=10000.0)
    parser.add_argument("--output", type=str)
    parser.add_argument("--graph", type=str)
    parser.add_argument("--end-date", type=str, help="End date for backtest (MM/DD/YYYY)")
    parser.add_argument(
        "--exit-policy",
        choices=sorted(VALID_MOMENTUM_EXIT_POLICIES),
        help="Momentum hold exit policy to test",
    )
    screener_group = parser.add_mutually_exclusive_group()
    screener_group.add_argument("--screener", dest="use_screener", action="store_true", help="Force dynamic stock screener on")
    screener_group.add_argument("--no-screener", dest="use_screener", action="store_false", help="Force fixed stock universe only")
    parser.set_defaults(use_screener=None)
    parser.add_argument(
        "--strategies",
        type=str,
        help="Comma-separated strategy allowlist for experiments, e.g. momentum,ma_crossover,range_breakout",
    )
    parser.add_argument(
        "--set",
        dest="config_overrides",
        action="append",
        help="Backtest-only config override, e.g. --set strategies.momentum.top_n=3",
    )
    args = parser.parse_args()
    enabled_strategies = None
    if args.strategies:
        enabled_strategies = [name.strip() for name in args.strategies.split(",") if name.strip()]
    print(run_backtest(
        days=args.days,
        initial_fund=args.fund,
        output_file=args.output,
        graph_file=args.graph,
        end_date=args.end_date,
        exit_policy=args.exit_policy,
        use_screener=args.use_screener,
        enabled_strategies=enabled_strategies,
        config_overrides=args.config_overrides,
    ))
