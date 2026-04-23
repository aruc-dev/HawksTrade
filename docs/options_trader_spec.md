# OptionsTrader — System Design & Build Plan

**Goal.** An automated options trading system on Alpaca, running on EC2,
optimized for **risk-adjusted** return on capital. Separate repo from
HawksTrade. Inherits HawksTrade's operational shape (systemd, Cloudflare
Tunnel dashboard, config YAML, trade-log CSV) but uses options-native
primitives.

**Audience.** This document is written for an AI coding agent to build the
system end-to-end, and for a human reviewer (Arun) to sanity-check
architecture decisions. If any section is ambiguous, STOP and ask Arun
before implementing — options mistakes are expensive.

---

## 0. Read this first — non-negotiables

These rules exist because options blow up accounts that stock strategies
wouldn't. They are mandatory. An implementing agent that cannot satisfy one
must surface the issue, not work around it.

1. **Defined-risk strategies only.** No naked short calls. No naked short
   puts without full cash coverage. Every position has a known,
   pre-calculated maximum loss *before* the order is submitted.

2. **Position cap = defined max loss, not notional.** Portfolio risk is
   measured as the sum of max-loss across all open positions. The bot
   halts new entries when sum(max_loss) / portfolio_value > `max_portfolio_risk_pct`.

3. **Assignment-aware.** Every short-option position has documented
   assignment behavior. American-style equity options can be assigned any
   time, especially around ex-dividend dates. The bot must check
   ex-dividend dates and close short calls the day before ex-div if the
   dividend > remaining extrinsic value.

4. **No 0-DTE or 1-DTE.** All entries have ≥ 7 DTE (days to expiration).
   No gamma roulette, no Friday-afternoon plays. Short-dated gamma risk
   is catastrophic and cannot be risk-managed on a 15-minute scheduler.

5. **IV rank gate.** Short-premium strategies only fire when IV rank ≥ 30.
   Long-premium strategies only fire when IV rank ≤ 40. (IV rank = current
   IV vs 52-week high/low.) Trading rich premium into spikes or paying for
   cheap premium is half the edge.

6. **Liquidity gate.** Every contract traded must have open interest ≥ 100,
   volume ≥ 10 (daily), and bid-ask spread ≤ 10% of mid. No exceptions.
   Illiquid options eat the account through slippage, not losses.

7. **Earnings blackout.** No new positions in an underlying within the
   5 calendar days before its next earnings release. Existing positions
   are closed 2 days before earnings unless the strategy explicitly
   *is* an earnings play (which requires a separate flag and tighter
   risk limit).

8. **Pattern Day Trader awareness.** Account value < $25k forces swing-only
   mode (no same-day close/reopen of the same contract). The config
   enforces this automatically based on account equity.

9. **Read-only Alpaca key for the dashboard.** Same pattern as HawksTrade:
   a separate dedicated read-only API key for the dashboard, never the
   trading key.

10. **Max position = 5% of portfolio max-loss, max open strategies = 8.**
    Hard caps. Tunable down, never up, without a formal review.

---

## 1. What "maximize profit" means (and doesn't mean)

A system that maximizes expected profit would sell naked strangles every
day on the highest-IV names and ignore tails. That system has positive
expected value and blows up eventually — not because it's wrong on
average, but because a single 4σ move ends the account before the edge
compounds.

This system optimizes **return on capital at risk**, not gross return.
Specifically:

- **Primary objective:** Sharpe ratio over 90-day rolling windows.
- **Secondary:** max drawdown ≤ 15%.
- **Tertiary:** annualized return.

A trade that raises Sharpe even if it lowers total return is preferred
over one that raises total return at the cost of Sharpe. This is
non-negotiable for the same reason as §0: there is no second chance
after a portfolio-level blowup.

---

## 2. Architecture overview

```
                  ┌──────────────────────────────┐
                  │   OptionsTrader (EC2)        │
                  │                              │
                  │   scheduler/  (systemd)      │
                  │     • scan.service (30 min)  │
                  │     • risk_check.service     │
                  │       (5 min baseline)       │
                  │     • risk_watch.service     │
                  │       (1 min elevated)       │
                  │     • roll_check.service     │
                  │     • eod_report.service     │
                  │                              │
                  │   strategies/                │
                  │     • covered_call           │
                  │     • cash_secured_put       │
                  │     • vertical_spread        │
                  │     • iron_condor            │
                  │     • calendar_spread        │
                  │     • earnings_iron_condor   │
                  │                              │
                  │   core/                      │
                  │     • alpaca_options_client  │
                  │     • risk_manager           │
                  │     • greeks_calculator      │
                  │     • iv_rank_tracker        │
                  │     • assignment_handler     │
                  │     • roll_engine            │
                  │                              │
                  │   ai/                        │
                  │     • news_gate              │
                  │     • earnings_sentiment     │
                  │     • trade_idea_critic      │
                  │                              │
                  │   data/                      │
                  │     • trades.csv             │
                  │     • greeks_snapshots/      │
                  │     • iv_rank_history.csv    │
                  │     • positions.json         │
                  │                              │
                  │   dashboard/  (FastAPI)      │
                  │     → 127.0.0.1:8080         │
                  └────────────┬─────────────────┘
                               │
                               ▼ (Cloudflare Tunnel + Access)
                      https://opts.<domain>.us
```

