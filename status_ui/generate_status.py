#!/usr/bin/env python3
"""
HawksTrade Status Dashboard Generator
======================================
Reads config/config.yaml, data/trades.csv, and logs/scan_YYYYMMDD.log
to produce a self-contained status.html. Run independently on a cron/loop.
Never imports or modifies any HawksTrade trading code.

Usage:
  python status_ui/generate_status.py
  python status_ui/generate_status.py --project-dir /path/to/HawksTrade
  python status_ui/generate_status.py --log-lines 200
"""

import argparse
import collections
import csv
import html
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    import re
    HAS_YAML = False


# ── Argument Parsing ──────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="HawksTrade Status HTML Generator")
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Path to HawksTrade root (default: parent of this script's directory)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path (default: <project-dir>/status_ui/status.html)",
    )
    parser.add_argument(
        "--log-lines",
        type=int,
        default=150,
        help="Number of log lines to show (default: 150)",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=60,
        help="Browser auto-refresh interval in seconds (default: 60, 0 to disable)",
    )
    return parser.parse_args()


# ── Path Resolution ───────────────────────────────────────────────────────────

def resolve_paths(project_dir_arg, output_arg, script_path):
    script_dir = Path(script_path).resolve().parent
    if project_dir_arg:
        project_dir = Path(project_dir_arg).resolve()
    else:
        project_dir = script_dir.parent

    config_path = project_dir / "config" / "config.yaml"
    trades_path = project_dir / "data" / "trades.csv"
    logs_dir    = project_dir / "logs"
    output_path = Path(output_arg).resolve() if output_arg else script_dir / "status.html"

    return project_dir, config_path, trades_path, logs_dir, output_path


