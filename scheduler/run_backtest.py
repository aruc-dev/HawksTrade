"""
HawksTrade - Historical Backtest Simulation
===========================================
Backtests the current strategies and risk management settings
over a specified period with a custom starting fund.

Usage:
  python3 scheduler/run_backtest.py --days 28 --fund 10000
"""

import os
import sys
import logging
import argparse
import yaml
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core import alpaca_client as ac
from core import risk_manager as rm
from core import order_executor as oe
from strategies.momentum import MomentumStrategy
from strategies.rsi_reversion import RSIReversionStrategy
from strategies.gap_up import GapUpStrategy
from strategies.ma_crossover import MACrossoverStrategy
from strategies.range_breakout import RangeBreakoutStrategy

# ── Setup Logging ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
log = logging.getLogger("backtest")

# ── Simulation State ─────────────────────────────────────────────────────────

class BacktestSimulator:
    def __init__(self, initial_fund=10000.0):
        self.portfolio_value = initial_fund
        self.cash = initial_fund
        self.positions = {}  # symbol -> {qty, entry_price, entry_date, asset_class, strategy}
        self.trades_log = []
        self.current_date = None
        self.historical_data = {}  # symbol -> df

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

    def get_current_price(self, symbol):
        df = self.historical_data.get(symbol)
        if df is None or df.empty:
            return 0.0
        mask = df.index <= self.current_date
        valid_bars = df[mask]
        if valid_bars.empty:
            return 0.0
        return float(valid_bars.iloc[-1]["close"])

    def submit_order(self, req):
        symbol = req.symbol
        side = req.side.value.lower()
        qty = float(req.qty)
        price = self.get_current_price(symbol)
        
        if side == "buy":
            cost = qty * price
            if cost > self.cash:
                log.warning(f"  [SIM] Insufficient cash to buy {qty} {symbol}")
                return MagicMock(id="failed")
            self.cash -= cost
            if symbol in self.positions:
                old_pos = self.positions[symbol]
                total_qty = old_pos["qty"] + qty
                avg_price = (old_pos["qty"] * old_pos["entry_price"] + cost) / total_qty
                self.positions[symbol]["qty"] = total_qty
                self.positions[symbol]["entry_price"] = avg_price
            else:
                self.positions[symbol] = {
                    "qty": qty,
                    "entry_price": price,
                    "entry_date": self.current_date,
                    "asset_class": "stock" if "/" not in symbol else "crypto",
                    "strategy": "backtest" 
                }
            log.info(f"  [SIM] BOUGHT {qty:.4f} {symbol} @ ${price:.2f}")
        else:
            if symbol not in self.positions:
                return MagicMock(id="failed")
            pos = self.positions[symbol]
            sell_qty = min(qty, pos["qty"])
            proceeds = sell_qty * price
            self.cash += proceeds
            
            pnl = (price - pos["entry_price"]) * sell_qty
            pnl_pct = (price / pos["entry_price"]) - 1
            
            self.trades_log.append({
                "symbol": symbol,
                "entry_date": pos["entry_date"],
                "exit_date": self.current_date,
                "entry_price": pos["entry_price"],
                "exit_price": price,
                "qty": sell_qty,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "strategy": pos.get("strategy", "unknown")
            })
            
            if sell_qty >= pos["qty"]:
                del self.positions[symbol]
            else:
                self.positions[symbol]["qty"] -= sell_qty
            
            log.info(f"  [SIM] SOLD {sell_qty:.4f} {symbol} @ ${price:.2f} | P&L: {pnl_pct:+.2%}")
            
        return MagicMock(id="order_id")

# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_all_data(symbols, start_date, end_date):
    log.info(f"Fetching historical data for {len(symbols)} symbols...")
    data = {}
    stocks = [s for s in symbols if "/" not in s]
    cryptos = [s for s in symbols if "/" in s]
    
    for s in stocks:
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from alpaca.data.enums import Adjustment
            req = StockBarsRequest(
                symbol_or_symbols=[s], 
                timeframe=TimeFrame.Day, 
                start=start_date, 
                end=end_date,
                adjustment=Adjustment.ALL
            )
            bars = ac.get_stock_data_client().get_stock_bars(req)
            if s in bars.data:
                data[s] = bars.df.loc[s]
        except Exception as e:
            log.error(f"Failed to fetch {s}: {e}")

    for s in cryptos:
        try:
            from alpaca.data.requests import CryptoBarsRequest
            from alpaca.data.timeframe import TimeFrame
            req = CryptoBarsRequest(symbol_or_symbols=[s], timeframe=TimeFrame.Day, start=start_date, end=end_date)
            bars = ac.get_crypto_data_client().get_crypto_bars(req)
            if s in bars.data:
                data[s] = bars.df.loc[s]
        except Exception as e:
            log.error(f"Failed to fetch {s}: {e}")
            
    return data