### Inherits from HawksTrade
- `scheduler/systemd/` unit + timer pattern, shared `hawkstrade-secrets.service`
  equivalent for secrets-in-RAM
- `dashboard/` architecture (FastAPI 127.0.0.1 only, Cloudflare Access,
  defense-in-depth JWT validation, dedicated service user)
- `config/config.yaml` structure, env-file separation, secrets-in-shm pattern
- Trade log CSV schema (extended for options — see §6)
- Cloud-setup markdown style: `cloud-setup/aws-setup-systemd.md` +
  `cloud-setup/dashboard-setup.md`
- Testing discipline: `python3 -m unittest discover` before commit, plus
  a backtest-as-validation step

### New primitives (not in HawksTrade)
- Options chain fetching, filtering, and contract selection
- Greeks calculation (delta, theta, vega, gamma)
- Implied volatility rank (IV rank) tracking — requires 52-week history
- Multi-leg order submission (`order_class: "mleg"`)
- Roll engine (close + reopen a contract at a new strike/expiry)
- Assignment handler (detect assignment from position reconciliation,
  convert to stock position, decide to hold or liquidate)
- AI news/earnings gate (optional, see §8)

---

## 3. Repository layout

```
OptionsTrader/
├── README.md
├── CLAUDE.md                    # copy HawksTrade style, adapted for options
├── LICENSE
├── config/
│   ├── config.yaml              # main config (see §4)
│   ├── .env.example
│   └── underlyings.yaml         # watchlist with earnings dates, div dates
├── core/
│   ├── __init__.py
│   ├── alpaca_options_client.py # thin wrapper, only whitelisted methods
│   ├── risk_manager.py
│   ├── greeks_calculator.py     # Black-Scholes + numerical greeks
│   ├── iv_rank_tracker.py
│   ├── options_chain.py         # fetch + filter chain for an underlying
│   ├── contract_selector.py     # pick a strike given delta / DTE targets
│   ├── assignment_handler.py
│   ├── roll_engine.py
│   └── order_executor.py        # multi-leg order submission
├── strategies/
│   ├── __init__.py
│   ├── base_strategy.py         # abstract base
│   ├── covered_call.py
│   ├── cash_secured_put.py
│   ├── vertical_spread.py       # bull put / bear call / etc.
│   ├── iron_condor.py
│   ├── calendar_spread.py
│   └── earnings_iron_condor.py
├── ai/                          # optional — gated by config.ai.enabled
│   ├── __init__.py
│   ├── news_gate.py             # kills/delays entries on bad news
│   ├── earnings_sentiment.py    # post-earnings IV-crush play sizing
│   └── trade_idea_critic.py     # second-opinion check before ordering
├── scheduler/
│   ├── run_scan.py
│   ├── run_risk_check.py
│   ├── run_risk_watch.py
│   ├── run_roll_check.py
│   ├── run_eod_report.py
│   ├── run_backtest.py
│   └── systemd/
│       ├── optionstrader-scan.service
│       ├── optionstrader-scan.timer
│       ├── optionstrader-risk-check.service
│       ├── optionstrader-risk-check.timer
│       ├── optionstrader-risk-watch.service
│       ├── optionstrader-risk-watch.timer
│       ├── optionstrader-roll-check.service
│       ├── optionstrader-roll-check.timer
│       ├── optionstrader-eod-report.service
│       ├── optionstrader-eod-report.timer
│       ├── optionstrader-secrets.service
│       └── optionstrader.env.example
├── dashboard/                   # port HawksTrade's dashboard/, options-extended
│   └── (same structure — see HawksTrade/dashboard/)
├── scripts/
│   ├── fetch_secrets.sh
│   ├── run_optionstrader_job.sh
│   ├── check_systemd.sh
│   └── check_health_linux.py
├── data/                        # runtime — not committed
├── logs/                        # runtime — not committed
├── reports/                     # runtime — not committed
├── tests/
│   ├── test_risk_manager.py
│   ├── test_greeks_calculator.py
│   ├── test_iv_rank_tracker.py
│   ├── test_contract_selector.py
│   ├── test_strategies_*.py
│   └── test_dashboard_*.py
├── cloud-setup/
│   ├── aws-setup-systemd.md     # port from HawksTrade
│   └── dashboard-setup.md       # port from HawksTrade
├── docs/
│   ├── strategies.md            # each strategy, when/why, formulas
│   ├── greeks_primer.md         # concept refresher for future agents
│   ├── assignment_playbook.md
│   └── roll_decision_tree.md
├── requirements.txt
└── requirements-dashboard.txt
```

---

## 4. Config (`config/config.yaml`)

