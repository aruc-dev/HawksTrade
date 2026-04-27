// HawksTrade dashboard client — polling, no mutations.
(function () {
  const POLL_MS = 15000;
  const STALE_RED_MS = 60000;
  let lastRefreshMs = 0;
  let lastStateMs = 0;

  const $ = (id) => document.getElementById(id);

  const money = (n, signed) => {
    const v = Number(n || 0);
    const sign = v > 0 && signed ? "+" : "";
    return sign + "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const pct = (n) => {
    const v = Number(n || 0) * 100;
    return v.toFixed(2) + "%";
  };
  const fmtQty = (n) => Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 4 });
  const fmtHoldDays = (n) => {
    if (n === null || n === undefined || n === "") return "—";
    return Number(n || 0).toFixed(1) + "d";
  };
  const colorFor = (v) => {
    const x = Number(v || 0);
    if (x > 0) return "text-emerald-400";
    if (x < 0) return "text-rose-400";
    return "text-slate-300";
  };

  async function fetchState() {
    try {
      const res = await fetch("/api/state", { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      render(data);
      lastStateMs = Date.now();
    } catch (e) {
      $("refresh-status").textContent = "fetch error: " + e.message;
      $("refresh-status").className = "text-rose-400";
    }
    lastRefreshMs = Date.now();
    updateRefreshTicker();
  }

  function updateRefreshTicker() {
    const age = Date.now() - lastStateMs;
    if (lastStateMs === 0) return;
    const s = Math.floor(age / 1000);
    const el = $("refresh-status");
    el.textContent = "refreshed " + s + "s ago";
    el.className = age > STALE_RED_MS ? "text-rose-400" : "text-slate-400";
  }

  function render(s) {
    // Header
    $("mode-badge").textContent = (s.mode || "?").toUpperCase();
    $("health-dot").className = "ht-dot " + ({
      green: "bg-emerald-400",
      yellow: "bg-amber-400",
      red: "bg-rose-500",
    }[s.health && s.health.status] || "bg-slate-600");

    // Account
    const acct = s.account || {};
    $("acct-portfolio").textContent = money(acct.portfolio_value);
    $("acct-cash").textContent = money(acct.cash);
    $("acct-bp").textContent = money(acct.buying_power);
    const activeCapital = s.active_capital != null ? s.active_capital : null;
    const acEl = $("acct-active-capital");
    acEl.textContent = activeCapital != null ? money(activeCapital) : "—";
    $("acct-active-capital-detail").textContent =
      activeCapital != null ? "max_positions × max_position_pct × portfolio" : "—";

    // Headroom
    const h = s.daily_loss_headroom || {};
    const limit = h.limit_usd || 0;
    const loss = Math.max(0, -(h.delta_usd || 0));
    const used = limit > 0 ? Math.min(100, Math.round((loss / limit) * 100)) : 0;
    const bar = $("headroom-bar");
    bar.style.width = used + "%";
    const headroomColorMap = {
      ok: "bg-emerald-500",
      warn: "bg-amber-400",
      critical: "bg-rose-500",
      tripped: "bg-rose-700",
      unknown: "bg-slate-600",
    };
    const newHeadroomColor = headroomColorMap[h.status] || "bg-slate-600";
    bar.classList.remove(...Object.values(headroomColorMap));
    bar.classList.add(newHeadroomColor);
    $("headroom-status").textContent = (h.status || "unknown");
    $("headroom-status").dataset.status = h.status || "unknown";
    $("headroom-text").innerHTML =
      "baseline " + money(h.baseline_value) +
      " → current <span class=\"" + colorFor(h.delta_usd) + "\">" + money(h.delta_usd, true) +
      " (" + pct(h.delta_pct) + ")</span>" +
      " • remaining " + money(h.remaining_usd);

    // P&L snapshot
    const r = s.realized_7d || s.realized_today || {};
    const u = s.position_summary || {};
    const rEl = $("pnl-realized");
    rEl.textContent = money(r.total_usd, true);
    rEl.className = "ht-kpi-value " + colorFor(r.total_usd);
    $("pnl-realized-detail").textContent =
      (r.trade_count || 0) + " trades • " + (r.wins || 0) + "W / " + (r.losses || 0) + "L";
    const uEl = $("pnl-unrealized");
    uEl.textContent = money(u.total_usd, true);
    uEl.className = "ht-kpi-value " + colorFor(u.total_usd);
    $("pnl-unrealized-detail").textContent =
      "stocks " + money(u.stock_usd, true) + " (" + (u.stock_count || 0) + ") • " +
      "crypto " + money(u.crypto_usd, true) + " (" + (u.crypto_count || 0) + ")";

    // Active days + total realized (all time)
    const activeDays = s.active_days;
    $("pnl-active-days").textContent = activeDays != null ? activeDays + "d" : "—";
    const tr = s.total_realized || {};
    const trEl = $("pnl-total-realized");
    trEl.textContent = tr.total_usd != null ? money(tr.total_usd, true) : "—";
    trEl.className = "ht-kpi-value " + colorFor(tr.total_usd);
    $("pnl-total-realized-detail").textContent = tr.trade_count != null
      ? (tr.trade_count || 0) + " trades • " + (tr.wins || 0) + "W / " + (tr.losses || 0) + "L"
      : "—";

    // Services (per-job health with colored dots)
    const jobs = (s.health && s.health.job_health) || [];
    const servicesDiv = $("services-list");
    if (jobs.length === 0) {
      servicesDiv.innerHTML = "<div class=\"text-sm text-slate-500\">No service data available</div>";
    } else {
      servicesDiv.innerHTML = jobs.map((j) => {
        const dotColor = {
          green: "bg-emerald-400",
          yellow: "bg-amber-400",
          red: "bg-rose-500",
        }[j.status] || "bg-slate-600";
        const lastRun = j.last_run_at ? j.last_run_at.replace("T", " ").slice(0, 19) : "never";
        const note = j.latest_note ? " — " + j.latest_note : "";
        return "<div class=\"ht-service-row\">" +
          "<span class=\"ht-service-dot " + dotColor + "\"></span>" +
          "<span class=\"ht-service-label\">" + escape(j.label) + "</span>" +
          (j.missed_runs ? "<span class=\"ht-service-missed\">" + j.missed_runs + " missed</span>" : "") +
          "<span class=\"ht-service-time\">" + escape(lastRun + note) + "</span>" +
          "</div>";
      }).join("");
    }

    // Positions
    const posBody = $("positions-tbody");
    const positions = s.positions || [];
    $("pos-count").textContent = "(" + positions.length + ")";
    posBody.innerHTML = positions.map((p) => {
      const pnlCls = colorFor(p.unrealized_pl);
      const pnlPctCls = colorFor(p.unrealized_plpc);
      return "<tr>" +
        "<td class=\"text-left\">" + escape(p.symbol) + "</td>" +
        "<td class=\"text-left\">" + escape(p.strategy || "unknown") + "</td>" +
        "<td class=\"text-right mono\">" + fmtHoldDays(p.hold_days) + "</td>" +
        "<td class=\"text-right mono\">" + fmtQty(p.qty) + "</td>" +
        "<td class=\"text-right mono\">" + money(p.avg_entry_price) + "</td>" +
        "<td class=\"text-right mono\">" + money(p.current_price) + "</td>" +
        "<td class=\"text-right mono " + pnlCls + "\">" + money(p.unrealized_pl, true) + "</td>" +
        "<td class=\"text-right mono " + pnlPctCls + "\">" + pct(p.unrealized_plpc) + "</td>" +
      "</tr>";
    }).join("") || "<tr><td colspan=\"8\" class=\"ht-empty-cell\">No open positions</td></tr>";

    // Strategies
    const stratBody = $("strategies-tbody");
    stratBody.innerHTML = (s.strategies || []).map((st) => {
      return "<tr>" +
        "<td class=\"text-left\">" + escape(st.strategy) + "</td>" +
        "<td class=\"text-right mono\">" + (st.count || 0) + "</td>" +
        "<td class=\"text-right mono\">" + pct(st.win_rate) + "</td>" +
        "<td class=\"text-right mono " + colorFor(st.total_usd) + "\">" + money(st.total_usd, true) + "</td>" +
      "</tr>";
    }).join("") || "<tr><td colspan=\"4\" class=\"ht-empty-cell\">No trades in window</td></tr>";

    // Trades
    const trBody = $("trades-tbody");
    trBody.innerHTML = (s.recent_trades || []).map((t) => {
      const pnl = Number(t.pnl_pct || 0);
      return "<tr>" +
        "<td class=\"text-left\">" + escape((t.timestamp || "").replace("T", " ").slice(0, 19)) + "</td>" +
        "<td class=\"text-left\">" + escape(t.symbol || "") + "</td>" +
        "<td class=\"text-left\">" + escape(t.strategy || "") + "</td>" +
        "<td class=\"text-right mono\">" + fmtQty(t.qty) + "</td>" +
        "<td class=\"text-right mono\">" + money(t.entry_price) + "</td>" +
        "<td class=\"text-right mono\">" + money(t.exit_price) + "</td>" +
        "<td class=\"text-right mono " + colorFor(pnl) + "\">" + pct(pnl) + "</td>" +
        "<td class=\"text-left\">" + escape(t.exit_reason || "") + "</td>" +
      "</tr>";
    }).join("") || "<tr><td colspan=\"8\" class=\"ht-empty-cell\">No closed trades</td></tr>";

    // Health details
    const healthStatus = s.health && s.health.status ? s.health.status : "unknown";
    const healthEl = $("health-status");
    healthEl.dataset.status = healthStatus;
    $("health-status").textContent =
      (healthStatus !== "unknown" ? healthStatus.toUpperCase() : "?") +
      (s.alpaca_reachable ? "" : " • Alpaca unreachable");
    const lines = (s.health && s.health.systemd && s.health.systemd.stdout_tail) || [];
    $("health-pre").textContent = lines.join("\n");
    const issues = (s.health && s.health.log_issues) || [];
    $("health-log-issues").textContent = issues.length
      ? issues.map((i) => i.file + " | " + i.level + " | " + i.line).join("\n")
      : "No recent warning/error log lines.";
  }

  function escape(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Kick off
  fetchState();
  setInterval(fetchState, POLL_MS);
  setInterval(updateRefreshTicker, 1000);
})();
