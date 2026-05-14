# Weekly Stock Scan Universe Update Prompt

Use this prompt to refresh `config/config.yaml` `stocks.scan_universe` once per week.

```text
You are helping maintain the HawksTrade paper-trading stock scan universe.

Goal:
Update the hardcoded `stocks.scan_universe` fallback list for the coming week. This list is always merged with the dynamic screener output, so it should contain high-priority, liquid symbols that are worth scanning even when the live screener is narrow or temporarily unavailable.

Context to gather first:
1. Read the current `config/config.yaml` `stocks.scan_universe`, `screener` settings, enabled stock strategies, and `config/sectors.json`.
2. Pull the last 5 to 10 trading days of HawksTrade trade logs and strategy P/L by symbol and strategy.
3. Run the HawksTrade screener using current market data and record the top dynamic candidates with score, 20-day dollar volume, ATR%, trend ratio, and 20-day return.
4. Check current weekly market context from reliable market sources. Note whether leadership is broad or concentrated, whether semiconductors/AI are extended, and whether macro conditions are risk-on or defensive.

Selection rules:
1. Prefer symbols that match the screener profile: price above $10, 20-day average dollar volume above $50M, ATR between roughly 1.2% and 6%, close above the 50-day trend, and 20-day return between -5% and +35%.
2. Keep broad market and sector ETFs that improve coverage: SPY, QQQ, and the strongest current sector/theme ETFs.
3. Include a small number of mega-cap or sector-leader names when they are highly liquid by dollar volume, even if they fail the screener only because share volume is below 1M.
4. Avoid forcing low-priced, low-liquidity, below-trend, or extreme ATR names into the hardcoded list.
5. Avoid chasing blow-off moves. If 20-day return is above 35% or trend ratio is above 1.30, include the symbol only if there is a clear reason and flag it as high risk.
6. Remove symbols with recent poor HawksTrade behavior unless the current setup has materially improved.
7. Keep the list around 30 to 40 symbols to control API usage and avoid diluting strategy scans.
8. For every new symbol, update `config/sectors.json` so momentum sector controls do not treat it as an unknown pseudo-sector.

Output required:
1. A proposed YAML replacement for `stocks.scan_universe`.
2. A table of additions and removals with one-line reasons.
3. A list of high-risk symbols that were considered but excluded.
4. Any required `config/sectors.json` updates.
5. Validation commands to run before deployment:
   - `python -m json.tool config/sectors.json >/dev/null`
   - `python - <<'PY'
from core.config_loader import get_config
cfg = get_config()
print(len(cfg["stocks"]["scan_universe"]), cfg["stocks"]["scan_universe"])
PY`
   - `.venv/bin/python -m unittest discover -v`

Important:
Do not change trading mode, live/paper settings, position sizing, stop-loss, or other risk parameters unless explicitly approved.
```