```yaml
# --- MODE ---
mode: paper                      # paper | live
secrets_source: shm

# --- ACCOUNT CONSTRAINTS ---
account:
  options_level: 3               # 1=CC/CSP, 2=long, 3=spreads/condors
  pdt_threshold_usd: 25000       # below this, swing-only
  max_portfolio_risk_pct: 0.20   # sum of max-loss / portfolio_value ceiling
  max_single_position_risk_pct: 0.05
  max_open_strategies: 8
  reserve_cash_pct: 0.15         # keep this fraction uninvested

# --- TRADE-WIDE GATES ---
gates:
  min_dte_entry: 7
  max_dte_entry: 55
  min_open_interest: 100
  min_daily_volume: 10
  max_bid_ask_spread_pct: 0.10   # (ask-bid)/mid
  earnings_blackout_days_before: 5
  close_positions_days_before_earnings: 2
  min_iv_rank_for_short_premium: 30
  max_iv_rank_for_long_premium: 40

# --- UNDERLYINGS ---
underlyings:
  source: config/underlyings.yaml
  refresh_earnings_dates_days: 7 # how often to pull fresh earnings dates

# --- STRATEGIES ---
strategies:

  cash_secured_put:
    enabled: true
    asset_class: options
    target_delta: -0.20          # short put delta
    target_dte: 35
    target_dte_min: 25
    target_dte_max: 45
    profit_take_pct: 0.50        # close at 50% of max profit
    loss_stop_multiple: 2.0      # close if loss = 2× credit received
    roll_threshold_delta: -0.40  # roll down/out if short delta breaches
    max_contracts_per_underlying: 1
    weight: 1.0

  covered_call:
    enabled: true
    asset_class: options
    # triggered by assignment_handler when long stock is assigned-to, or
    # manual stock position marked 'covered-call eligible'
    target_delta: 0.25           # short call delta
    target_dte: 35
    profit_take_pct: 0.50
    loss_stop_multiple: 2.0
    roll_threshold_delta: 0.45
    # Ex-dividend avoidance
    close_before_ex_div_if_itm: true
    weight: 1.0

  vertical_spread:               # bull put (credit) + bear call (credit)
    enabled: true
    asset_class: options
    variant: bull_put_credit     # bull_put_credit | bear_call_credit | bull_call_debit | bear_put_debit
    short_delta: -0.25
    long_delta: -0.10            # further OTM leg
    target_dte: 35
    profit_take_pct: 0.50
    loss_stop_multiple: 1.5      # tighter than CSP because spread max loss is small
    weight: 1.0

  iron_condor:
    enabled: true
    asset_class: options
    put_short_delta: -0.18
    put_long_delta: -0.08
    call_short_delta: 0.18
    call_long_delta: 0.08
    target_dte: 40
    profit_take_pct: 0.40        # condors take profit earlier
    loss_stop_multiple: 2.0
    skip_if_earnings_within_dte: true
    weight: 1.0

  calendar_spread:
    enabled: false               # defer to phase 2 — see §7
    asset_class: options
    weight: 0.5

  earnings_iron_condor:
    enabled: false               # HIGH RISK — only enable after phase 1 proven
    asset_class: options
    require_iv_rank: 60
    dte_range: [7, 14]
    # entered 1 day before earnings, held through, closed morning after
    max_contracts: 1
    weight: 0.3

# --- AI ---
ai:
  enabled: false                 # opt-in; off by default
  provider: anthropic            # anthropic | openai
  news_gate:
    enabled: true
    veto_threshold: 0.7          # confidence that news is materially bearish for the underlying
  earnings_sentiment:
    enabled: false               # phase 2
  trade_idea_critic:
    enabled: true
    veto_on: major_concern       # only vetoes if the AI flags a structural problem

# --- DASHBOARD / SCHEDULING (inherit HawksTrade shape) ---
schedule:
  scan_interval_min: 30
  risk_check_interval_min: 5
  elevated_risk_check_interval_min: 1
  roll_check_interval_min: 60
  eod_report_time: "16:45"       # 16:45 ET after options settlement data
  expiration_exit_cutoff_time: "15:15"  # ET; do not rely on Alpaca's 15:30 expiry handling

reporting:
  trade_log_file: data/trades.csv
  greeks_snapshot_dir: data/greeks_snapshots/
  iv_history_file: data/iv_rank_history.csv
  reports_dir: reports/
  logs_dir: logs/
```

`config/underlyings.yaml`:

```yaml
# Start small. Each underlying adds API calls and monitoring overhead.
underlyings:
  - symbol: SPY
    max_contracts: 5
    strategies_allowed: [cash_secured_put, vertical_spread, iron_condor]
  - symbol: QQQ
    max_contracts: 5
    strategies_allowed: [cash_secured_put, vertical_spread, iron_condor]
  - symbol: IWM
    max_contracts: 3
    strategies_allowed: [iron_condor]
  - symbol: AAPL
    max_contracts: 2
    strategies_allowed: [cash_secured_put, covered_call]
  - symbol: MSFT
    max_contracts: 2
    strategies_allowed: [cash_secured_put, covered_call]
  # Grow this list after 30 days of paper trading with no incidents.
```

---

## 5. Strategies (detailed)

Each strategy section specifies: entry criteria, contract selection,
position sizing, exit criteria, roll criteria, and known failure modes.

### 5.1 Cash-Secured Put (CSP)

**Thesis.** Sell puts on quality underlyings you'd be willing to own.
Collect premium. If assigned, you own the stock at the strike (your
intended entry). If not assigned, you keep the premium.

**Entry.**
- Underlying is in `underlyings.yaml` and strategy is permitted.
- No earnings within 5 days.
- IV rank ≥ 30.
- Account has cash to cover 100 × strike for each contract.
- No existing CSP open on same underlying with overlapping strikes.

**Contract selection.**
- Target DTE: 35 (accept 25–45)
- Target delta: -0.20 (accept -0.15 to -0.25)
- Liquidity gates per §0.6
- Use `core/contract_selector.py::find_by_delta(chain, -0.20, tolerance=0.05)`.

