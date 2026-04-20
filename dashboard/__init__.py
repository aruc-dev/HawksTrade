"""HawksTrade read-only dashboard.

Exposes system health, open positions, and realized/unrealized P&L behind
Cloudflare Access. This package MUST NOT mutate trading state — no order
placement, no cancellation, no config writes. See dashboard_implementation_plan.md
for the full security model.
"""

__version__ = "0.1.0"