# ── Main Backtest Loop ────────────────────────────────────────────────────────

def run_backtest(days=28, initial_fund=10000.0):
    with open(BASE_DIR / "config" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    
    symbols = cfg["stocks"]["scan_universe"] + cfg["crypto"]["scan_universe"]
    end_dt = datetime.now(timezone.utc) - timedelta(days=2) 
    start_dt = end_dt - timedelta(days=days + 30) 
    
    historical_data = fetch_all_data(symbols, start_dt, end_dt)
    sim = BacktestSimulator(initial_fund)
    sim.historical_data = historical_data
    
    sim_start_date = end_dt - timedelta(days=days)
    curr = sim_start_date
    all_dates = []
    while curr <= end_dt:
        all_dates.append(curr)
        curr += timedelta(days=1)
    
    log.info("\n" + "="*60)
    log.info(f"STARTING BACKTEST: {sim_start_date.date()} to {end_dt.date()}")
    log.info(f"Initial Fund: ${initial_fund:,.2f}")
    log.info("="*60 + "\n")
    
    strategies = [MomentumStrategy(), RSIReversionStrategy(), GapUpStrategy(), MACrossoverStrategy(), RangeBreakoutStrategy()]
    
    with (
        patch("core.alpaca_client.get_portfolio_value", side_effect=sim.get_portfolio_value),
        patch("core.alpaca_client.get_cash", side_effect=sim.get_cash),
        patch("core.alpaca_client.get_all_positions", side_effect=sim.get_all_positions),
        patch("core.alpaca_client.get_position", side_effect=lambda s: sim.get_position(s)),
        patch("core.alpaca_client.get_stock_latest_price", side_effect=sim.get_current_price),
        patch("core.alpaca_client.get_crypto_latest_price", side_effect=sim.get_current_price),
        patch("core.alpaca_client.get_trading_client") as mock_trading_client,
        patch("core.alpaca_client.is_market_open", return_value=True),
        patch("tracking.trade_log.get_open_trades") as mock_get_open_trades,
        patch("tracking.trade_log.log_trade") as mock_log_trade,
        patch("tracking.trade_log.mark_trade_closed") as mock_mark_trade_closed,
        patch("core.order_executor.MODE", "backtest"),
        patch("core.order_executor.ORDER_TYPE", "market"),
    ):
        mock_trading_client.return_value.submit_order.side_effect = sim.submit_order
        mock_trading_client.return_value.get_clock.return_value.is_open = True
        
        for dt in all_dates:
            sim.current_date = dt
            log.info(f"--- SIMULATING DATE: {dt.date()} ---")
            
            open_trades_formatted = []
            for s, p in sim.positions.items():
                open_trades_formatted.append({
                    "symbol": s, "qty": p["qty"], "entry_price": p["entry_price"],
                    "entry_date": p["entry_date"].isoformat(), "asset_class": p["asset_class"],
                    "strategy": p.get("strategy", "unknown"), "status": "open"
                })
            mock_get_open_trades.return_value = open_trades_formatted

            # 1. RISK CHECK
            for symbol in list(sim.positions.keys()):
                pos = sim.positions[symbol]
                price = sim.get_current_price(symbol)
                if price <= 0: continue
                should_exit, reason = rm.should_exit_position(symbol, pos["entry_price"], price)
                if should_exit:
                    log.info(f"  [RISK] {symbol} triggered exit: {reason}")
                    oe.exit_position(symbol, reason, pos["asset_class"])

            # 2. SCAN & ENTRY
            for strat in strategies:
                universe = cfg["stocks"]["scan_universe"] if strat.asset_class == "stocks" else cfg["crypto"]["scan_universe"]
                
                def mock_get_bars(symbols, timeframe="1Day", limit=60):
                    class MockBarSet:
                        def __init__(self): self.data = {}; self.df = pd.DataFrame()
                        def __getitem__(self, key): return self.data.get(key)
                    res = MockBarSet(); dfs = []
                    for s in symbols:
                        if s in sim.historical_data:
                            df = sim.historical_data[s]
                            mask = df.index <= sim.current_date
                            hist_df = df[mask].tail(limit)
                            bars_list = []
                            for idx, row in hist_df.iterrows():
                                bar = MagicMock()
                                bar.close = float(row["close"])
                                bar.open = float(row["open"])
                                bar.high = float(row["high"])
                                bar.low = float(row["low"])
                                bar.volume = float(row["volume"])
                                bar.timestamp = idx
                                bars_list.append(bar)
                            res.data[s] = bars_list
                            temp_df = hist_df.copy()
                            temp_df.index = pd.MultiIndex.from_product([[s], temp_df.index], names=['symbol', 'timestamp'])
                            dfs.append(temp_df)
                    if dfs: res.df = pd.concat(dfs)
                    return res
                
                with patch("core.alpaca_client.get_stock_bars", side_effect=mock_get_bars), \
                     patch("core.alpaca_client.get_crypto_bars", side_effect=mock_get_bars):
                    signals = strat.scan(universe, current_time=dt)
                    for sig in signals:
                        symbol = sig["symbol"]
                        if symbol not in sim.positions:
                            price = sim.get_current_price(symbol)
                            if price > 0:
                                log.info(f"  [STRAT] {strat.name} signal for {symbol}: {sig['reason']}")
                                with patch("tracking.trade_log.get_trade_age_days", return_value=0):
                                    order = oe.enter_position(symbol, strat.name, strat.asset_class)
                                    if order and order.get("order_id") != "failed":
                                        if symbol in sim.positions: sim.positions[symbol]["strategy"] = strat.name

            # 3. HOLD DAY EXITS
            for symbol in list(sim.positions.keys()):
                pos = sim.positions[symbol]
                strat_name = pos.get("strategy")
                hold_days_limit = cfg["strategies"].get(strat_name, {}).get("hold_days")
                if hold_days_limit:
                    age = (sim.current_date - pos["entry_date"]).days
                    if age >= hold_days_limit:
                        log.info(f"  [TIME] {symbol} reached hold limit ({age} days)")
                        oe.exit_position(symbol, f"Hold period {age}d reached", pos["asset_class"])

    log.info("\n" + "="*60 + "\nBACKTEST COMPLETED\n" + "="*60)
    final_val = sim.get_portfolio_value()
    pnl = final_val - initial_fund
    pnl_pct = (final_val / initial_fund) - 1
    
    log.info(f"Initial Fund: ${initial_fund:,.2f}")
    log.info(f"Final Value:  ${final_val:,.2f}")
    log.info(f"Total P&L:    ${pnl:+,.2f} ({pnl_pct:+.2%})")
    
    if sim.trades_log:
        df = pd.DataFrame(sim.trades_log)
        
        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]
        win_rate = len(wins) / len(df)
        
        gross_profit = wins["pnl"].sum()
        gross_loss = abs(losses["pnl"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        avg_win = wins["pnl_pct"].mean() if not wins.empty else 0
        avg_loss = losses["pnl_pct"].mean() if not losses.empty else 0
        
        # Strategy Breakdown
        strat_perf = df.groupby("strategy").agg({
            "pnl": ["sum", "count"],
            "pnl_pct": ["mean", "max", "min"]
        })
        strat_wins = df[df["pnl"] > 0].groupby("strategy").size()
        strat_total = df.groupby("strategy").size()
        strat_win_rate = (strat_wins / strat_total).fillna(0)

        log.info(f"Total Trades:  {len(df)}")
        log.info(f"Win Rate:      {win_rate:.1%}")
        log.info(f"Profit Factor: {profit_factor:.2f}")
        log.info(f"Avg Win:       {avg_win:+.2%}")
        log.info(f"Avg Loss:      {avg_loss:+.2%}")
        
        log.info("\nStrategy Attribution Report:")
        summary = pd.DataFrame({
            "Trades": strat_total,
            "Win Rate": strat_win_rate.map(lambda x: f"{x:.1%}"),
            "Avg P&L %": strat_perf[("pnl_pct", "mean")].map(lambda x: f"{x:+.2%}"),
            "Total P&L $": strat_perf[("pnl", "sum")].map(lambda x: f"${x:,.2f}"),
            "Best Trade": strat_perf[("pnl_pct", "max")].map(lambda x: f"{x:+.2%}"),
            "Worst Trade": strat_perf[("pnl_pct", "min")].map(lambda x: f"{x:+.2%}")
        })
        log.info(summary.to_string())
        
        log.info("\nTop 5 Winning Trades:")
        log.info(df.sort_values("pnl_pct", ascending=False).head(5)[["symbol", "pnl_pct", "strategy"]].to_string(index=False))
        
        log.info("\nTop 5 Losing Trades:")
        log.info(df.sort_values("pnl_pct", ascending=True).head(5)[["symbol", "pnl_pct", "strategy"]].to_string(index=False))

    return pnl_pct

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--fund", type=float, default=10000.0)
    args = parser.parse_args()
    run_backtest(days=args.days, initial_fund=args.fund)