**Sizing.**
- Max loss = strike × 100 − credit (for one contract).
- Contracts capped by: `underlyings.yaml::max_contracts`, portfolio risk cap,
  and cash coverage.

**Exit.**
- Take profit at 50% of credit received (buy to close).
- Stop loss if position value ≥ 2× credit received.
- Time-based: close at 21 DTE regardless (gamma risk rises fast below 21 DTE).

**Roll.**
- If short-put delta breaches -0.40, roll down and out: buy to close current,
  sell to open at lower strike + later expiry, for a credit.
- Never roll for a debit. If you can't get a credit, take the loss.

**Known failure modes.**
- **Gap-down assignment risk.** Underlying gaps to 80% of strike overnight.
  The 3.5% stock stop won't protect you; you're already assigned.
  Mitigation: quality underlyings only, position-size with max-loss = strike
  × 100 − credit (full assignment loss).
- **IV crush post-earnings.** Mitigation: earnings blackout.
- **Assignment into dividend.** Rare for puts (holder exercises by selling,
  so they'd lose the dividend). Low priority.

### 5.2 Covered Call (CC)

**Thesis.** Against a long stock position, sell calls to generate income.
Cap upside at the strike, collect premium. Low risk, modest return.

**Entry.**
- Only triggered when a stock position exists (from CSP assignment or
  manual "covered-call eligible" mark).
- Strike above cost basis *and* above last close.
- No earnings within 5 days.
- IV rank ≥ 30.

**Contract selection.**
- Target DTE: 35 (accept 25–45)
- Target delta: 0.25 (accept 0.20–0.30)

**Sizing.**
- One short call per 100 shares owned.

**Exit.**
- Take profit at 50% of credit.
- Stop loss if short-call delta breaches 0.45 (deep ITM — risk of assignment).

**Roll.**
- If ITM and ex-div date within 5 days and dividend > remaining extrinsic
  value, BUY TO CLOSE IMMEDIATELY. Do not risk assignment for dividend.
- Otherwise, if short delta > 0.45, roll up and out for a credit if
  possible; else close.

**Known failure modes.**
- **Assignment on ex-div day.** Mitigation: `close_before_ex_div_if_itm: true`
  in config; `assignment_handler.py` checks ex-div dates daily.
- **Capped upside regret.** The whole point of CC is capping upside. If you
  wouldn't sell at the strike, don't sell the call.

### 5.3 Vertical Spread (Bull Put Credit example)

**Thesis.** Sell a put at short delta, buy a further-OTM put to define
risk. Like CSP but with a known maximum loss far smaller than full
assignment cost. Best when IV is moderately high.

**Entry.**
- Same underlying/earnings/IV gates as CSP.
- Enough buying power for the spread's max loss.

**Contract selection.**
- Target DTE: 35
- Short delta: -0.25
- Long delta: -0.10 (typically $5–$10 wide for ETFs, proportional for stocks)

**Sizing.**
- Max loss per spread = (width × 100) − credit.
- Contracts capped by `max_single_position_risk_pct`.

**Exit.**
- 50% profit take.
- 1.5× credit stop (tighter than CSP).
- 21 DTE time exit.

**Roll.**
- If short delta breaches -0.40, consider rolling the spread down and out
  for credit. If not possible, close.

**Known failure modes.**
- **Pin risk at expiry.** Never hold to expiry through a strike. Close by
  21 DTE.
- **Early assignment of short leg.** American-style options; if the short
  leg goes deep ITM with no extrinsic value, expect assignment. The long
  leg still protects you, but margin impact can be significant. Mitigation:
  `close_before_ex_div_if_itm: true` and monitor extrinsic value.

### 5.4 Iron Condor

**Thesis.** Sell OTM put + OTM call, buy further-OTM wings for definition.
Profits when underlying stays within a range. Best in high-IV, low-trend
regimes.

**Entry.**
- Same gates.
- Underlying's 20-day realized vol < implied vol of short strikes (market
  is overpricing the range).
- ATR% within historical norms (no breakout regime).

**Contract selection.**
- Target DTE: 40
- Put short delta: -0.18
- Put long delta: -0.08
- Call short delta: 0.18
- Call long delta: 0.08

**Sizing.**
- Max loss = (max(put_width, call_width) × 100) − net credit.

**Exit.**
- 40% profit take (condors should close earlier than credit spreads).
- 2× credit stop.
- 21 DTE time exit.

**Roll.**
- If one side breaches (short delta > 0.40 or < -0.40): DO NOT roll the
  untested side to increase credit. That's a common trap. Close and reassess.

**Known failure modes.**
- **Trend breakout.** Iron condors lose in trends. IVR gate helps but
  doesn't eliminate. Mitigation: hard stop, don't double down.
- **Vega blowup in vol spike.** If VIX gaps from 15 to 30, mark-to-market
  loss is brutal even if underlying hasn't moved. The hard stop catches this.

### 5.5 Calendar Spread (Phase 2)

Deferred. Wait until 30 days of clean paper results from §5.1–5.4 before
enabling. Calendar spreads are vega-positive and theta-positive — they
profit when front-month decays and back-month holds IV. Subtle to risk-manage.

### 5.6 Earnings Iron Condor (High Risk — Phase 2+)

