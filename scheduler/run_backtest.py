"""
HawksTrade - Historical Backtest Simulation
===========================================
Backtests the current strategies and risk management settings
over a specified period with a custom starting fund.
"""

import os
import sys
import logging
import argparse
import yaml
import pandas as pd
import matplotlib.pyplot as plt
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
        """Compute trade age from sim positions and current_date."""
        if symbol not in self.positions:
            return 0.0
        entry_date = self.positions[symbol]["entry_date"]
        return (self.current_date - entry_date).days

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
            else:
                self.positions[symbol] = {"qty": qty, "entry_price": price, "entry_date": self.current_date, "asset_class": "stock" if "/" not in symbol else "crypto", "strategy": "backtest"}
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
        return {"order_id": "order_id", "status": "filled"}

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

def run_backtest(days=365, initial_fund=10000.0, output_file=None, graph_file=None, end_date=None):
    with open(BASE_DIR / "config" / "config.yaml") as f: cfg = yaml.safe_load(f)

    # Build extended symbol pool (legacy + extended, deduped)
    all_stock_symbols = list(dict.fromkeys(
        cfg["stocks"]["scan_universe"] + EXTENDED_POOL
    ))
    symbols = all_stock_symbols + cfg["crypto"]["scan_universe"]
    
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

    # Initialize screener with backtest bars for point-in-time accuracy
    screener = UniverseBuilder(cfg)
    screener.preload_historical_bars(historical_data)
    
    sim_start_date = end_dt - timedelta(days=days)
    curr = sim_start_date
    all_dates = []
    while curr <= end_dt:
        all_dates.append(curr)
        curr += timedelta(days=1)
    
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
        patch("tracking.trade_log.get_open_trades", side_effect=lambda: sim.get_open_trades_for_backtest()),
        patch("tracking.trade_log.get_trade_age_days", side_effect=lambda s: sim.get_trade_age_days(s)),
        patch("tracking.trade_log.log_trade"),
        patch("tracking.trade_log.mark_trade_closed"),
        patch("core.order_executor.MODE", "backtest"),
        patch("core.order_executor.ORDER_TYPE", "market"),
    ):
        mock_trading_client.return_value.submit_order.side_effect = sim.submit_order
        
        for dt in all_dates:
            sim.current_date = dt
            # Risk Check
            for symbol in list(sim.positions.keys()):
                pos = sim.positions[symbol]; price = sim.get_current_price(symbol)
                if price <= 0: continue
                should_exit, reason = rm.should_exit_position(symbol, pos["entry_price"], price)
                if should_exit: oe.exit_position(symbol, reason, pos["asset_class"])
            # Scan
            for strat in strategies:
                universe = screener.get_universe(as_of_date=dt) if strat.asset_class == "stocks" else cfg["crypto"]["scan_universe"]
                def mock_get_bars(symbols, timeframe="1Day", limit=60):
                    class MockBarSet:
                        def __init__(self): self.data = {}; self.df = pd.DataFrame()
                        def __getitem__(self, key): return self.data.get(key)
                    res = MockBarSet(); dfs = []
                    for s in symbols:
                        if s in sim.historical_data:
                            df = sim.historical_data[s]; mask = df.index <= sim.current_date; hist_df = df[mask].tail(limit)
                            bars_list = []
                            for idx, row in hist_df.iterrows():
                                bar = SimpleBar(open_price=float(row["open"]), high_price=float(row["high"]), low_price=float(row["low"]), close_price=float(row["close"]), volume=float(row["volume"]), timestamp=idx)
                                bars_list.append(bar)
                            res.data[s] = bars_list
                            temp_df = hist_df.copy(); temp_df.index = pd.MultiIndex.from_product([[s], temp_df.index], names=['symbol', 'timestamp']); dfs.append(temp_df)
                    if dfs: res.df = pd.concat(dfs)
                    return res
                with patch("core.alpaca_client.get_stock_bars", side_effect=mock_get_bars), \
                     patch("core.alpaca_client.get_crypto_bars", side_effect=mock_get_bars):
                    signals = strat.scan(universe, current_time=dt)
                    for sig in signals:
                        if sig["symbol"] not in sim.positions:
                            order = oe.enter_position(sig["symbol"], strat.name, strat.asset_class)
                            # enter_position returns status="open" (not "filled") — update strategy name on any non-None return
                            if order and sig["symbol"] in sim.positions:
                                sim.positions[sig["symbol"]]["strategy"] = strat.name
            # Hold Day Check
            for symbol in list(sim.positions.keys()):
                pos = sim.positions[symbol]; strat_name = pos.get("strategy")
                hold_days_limit = cfg["strategies"].get(strat_name, {}).get("hold_days")
                if hold_days_limit:
                    age = (sim.current_date - pos["entry_date"]).days
                    if age >= hold_days_limit: oe.exit_position(symbol, f"Hold {age}d", pos["asset_class"])
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
        report = f"### Backtest Results ({days} Days)\n"
        report += f"- **Final Value**: ${final_val:,.2f} ({ (final_val/initial_fund-1):+.2%})\n"
        report += f"- **Total Trades**: {len(df)}\n\n"
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
    args = parser.parse_args()
    print(run_backtest(days=args.days, initial_fund=args.fund, output_file=args.output, graph_file=args.graph, end_date=args.end_date))