# ── Data Loaders ──────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        if HAS_YAML:
            with open(config_path, encoding="utf-8", errors="replace") as f:
                return yaml.safe_load(f) or {}
        else:
            text = config_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^mode:\s*(['\"]?)(\w+)\1", text, re.MULTILINE)
            return {"mode": m.group(2) if m else "unknown"}
    except Exception:
        return {}


def get_last_run_time(logs_dir: Path) -> str:
    try:
        logs = sorted(logs_dir.glob("scan_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            return "N/A"
        mtime = logs[0].stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "N/A"


def get_last_run_log_path(logs_dir: Path):
    try:
        logs = sorted(logs_dir.glob("scan_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        return logs[0] if logs else None
    except Exception:
        return None


def tail_log(log_path: Path, n: int) -> list:
    """Memory-efficient tail using deque(maxlen=n) — reads O(n) lines into memory."""
    if not log_path or not log_path.exists():
        return ["(No log file found)"]
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = collections.deque(f, maxlen=n)
        return [line.rstrip() for line in lines]
    except Exception as e:
        return [f"(Error reading log: {e})"]


def load_trades(trades_path: Path) -> list:
    if not trades_path.exists():
        return []
    try:
        rows = []
        with open(trades_path, encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows
    except Exception:
        return []


def get_open_positions(trades: list) -> list:
    open_trades = [t for t in trades if t.get("status", "").strip().lower() == "open"]
    open_trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return open_trades


def get_recent_actions(trades: list, n: int = 60) -> list:
    sorted_trades = sorted(trades, key=lambda x: x.get("timestamp", ""), reverse=True)
    return sorted_trades[:n]


def _normalize_sym(symbol: str) -> str:
    """Normalize symbol for cross-source matching (e.g. DOGE/USD → DOGEUSD)."""
    return (symbol or "").replace("/", "").upper().strip()


def parse_portfolio_snapshot(log_path) -> dict:
    """
    Parse the last portfolio snapshot from the scan log.
    Returns dict keyed by normalized symbol:
      { "DOGEUSD": {"now": 0.096, "pnl_pct": 0.013, "entry": 0.0947, "qty": 52769.1} }
    Snapshot lines look like:
      [INFO] portfolio:   TQQQ   qty= 93.2589  entry=$  53.5826  now=$  55.5819  P&L=+3.73%
    """
    import re
    if not log_path or not Path(log_path).exists():
        return {}

    # Regex for position lines in portfolio snapshot
    pos_re = re.compile(
        r"\[INFO\]\s+portfolio:\s{2,}(\S+)\s+qty=\s*([\d.]+)\s+entry=\$\s*([\d,.]+)\s+"
        r"now=\$\s*([\d,.]+)\s+P&L=([+-]?[\d.]+%)"
    )

    # Read only the tail of the log, because only the most recent snapshot is needed.
    # 64 KB is large enough for typical snapshot blocks while avoiding a full-file scan.
    tail_bytes = 64 * 1024

    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            read_size = min(file_size, tail_bytes)
            if read_size <= 0:
                return {}
            f.seek(-read_size, 2)
            tail_text = f.read(read_size).decode("utf-8", errors="replace")

        marker = "PORTFOLIO SNAPSHOT"
        start_idx = tail_text.rfind(marker)
        if start_idx == -1:
            return {}

        result = {}
        in_snapshot = False
        for line in tail_text[start_idx:].splitlines():
            if marker in line:
                result = {}
                in_snapshot = True
                continue
            if not in_snapshot:
                continue

            m = pos_re.search(line)
            if m:
                sym, qty_s, entry_s, now_s, pnl_s = m.groups()
                try:
                    result[_normalize_sym(sym)] = {
                        "symbol_raw": sym,
                        "qty":    float(qty_s.replace(",", "")),
                        "entry":  float(entry_s.replace(",", "")),
                        "now":    float(now_s.replace(",", "")),
                        "pnl_pct": float(pnl_s.replace("%", "")) / 100.0,
                    }
                except (ValueError, TypeError):
                    pass
            elif "=======" in line and result:
                break

        # Return whatever was parsed even if the closing "=======" line is absent
        # (e.g., log truncated mid-snapshot due to a crash).
        return result
    except Exception:
        pass
    return {}


def compute_stats(trades: list) -> dict:
    closed = [t for t in trades if t.get("status", "").strip().lower() == "closed"
              and t.get("side", "").strip().lower() == "buy"]
    if not closed:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": "N/A", "realized_pnl_pct": "N/A", "realized_pnl_usd": "N/A",
        }
    total = len(closed)
    wins = 0
    total_dollar_pnl = 0.0
    total_invested = 0.0
    for t in closed:
        try:
            ep  = float(t.get("entry_price") or 0)
            xp  = float(t.get("exit_price") or 0)
            qty = float(t.get("qty") or 0)
            if ep > 0 and xp > 0 and qty > 0:
                dollar_pnl = (xp - ep) * qty
                total_dollar_pnl += dollar_pnl
                total_invested += ep * qty
                if dollar_pnl > 0:
                    wins += 1
        except (ValueError, TypeError):
            pass
    losses = total - wins
    win_rate = f"{wins/total:.1%}" if total else "N/A"
    pct = (total_dollar_pnl / total_invested) if total_invested else 0.0
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "realized_pnl_pct": f"{pct:+.2%}",
        "realized_pnl_usd": f"${total_dollar_pnl:+,.2f}",
    }


def compute_unrealized(open_positions: list, snapshot: dict) -> dict:
    """Compute unrealized P&L for open buy positions using the live snapshot."""
    total_usd = 0.0
    total_cost = 0.0
    for t in open_positions:
        if t.get("side", "").strip().lower() != "buy":
            continue
        key = _normalize_sym(t.get("symbol", ""))
        snap = snapshot.get(key)
        if not snap:
            continue
        try:
            ep  = float(t.get("entry_price") or snap["entry"])
            qty = float(t.get("qty") or snap["qty"])
            now = snap["now"]
            total_usd  += (now - ep) * qty
            total_cost += ep * qty
        except (ValueError, TypeError):
            pass
    pct = (total_usd / total_cost) if total_cost else 0.0
    if total_cost == 0:
        return {"unrealized_pnl_usd": "N/A", "unrealized_pnl_pct": "N/A"}
    return {
        "unrealized_pnl_usd": f"${total_usd:+,.2f}",
        "unrealized_pnl_pct": f"{pct:+.2%}",
    }


# ── HTML Helpers ──────────────────────────────────────────────────────────────

# Whitelist of known-safe CSS class suffixes for status badges
_SAFE_STATUS_CLASSES = {"open", "closed", "dry_run", "submitted", "partially_filled"}


def _esc(val) -> str:
    """HTML-escape a value from an untrusted source (CSV field, config)."""
    return html.escape(str(val) if val is not None else "", quote=True)


def _pnl_class(val_str: str) -> str:
    try:
        v = float(str(val_str).strip().replace("%", ""))
        return "pos" if v > 0 else ("neg" if v < 0 else "neutral")
    except (ValueError, TypeError):
        return "neutral"


def _fmt_pnl(val_str: str) -> str:
    try:
        v = float(str(val_str).strip())
        return html.escape(f"{v:+.2%}")
    except (ValueError, TypeError):
        return _esc(val_str) if val_str else "—"


def _fmt_price(val_str: str) -> str:
    try:
        v = float(str(val_str).strip())
        return html.escape(f"${v:,.4f}")
    except (ValueError, TypeError):
        return _esc(val_str) if val_str else "—"


def _fmt_qty(val_str: str) -> str:
    try:
        v = float(str(val_str).strip())
        return html.escape(f"{v:,.4f}".rstrip("0").rstrip("."))
    except (ValueError, TypeError):
        return _esc(val_str) if val_str else "—"


def _side_badge(side: str) -> str:
    s = (side or "").upper().strip()
    if s == "BUY":
        css = "buy"
    elif s == "SELL":
        css = "sell"
    else:
        css = "neutral-badge"
    label = _esc(s) if s else "—"
    return f'<span class="badge {css}">{label}</span>'


def _strat_badge(strategy: str) -> str:
    return f'<span class="strat-badge">{_esc(strategy) if strategy else "—"}</span>'


def _status_badge(status: str) -> str:
    """Sanitize status to a whitelisted CSS class; always escape displayed text."""
    raw = (status or "").lower().strip()
    css_class = raw if raw in _SAFE_STATUS_CLASSES else "unknown"
    label = _esc(status.upper() if status else "—")
    return f'<span class="status-badge {css_class}">{label}</span>'


# ── Table Builders ────────────────────────────────────────────────────────────

def build_positions_table(positions: list, snapshot: dict = None) -> str:
    if not positions:
        return '<p class="empty-msg">No open positions.</p>'
    snapshot = snapshot or {}
    rows_html = ""
    for p in positions:
        sym = p.get("symbol", "")
        key = _normalize_sym(sym)
        snap = snapshot.get(key)

        # Prefer live snapshot P&L; fall back to trade log pnl_pct (always blank for open)
        if snap:
            now_price = snap["now"]
            try:
                ep  = float(p.get("entry_price") or snap["entry"])
                qty = float(p.get("qty") or snap["qty"])
                now_display = html.escape(f"${now_price:,.4f}")
                if ep > 0:
                    pnl_pct_val = (now_price - ep) / ep
                    dollar_pnl  = (now_price - ep) * qty
                    pnl_display = (
                        f'<span class="{("pos" if pnl_pct_val >= 0 else "neg")}">'
                        f'{pnl_pct_val:+.2%} ({dollar_pnl:+,.2f})</span>'
                    )
                else:
                    pnl_display = '<span class="neutral">—</span>'
            except (ValueError, TypeError):
                pnl_display = '<span class="neutral">—</span>'
                now_display = "—"
        else:
            pnl_raw = p.get("pnl_pct", "")
            pnl_cls = _pnl_class(pnl_raw)
            pnl_display = (
                f'<span class="{pnl_cls}">{_fmt_pnl(pnl_raw)}</span>'
                if pnl_raw else '<span class="neutral">(no snapshot)</span>'
            )
            now_display = "—"

        rows_html += f"""
        <tr>
          <td><strong>{_esc(sym)}</strong></td>
          <td>{_side_badge(p.get('side',''))}</td>
          <td>{_fmt_qty(p.get('qty',''))}</td>
          <td>{_fmt_price(p.get('entry_price',''))}</td>
          <td>{now_display}</td>
          <td>{_esc(p.get('strategy',''))}</td>
          <td>{_esc(p.get('asset_class',''))}</td>
          <td>{_esc(p.get('timestamp','')[:19])}</td>
          <td>{pnl_display}</td>
        </tr>"""
    return f"""
    <table>
      <thead>
        <tr>
          <th>Symbol</th><th>Side</th><th>Qty</th><th>Entry Price</th>
          <th>Current Price</th><th>Strategy</th><th>Asset Class</th>
          <th>Opened (UTC)</th><th>P&amp;L % ($)</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>"""


def build_actions_table(actions: list) -> str:
    if not actions:
        return '<p class="empty-msg">No trade history found.</p>'
    rows_html = ""
    for t in actions:
        pnl_raw = t.get("pnl_pct", "")
        pnl_cls = _pnl_class(pnl_raw)
        rows_html += f"""
        <tr>
          <td>{_esc(t.get('timestamp','')[:19])}</td>
          <td><strong>{_esc(t.get('symbol',''))}</strong></td>
          <td>{_side_badge(t.get('side',''))}</td>
          <td>{_fmt_qty(t.get('qty',''))}</td>
          <td>{_fmt_price(t.get('entry_price',''))}</td>
          <td>{_fmt_price(t.get('exit_price',''))}</td>
          <td>{_strat_badge(t.get('strategy',''))}</td>
          <td>{_status_badge(t.get('status',''))}</td>
          <td class="{pnl_cls}">{_fmt_pnl(pnl_raw) if pnl_raw else '—'}</td>
          <td>{_esc(t.get('exit_reason',''))}</td>
        </tr>"""
    return f"""
    <table>
      <thead>
        <tr>
          <th>Timestamp (UTC)</th><th>Symbol</th><th>Side</th><th>Qty</th>
          <th>Entry</th><th>Exit</th><th>Strategy</th><th>Status</th><th>P&amp;L</th><th>Exit Reason</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>"""


def build_log_section(lines: list) -> str:
    escaped = "\n".join(html.escape(line) for line in lines)
    return f'<pre id="log-content">{escaped}</pre>'


# ── HTML Renderer ─────────────────────────────────────────────────────────────

def render_html(
    config: dict,
    last_run: str,
    open_positions: list,
    actions: list,
    stats: dict,
    unrealized: dict,
    log_lines: list,
    refresh_secs: int,
    log_path_str: str,
    generated_at: str,
    snapshot: dict = None,
) -> str:
    mode = (config.get("mode") or "unknown").upper()
    mode_class = "live" if mode == "LIVE" else "paper"
    mode_icon = "🔴" if mode == "LIVE" else "🟡"
    mode_label = f"{mode_icon} {html.escape(mode)}"

    intraday = config.get("intraday", {}).get("enabled", False)
    screener = config.get("screener", {}).get("enabled", False)
    max_pos  = config.get("trading", {}).get("max_positions", "—")
    stock_scan_interval  = config.get("schedule", {}).get("stock_scan_interval_min", "—")
    crypto_scan_interval = config.get("schedule", {}).get("crypto_scan_interval_min", "—")

    strategies_cfg = config.get("strategies", {})
    enabled_strats = [k for k, v in strategies_cfg.items() if isinstance(v, dict) and v.get("enabled", False)]
    strat_pills = "".join(f'<span class="strat-badge">{_esc(s)}</span>' for s in enabled_strats) or "—"

    pos_count    = len(open_positions)
    refresh_tag  = f'<meta http-equiv="refresh" content="{refresh_secs}">' if refresh_secs > 0 else ""
    pos_table    = build_positions_table(open_positions, snapshot=snapshot)
    actions_table = build_actions_table(actions)
    log_html     = build_log_section(log_lines)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {refresh_tag}
  <title>HawksTrade Status</title>
  <style>
    :root {{
      --bg: #0d1117;
      --surface: #161b22;
      --surface2: #21262d;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --green: #3fb950;
      --red: #f85149;
      --yellow: #d29922;
      --blue: #58a6ff;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
      font-size: 14px;
      line-height: 1.6;
    }}
    .header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 16px 24px;
      display: flex;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .header h1 {{ font-size: 18px; font-weight: 700; color: var(--blue); letter-spacing: 0.5px; }}
    .mode-badge {{
      font-size: 13px; font-weight: 700; padding: 4px 14px;
      border-radius: 20px; letter-spacing: 0.8px;
    }}
    .mode-badge.live {{ background: rgba(248,81,73,0.15); color: var(--red); border: 1px solid var(--red); }}
    .mode-badge.paper {{ background: rgba(210,153,34,0.15); color: var(--yellow); border: 1px solid var(--yellow); }}
    .header-meta {{ margin-left: auto; color: var(--muted); font-size: 12px; text-align: right; }}
    .header-meta span {{ display: block; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 12px; padding: 20px 24px 0;
    }}
    .card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 14px 16px;
    }}
    .card-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 6px; }}
    .card-value {{ font-size: 20px; font-weight: 700; color: var(--text); }}
    .card-value.green {{ color: var(--green); }}
    .card-value.red {{ color: var(--red); }}
    .card-value.blue {{ color: var(--blue); }}
    .card-value.neutral {{ color: var(--muted); }}
    .section {{
      margin: 20px 24px 0;
      background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
    }}
    .section-header {{
      padding: 12px 16px; background: var(--surface2); border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 10px; cursor: pointer; user-select: none;
    }}
    .section-header h2 {{ font-size: 14px; font-weight: 600; flex: 1; }}
    .section-header .count {{
      font-size: 12px; background: var(--bg); border: 1px solid var(--border);
      border-radius: 10px; padding: 1px 8px; color: var(--muted);
    }}
    .toggle-icon {{ font-size: 11px; color: var(--muted); }}
    .section-body {{ overflow-x: auto; }}
    .section-body.collapsed {{ display: none; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    thead tr {{ background: var(--surface2); }}
    th {{
      padding: 9px 12px; text-align: left; font-weight: 600; color: var(--muted);
      font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px;
      white-space: nowrap; border-bottom: 1px solid var(--border);
    }}
    td {{ padding: 9px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: rgba(255,255,255,0.03); }}
    .badge {{
      font-size: 11px; font-weight: 700; padding: 2px 8px;
      border-radius: 4px; letter-spacing: 0.5px;
    }}
    .badge.buy {{ background: rgba(63,185,80,0.15); color: var(--green); border: 1px solid var(--green); }}
    .badge.sell {{ background: rgba(248,81,73,0.15); color: var(--red); border: 1px solid var(--red); }}
    .badge.neutral-badge {{ background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }}
    .strat-badge {{
      font-size: 11px; background: rgba(88,166,255,0.1); color: var(--blue);
      border: 1px solid rgba(88,166,255,0.3); border-radius: 4px;
      padding: 1px 7px; margin-right: 4px; display: inline-block;
    }}
    .status-badge {{
      font-size: 10px; font-weight: 700; padding: 2px 6px;
      border-radius: 3px; letter-spacing: 0.5px;
    }}
    .status-badge.open {{ background: rgba(63,185,80,0.15); color: var(--green); border: 1px solid var(--green); }}
    .status-badge.closed {{ background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }}
    .status-badge.dry_run {{ background: rgba(88,166,255,0.1); color: var(--blue); border: 1px solid var(--blue); }}
    .status-badge.submitted {{ background: rgba(210,153,34,0.12); color: var(--yellow); border: 1px solid var(--yellow); }}
    .status-badge.partially_filled {{ background: rgba(210,153,34,0.12); color: var(--yellow); border: 1px solid var(--yellow); }}
    .status-badge.unknown {{ background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }}
    .pos {{ color: var(--green); font-weight: 600; }}
    .neg {{ color: var(--red); font-weight: 600; }}
    .neutral {{ color: var(--muted); }}
    pre#log-content {{
      background: var(--bg); padding: 16px; font-size: 12px;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      color: var(--muted); overflow-x: auto; white-space: pre-wrap;
      word-break: break-all; max-height: 500px; overflow-y: auto;
    }}
    .config-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }}
    .config-row {{ padding: 9px 16px; border-bottom: 1px solid var(--border); display: flex; gap: 8px; }}
    .config-row:last-child {{ border-bottom: none; }}
    .config-key {{ color: var(--muted); font-size: 12px; min-width: 160px; }}
    .config-val {{ color: var(--text); font-size: 12px; font-weight: 500; }}
    .empty-msg {{ color: var(--muted); padding: 20px 16px; font-style: italic; }}
    .footer {{
      padding: 20px 24px; color: var(--muted); font-size: 11px;
      text-align: center; border-top: 1px solid var(--border); margin-top: 20px;
    }}
  </style>