Deferred. Explicit earnings play sized at 1/3 normal position risk. Relies
on IV crush post-release. Requires its own validation cohort (20+ paper
earnings events) before live.

---

## 6. Trade log schema

Extend HawksTrade's CSV schema. New columns in **bold**:

```
timestamp, mode, strategy, underlying,
**strategy_id**,              # groups legs of the same multi-leg order
**leg_number**,                # 1..N within strategy_id
**contract_symbol**,           # OCC symbol e.g. AAPL240119C00180000
**option_type**,               # call | put
**strike**,
**expiration**,                # YYYY-MM-DD
**dte_at_entry**,
side, qty, entry_price, exit_price,
**credit_received_per_spread**,
**max_loss_per_spread**,
stop_loss, take_profit, pnl_pct, exit_reason, order_id, status,
**delta_at_entry**, **theta_at_entry**, **vega_at_entry**,
**iv_at_entry**, **iv_rank_at_entry**,
**underlying_price_at_entry**
```

A single iron condor writes 4 rows (4 legs) sharing a `strategy_id`. The
dashboard aggregates back to the strategy level.

---

## 7. Build phases

### Phase 0 — Foundation (week 1)
- Repo scaffold, `requirements.txt`, CLAUDE.md, README
- Port systemd + secrets pattern from HawksTrade
- `core/alpaca_options_client.py` — minimal wrapper, only read methods first
- `core/greeks_calculator.py` + unit tests (Black-Scholes verified against
  known values from Hull textbook)
- `core/options_chain.py` — fetch + filter
- Unit tests passing.

### Phase 1 — First strategy end-to-end (weeks 2–3)
- Implement `cash_secured_put` strategy
- Implement `core/risk_manager.py` with all §0 gates
- Implement `core/order_executor.py` for single-leg orders
- Implement `run_risk_check.py` (5-minute baseline) and `run_risk_watch.py`
  (1-minute elevated monitoring)
- Backtesting harness: replay 180 days of SPY chain data, simulate CSP
- Paper trade for 14 days
- Go/no-go review before proceeding

### Phase 2 — Spreads (weeks 4–5)
- Multi-leg order submission (`order_class: mleg`)
- Implement `vertical_spread`, `covered_call`
- `assignment_handler.py`
- `roll_engine.py` — automated rolls per strategy rules
- 14-day paper soak

### Phase 3 — Iron condor + dashboard (weeks 6–7)
- Implement `iron_condor`
- Port dashboard from HawksTrade, adapt for options-specific panels:
  - Open strategies with Greeks aggregated
  - Portfolio Greeks (net delta, theta, vega)
  - IV rank heatmap for watched underlyings
  - Upcoming earnings calendar with blackout status
  - P&L today, realized and mark-to-market unrealized
- Cloudflare Tunnel + Access per HawksTrade pattern.
- 14-day paper soak.

### Phase 4 — AI integration (week 8) — OPTIONAL
- `ai/news_gate.py` — checks Alpaca News API + LLM summary before entries
- `ai/trade_idea_critic.py` — LLM second-opinion before order submission
- Both are VETO-only: AI can block a trade, never originate one. See §8.

### Phase 5 — Hardening (weeks 9–10)
- 30-day full paper run with all strategies enabled
- Incident review: any day loss > 3%? any unexpected assignment?
- Only after clean 30-day review: discussion with Arun about flipping to
  live, starting with $1000 of the portfolio capped at 10% of paper sizing.

### Phase 6+ — Advanced (deferred, dated post-phase-5)
- `calendar_spread`, `earnings_iron_condor`
- Dynamic position sizing based on portfolio Greeks (delta-neutralize on entry)
- Multi-account support

---

## 8. AI — what role it actually plays

AI adds value when it is hard to replace with a rule, and is catastrophic
when it replaces a rule that exists for a reason. Scope accordingly.

### 8.1 Allowed AI uses (in order of value)

1. **News gate (veto-only).**
   Before submitting a trade, fetch the last 48h of news headlines for the
   underlying (Alpaca News API or similar). Pass them to an LLM with a
   structured prompt asking:
   - "Is there a news event in the next 7 days with binary outcome risk
     (FDA decision, lawsuit ruling, major product launch, acquisition
     rumor)?"
   - "Is there news in the last 48h that has not yet been priced in and
     that would make selling premium on this name a bad idea (accounting
     fraud allegation, executive departure, guidance cut)?"
   - Output JSON: `{"veto": bool, "confidence": float, "reason": str}`
   - Only vetoes if confidence ≥ `ai.news_gate.veto_threshold`.
   Requires deterministic settings (`temperature=0`) and prompt version
   pinning. Full prompt + response logged per trade.

2. **Trade idea critic (veto-only).**
   After all rule-based gates pass, send the proposed trade (strategy,
   contract, Greeks, risk metrics, reason) to an LLM and ask:
   - "Is there a structural problem with this trade that the rules might
     have missed?" (e.g., earnings moved, ex-div tomorrow the system didn't
     catch, underlying is under acquisition and options will convert)
   - Output JSON: `{"concerns": [str], "severity": "none"|"minor"|"major"}`
   - Only vetoes if severity == "major".

3. **Post-mortem summaries (non-gating).**
   After each trade closes, an LLM writes a 3-line summary into a structured
   log field. Useful for humans reading the trade log later. Does not affect
   decisions.