</head>
<body>

<div class="header">
  <span style="font-size:22px">🦅</span>
  <h1>HawksTrade</h1>
  <span class="mode-badge {mode_class}">{mode_label} MODE</span>
  <div class="header-meta">
    <span>📄 Generated: {html.escape(generated_at)}</span>
    <span>🕐 Last run: {html.escape(last_run)}</span>
    {"<span>🔄 Auto-refresh: every " + str(refresh_secs) + "s</span>" if refresh_secs > 0 else ""}
  </div>
</div>

<div class="cards">
  <div class="card">
    <div class="card-label">Open Positions</div>
    <div class="card-value blue">{pos_count}</div>
  </div>
  <div class="card">
    <div class="card-label">Closed Trades</div>
    <div class="card-value">{stats['total']}</div>
  </div>
  <div class="card">
    <div class="card-label">Win Rate</div>
    <div class="card-value {'green' if stats['total'] > 0 else 'neutral'}">{html.escape(stats['win_rate'])}</div>
  </div>
  <div class="card">
    <div class="card-label">Realized P&amp;L</div>
    <div class="card-value {_pnl_class(stats['realized_pnl_pct'].replace('%',''))}">
      {html.escape(stats['realized_pnl_usd'])}<br>
      <span style="font-size:13px;font-weight:400">{html.escape(stats['realized_pnl_pct'])}</span>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Unrealized P&amp;L</div>
    <div class="card-value {_pnl_class(unrealized['unrealized_pnl_pct'].replace('%','')) if unrealized['unrealized_pnl_pct'] != 'N/A' else 'neutral'}">
      {html.escape(unrealized['unrealized_pnl_usd'])}<br>
      <span style="font-size:13px;font-weight:400">{html.escape(unrealized['unrealized_pnl_pct'])}</span>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Max Positions</div>
    <div class="card-value">{html.escape(str(max_pos))}</div>
  </div>
  <div class="card">
    <div class="card-label">Intraday</div>
    <div class="card-value {'green' if intraday else 'neutral'}">{'ON' if intraday else 'OFF'}</div>
  </div>
</div>

<div class="section">
  <div class="section-header" onclick="toggle('positions-body','pos-icon')">
    <span>📂</span>
    <h2>Open Positions</h2>
    <span class="count">{pos_count}</span>
    <span class="toggle-icon" id="pos-icon">▼</span>
  </div>
  <div class="section-body" id="positions-body">{pos_table}</div>
</div>

<div class="section">
  <div class="section-header" onclick="toggle('actions-body','act-icon')">
    <span>⚡</span>
    <h2>Trade Actions</h2>
    <span class="count">{len(actions)}</span>
    <span class="toggle-icon" id="act-icon">▼</span>
  </div>
  <div class="section-body" id="actions-body">{actions_table}</div>
</div>

<div class="section">
  <div class="section-header" onclick="toggle('config-body','cfg-icon')">
    <span>⚙️</span>
    <h2>System Configuration</h2>
    <span class="toggle-icon" id="cfg-icon">▼</span>
  </div>
  <div class="section-body collapsed" id="config-body">
    <div class="config-grid">
      <div class="config-row"><span class="config-key">Trading Mode</span><span class="config-val">{html.escape(mode)}</span></div>
      <div class="config-row"><span class="config-key">Intraday Trading</span><span class="config-val">{'Enabled' if intraday else 'Disabled'}</span></div>
      <div class="config-row"><span class="config-key">Dynamic Screener</span><span class="config-val">{'Enabled' if screener else 'Disabled'}</span></div>
      <div class="config-row"><span class="config-key">Max Positions</span><span class="config-val">{html.escape(str(max_pos))}</span></div>
      <div class="config-row"><span class="config-key">Stock Scan Interval</span><span class="config-val">{html.escape(str(stock_scan_interval))} min</span></div>
      <div class="config-row"><span class="config-key">Crypto Scan Interval</span><span class="config-val">{html.escape(str(crypto_scan_interval))} min</span></div>
      <div class="config-row"><span class="config-key">Enabled Strategies</span><span class="config-val">{strat_pills}</span></div>
    </div>
  </div>