### 8.2 Disallowed AI uses

- **Price prediction.** LLMs are worse than random at "will SPY be up or
  down tomorrow." Do not ask.
- **Strike selection.** The rules in §5 pick strikes. Do not ask an LLM to
  pick a better one.
- **Sizing.** Position size is a formula. Not an LLM call.
- **Originating trades.** The LLM never says "this is a good trade, take
  it." It can only say "this is a bad trade, skip it."
- **Trading on AI-generated news interpretations without confidence gating.**
  False positives on the news gate are acceptable (skipped trades); false
  negatives are not. Bias toward vetoing.

### 8.3 Operational requirements for AI

- Prompt files version-controlled in `ai/prompts/*.txt`
- Every AI call logged with input + output + model version + latency
- Cost cap in config (e.g., `ai.daily_spend_cap_usd: 5.00`); once hit,
  AI gates are treated as "pass" (don't block trading) and the day's cap
  breach is alerted
- Deterministic sampling (`temperature=0`)
- Timeout: 10s. Beyond timeout, gate is treated as "pass" and the failure
  is logged and alerted.

---

## 9. Risk manager (`core/risk_manager.py`) — the spec

This is the most important file in the codebase. Every order passes through
`pre_trade_check(strategy_order)` before submission.

### 9.1 Pre-trade gates (in order, fail-fast)

1. Mode is paper or live and matches config.
2. Account equity fetched successfully from Alpaca.
3. `account.options_level` ≥ strategy's required level.
4. PDT compliance: if equity < $25k, strategy must be swing-only.
5. Sum of max-loss across open positions + this trade's max-loss ≤
   `max_portfolio_risk_pct` × equity.
6. This trade's max-loss ≤ `max_single_position_risk_pct` × equity.
7. Open strategy count < `max_open_strategies`.
8. Cash available ≥ trade's required buying power + reserve buffer.
9. Every contract in the order passes liquidity gates (§0.6).
10. DTE within [7, 55].
11. No earnings within blackout window.
12. IV rank gate satisfied for this strategy type.
13. No conflicting position on same underlying (no overlapping CSP + vertical
    puts on same strike range).
14. AI veto gate (if enabled): pass.
15. If any gate fails: reject trade, log reason, DO NOT retry.

### 9.2 Continuous risk checks (5 min baseline via `run_risk_check.py`)

The 5-minute loop is the **baseline**, not the whole defense. Options risk
accelerates near expiry and around assignment/ex-dividend events, so the
system must escalate monitoring instead of assuming one scheduler cadence is
always enough.

- Refresh Greeks for all open positions.
- Mark-to-market P&L per position.
- Check profit-take and stop-loss thresholds.
- Check short-leg delta against roll thresholds.
- Check days-to-expiration — flag anything ≤ 21 DTE.
- Check ex-dividend dates for short calls — trigger CC closure if §5.2 rule fires.
- Check upcoming earnings — close positions 2 days before earnings per §0.7.
- Compute portfolio Greeks (aggregate delta, theta, vega).
- Write a snapshot to `data/greeks_snapshots/YYYYMMDD-HHMM.json` for audit.

### 9.2.1 Elevated-risk monitoring (1 min via `run_risk_watch.py`)

Any open strategy enters elevated monitoring when **any** of the following is
true:

- Strategy has `<= 21 DTE`
- Any short leg is ITM
- Short-leg delta has breached the strategy's warning/roll threshold
- Ex-dividend date is next trading day for a short-call position
- Earnings blackout closure window has started
- Position mark-to-market loss exceeds 75% of the strategy stop level
- It is expiration day

When elevated monitoring is active:

- Run risk checks every 1 minute for flagged strategies
- Re-poll open orders until terminal status instead of waiting for the next
  baseline pass
- Emit health/dashboard alerts immediately when the flagged state begins
- Do not open new positions in the same underlying until the flagged state clears

### 9.2.2 Expiration-day rule

The system must not depend on Alpaca's expiration handling as its risk plan.
Alpaca begins evaluating expiring positions around 3:30 PM ET on expiration
day, and may auto-assign or liquidate based on moneyness and buying power.

Required behavior:

- Short-premium positions should normally be closed before expiration day
- If any short-premium position is still open on expiration day, it is
  automatically promoted to elevated monitoring
- No new positions may be opened or rolled into the same-day expiry
- Any remaining expiring short-premium position must be exited by
  `schedule.expiration_exit_cutoff_time`
- Failure to exit by cutoff is a critical alert condition

### 9.3 Daily-loss kill switch

Same pattern as HawksTrade:
- Seed portfolio_value on first read of each NY trading date, persist to
  `data/daily_loss_baseline.json`.
- If portfolio_value drops to baseline × (1 − 0.05) = -5%, halt all new
  entries for the day.
- At -8%, close all short-premium positions regardless of P&L (tail-risk
  escape hatch — a -8% day on options is a sign something structural has
  broken).

---

## 10. Dashboard (ported from HawksTrade)

Panels:

1. **Account.** Equity, buying power, cash, reserve headroom.
2. **Daily loss headroom.** -5% halt, -8% hard close; same status rings as
   HawksTrade.
3. **Open strategies.** One row per `strategy_id`, aggregated across legs:
   underlying, strategy type, DTE, net credit/debit, mark-to-market P&L,
   short-leg delta (worst leg), days to earnings.
4. **Portfolio Greeks.** Net delta, theta, vega, gamma. Visual warning if
   |net delta| > thresholds.
5. **IV rank heatmap.** For every underlying in watchlist: current IV, 52-wk
   low/high, IV rank, trend arrow.
6. **Upcoming earnings.** Next 14 days, colored by blackout/open/closed
   position status.
7. **Today's realized P&L** + last 30 closed strategies.
8. **Per-strategy win rate** (last 30 days).
9. **Health.** systemd unit status (same pattern as HawksTrade).
10. **AI activity.** If AI enabled: number of vetoes today, by reason; cost
    spent today; last LLM call latency.

Security model: identical to HawksTrade (§2.1, §2.2 of `cloud-setup/dashboard-setup.md`).

---

## 11. Testing strategy

### 11.1 Unit tests (must pass before any commit)

- `test_greeks_calculator.py`: verify Black-Scholes against textbook values
  (Hull 11th ed. Appendix B, or CBOE examples)
- `test_risk_manager.py`: every §9.1 gate has positive + negative tests
- `test_contract_selector.py`: delta-targeting returns expected strike
  given synthetic chains
- `test_iv_rank_tracker.py`: rank computation against hand-calculated 52-wk
  series
- `test_strategies_cash_secured_put.py` etc.: each strategy's scan logic
  against synthetic chains
- `test_assignment_handler.py`: correctly detects assignment from position
  deltas
- `test_roll_engine.py`: roll decision tree against synthetic positions
- `test_dashboard_*.py`: port HawksTrade tests; ensure no mutation endpoints,
  CF JWT validation works

### 11.2 Backtesting

Write `scheduler/run_backtest.py` that:
- Takes `--days N --fund $` like HawksTrade
- Replays historical option chains for `underlyings.yaml` symbols
- Simulates each strategy's entries/exits using recorded Greeks and prices
- Outputs per-strategy and aggregate: total return, Sharpe, max DD, win
  rate, avg hold time, worst trade

**Data source for backtests.** Alpaca's historical options data is limited;
supplement with a free dataset like CBOE DataShop samples or purchase
historical options EOD. Don't skip this — options backtests on synthetic
data are worthless.

### 11.3 Paper trading validation

Before any live trading:
- Phase 1: 14 days CSP only
- Phase 2: 14 days CSP + verticals + CC
- Phase 3: 14 days all non-deferred strategies
- Phase 5: 30 days full paper run

Pass criteria per phase:
- Sharpe ≥ 0.5 (annualized)
- Max drawdown ≤ 10%
- No assignments that weren't expected by the strategy
- No risk-manager gate breaches
- No orders that differ from strategy specification

### 11.4 CLAUDE.md validation rules

Same as HawksTrade: `python3 -m unittest discover -v` must pass +
`python3 scheduler/run_backtest.py --days 30 --fund 10000` must complete
and produce a report (not "No trades executed") before any commit.

---

## 12. Cloud deployment (EC2)

Exactly mirror `HawksTrade/cloud-setup/aws-setup-systemd.md` and
`dashboard-setup.md`. Changes only where the app name differs:

- Secrets bucket: `optionstrader/keys` (ALPACA_OPTIONS_PAPER_API_KEY, etc.)
- systemd prefix: `optionstrader-*`
- Config dir: `/etc/optionstrader/`
- Service user: `optionstrader`
- Dashboard user: `optionstrader-dash`
- Dashboard hostname: `opts.<yourdomain>.us`

Rationale: one machine can run both HawksTrade and OptionsTrader without
path or unit-name collisions.

**IAM note.** Grant the EC2 role `secretsmanager:GetSecretValue` on
`arn:*:secret:optionstrader/*` only. Do not share the HawksTrade secret.

---

## 13. Risks & what can go wrong

Be explicit about failure modes so the implementing agent doesn't wave them
away.

### 13.1 Market risks the bot can't fully mitigate
- **Volatility expansion.** Short-premium strategies lose in vol spikes.
  The hard stops (§5.1–5.4) limit the bleed but do not eliminate it.
- **Flash crashes / halts.** Options mark-to-market goes haywire during
  halts; orders may reject. No good mitigation — log and escalate.
- **Assignment into bankruptcy / ticker change.** Rare for ETFs and large
  caps. Mitigation: stick to high-quality underlyings.

### 13.2 Implementation risks the agent must actively avoid
- **Greeks drift.** Alpaca may or may not return Greeks directly. If
  computing locally via Black-Scholes, use actual risk-free rate and
  dividend yield; wrong inputs make Greeks wrong, which makes every decision
  wrong. Unit test against known CBOE values.
- **OCC symbol parsing.** The format is `UNDERLYING + YYMMDD + C/P +
  STRIKE_PADDED`. Parsing mistakes are silent and deadly. Use a vetted
  library (`pytz` for dates, custom parser with tests for strike padding).
- **Multi-leg order partial fills.** An mleg order on Alpaca executes
  atomically (all or none), but fills can arrive late. Poll order status
  with exponential backoff; do not resubmit on timeout.
- **Timezone boundaries.** Options settle at 4:00 PM ET. Any cron running
  close to that boundary must be in ET, not UTC, or convert carefully.
  Pattern already solved in HawksTrade — inherit it.

### 13.3 Process risks
- **Live-mode enablement must be a two-human decision.** Arun + at least
  one reviewer. Never by the implementing agent alone, ever.
- **Roll loops.** An aggressive roll rule can roll the same position 4×
  in a week, compounding losses. `roll_engine.py` must enforce a
  `max_rolls_per_strategy_id: 2` cap.
- **Config drift.** Someone edits config.yaml at 2am. Changes to
  `max_portfolio_risk_pct`, `options_level`, or `mode` must write a row
  to an append-only audit log with the diff.

---

## 14. Success criteria

A successful system by end of Phase 5 shows:

- **Hard metrics (30-day paper):**
  - Sharpe ≥ 0.8 annualized
  - Max drawdown ≤ 8%
  - Win rate on closed strategies ≥ 55%
  - No risk-manager breach
  - No unhandled assignment

- **Operational metrics:**
  - < 5 min mean time to detect (MTTD) a halted scheduler, via dashboard
  - Dashboard uptime ≥ 99.5%
  - Zero orders submitted with malformed OCC symbols
  - AI vetoes (if enabled) false-positive rate < 20% (measured by
    subsequent trades that were vetoed then later would have won — human
    reviewed monthly)

- **Code-quality metrics:**
  - 100% of strategy modules have unit tests covering at least the happy
    path + one failure mode
  - Backtest produces a reproducible report given the same seed + date range
  - Every AI call is fully logged and replayable

If any hard metric is missed in Phase 5, do not proceed to live; open a
review.

---

## 15. Open questions for Arun before building

Items I can't decide for you. The implementing agent should flag these
and WAIT for answers before proceeding, because assumptions here drive
everything downstream.

1. **Starting capital.** What portfolio size does this system assume?
   (PDT threshold, position sizing, and strategy selection all depend on
   this.) Example values: $5k, $10k, $25k+, $100k+.
2. **Options approval level on your Alpaca account.** Level 1, 2, or 3?
   (Determines which strategies can be enabled.) Apply now if not yet
   approved — approval can take days.
3. **AI budget.** Willing to pay ~$0.50–$5.00/day for the optional AI gates?
   If no, keep `ai.enabled: false` and the system works rule-only.
4. **Live-trading ambition.** Paper only, or plan to flip to live after
   Phase 5? Answer affects how much effort goes into edge cases vs. speed
   of iteration.
5. **Single-repo or two-account.** Run HawksTrade and OptionsTrader against
   the same Alpaca account, or separate Alpaca accounts? (Recommend
   separate — isolates blast radius and simplifies reconciliation.)

---

## 16. What I'd build in the first 72 hours

If I were the implementing agent, day-by-day:

**Day 1.** Scaffold repo. Port HawksTrade's systemd, secrets, CLAUDE.md,
cloud-setup, dashboard skeleton. Get `optionstrader-secrets.service` running
on a fresh EC2 with placeholder keys.

**Day 2.** Write `core/alpaca_options_client.py` (read-only methods first:
`get_option_chain`, `get_option_snapshot`, `get_account`, `get_positions`).
Write `core/greeks_calculator.py` with Black-Scholes + unit tests against
Hull textbook values.

**Day 3.** Write `core/options_chain.py` + `core/contract_selector.py` with
full test coverage. End of day 3: can run `scripts/scan_spy.py` that prints
"candidate SPY CSP contracts targeting delta -0.20, 35 DTE, liquid, non-earnings."
No orders yet — just the selection logic visible.

That's a reality-check milestone before committing to the rest. If day 3's
output looks right to a human reading it, the plan is on track. If it
doesn't, something in §4–§5 is wrong and we reset before writing more code.

---

## 17. What's NOT in this document

- Exact order-placement API schemas (the agent reads Alpaca docs directly —
  they evolve, pinning a schema here would date this doc)
- Black-Scholes formula (any textbook; Hull is the reference)
- Specific earnings-date API provider (implementer picks; options:
  Alpaca's corporate actions API, yfinance, polygon.io)
- Specific news API provider for AI gate (implementer picks between
  Alpaca News, Benzinga, NewsAPI.org)

The agent has discretion on these pluggable pieces. The architecture above
is what must be held fixed.

---

## Appendix A — HawksTrade patterns to copy verbatim

- `scheduler/systemd/*.service` hardening block (ProtectSystem=strict, etc.)
- `scripts/run_hawkstrade_job.sh` → rename to `run_optionstrader_job.sh`,
  otherwise identical structure (preflight, lock, exec)
- `scripts/fetch_secrets.sh` → rename paths, otherwise identical
- `dashboard/` FastAPI app structure + templates + static assets
- `dashboard/security.py` — Cloudflare JWT validation is universal
- `cloud-setup/aws-setup-systemd.md` and `cloud-setup/dashboard-setup.md` —
  copy, s/HawksTrade/OptionsTrader/g, update secrets bucket name, check step-
  by-step

## Appendix B — What to put in `CLAUDE.md`

- A §0-equivalent "non-negotiables" block at the top
- Daily operating schedule (scan times, risk check times)
- Validation-after-every-change protocol
- Emergency stop procedure: how to halt the bot in one command
  (`sudo systemctl stop 'optionstrader-*.timer'`)

---

*OptionsTrader — built for risk-adjusted return. Options respect rules or
they take the account. Respect the rules.*