</div>

<div class="section">
  <div class="section-header" onclick="toggle('log-body','log-icon')">
    <span>📄</span>
    <h2>Last Run Logs</h2>
    <span class="count">{html.escape(log_path_str)}</span>
    <span class="toggle-icon" id="log-icon">▶</span>
  </div>
  <div class="section-body collapsed" id="log-body">
    {log_html}
    <div style="padding:8px 16px;border-top:1px solid var(--border);">
      <button onclick="scrollLogToBottom()" style="background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;">
        ↓ Scroll to bottom
      </button>
    </div>
  </div>
</div>

<div class="footer">
  HawksTrade Status UI &nbsp;·&nbsp; Generated {html.escape(generated_at)} &nbsp;·&nbsp; Read-only dashboard — trading system not modified
</div>

<script>
  function toggle(bodyId, iconId) {{
    const body = document.getElementById(bodyId);
    const icon = document.getElementById(iconId);
    const collapsed = body.classList.toggle('collapsed');
    icon.textContent = collapsed ? '▶' : '▼';
  }}
  function scrollLogToBottom() {{
    const el = document.getElementById('log-content');
    if (el) el.scrollTop = el.scrollHeight;
  }}
</script>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    project_dir, config_path, trades_path, logs_dir, output_path = resolve_paths(
        args.project_dir, args.output, __file__
    )

    config       = load_config(config_path)
    last_run     = get_last_run_time(logs_dir)
    log_path     = get_last_run_log_path(logs_dir)
    log_lines    = tail_log(log_path, args.log_lines)
    trades       = load_trades(trades_path)
    open_pos     = get_open_positions(trades)
    actions      = get_recent_actions(trades, n=60)
    stats        = compute_stats(trades)
    snapshot     = parse_portfolio_snapshot(log_path)
    unrealized   = compute_unrealized(open_pos, snapshot)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log_path_str = log_path.name if log_path else "no log found"

    html_content = render_html(
        config=config,
        last_run=last_run,
        open_positions=open_pos,
        actions=actions,
        stats=stats,
        unrealized=unrealized,
        log_lines=log_lines,
        refresh_secs=args.refresh,
        log_path_str=log_path_str,
        generated_at=generated_at,
        snapshot=snapshot,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    print(f"[HawksTrade Status] Dashboard written → {output_path}")


if __name__ == "__main__":
    main()
