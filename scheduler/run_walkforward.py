"""
HawksTrade - Rolling Window Validation
======================================
Runs repeated historical backtest windows to check whether a strategy profile is
stable across different market regimes.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.config_loader import get_config  # noqa: E402
from scheduler.run_backtest import run_backtest  # noqa: E402


@dataclass(frozen=True)
class WalkForwardThresholds:
    min_return_pct: float
    max_drawdown_pct: float
    min_profit_factor: float
    min_trades: int
    min_pass_rate: float
    min_daily_sharpe: float | None = None


def _parse_end_date(value: str | None) -> datetime:
    if value:
        return datetime.strptime(value, "%m/%d/%Y").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timedelta(days=2)


def _format_end_date(value: datetime) -> str:
    return value.strftime("%m/%d/%Y")


def _format_report_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def build_rolling_windows(*, end_date: str | None, windows: int, step_days: int) -> list[str]:
    if windows <= 0:
        raise ValueError("windows must be positive")
    if step_days <= 0:
        raise ValueError("step_days must be positive")
    latest_end = _parse_end_date(end_date)
    return [
        _format_end_date(latest_end - timedelta(days=step_days * offset))
        for offset in range(windows - 1, -1, -1)
    ]


def _format_pct(value: float) -> str:
    return f"{value:+.2%}"


def _format_ratio(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.2f}"


def _as_strategy_list(value) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, list):
        strategies = [str(part).strip() for part in value if str(part).strip()]
        return strategies or None
    strategies = [part.strip() for part in str(value).split(",") if part.strip()]
    return strategies or None


def _cost_model_from_config(cfg: dict, *, slippage_bps, fee_bps, min_fee_usd) -> dict:
    configured = dict((cfg.get("validation", {}) or {}).get("cost_model", {}) or {})
    return {
        "slippage_bps": configured.get("slippage_bps", 0.0) if slippage_bps is None else slippage_bps,
        "fee_bps": configured.get("fee_bps", 0.0) if fee_bps is None else fee_bps,
        "min_fee_usd": configured.get("min_fee_usd", 0.0) if min_fee_usd is None else min_fee_usd,
    }


def _cost_model_for_level(base_cost_model: dict, cost_level: dict) -> dict:
    cost_model = {
        "slippage_bps": float(base_cost_model.get("slippage_bps", 0.0) or 0.0),
        "fee_bps": float(base_cost_model.get("fee_bps", 0.0) or 0.0),
        "min_fee_usd": float(base_cost_model.get("min_fee_usd", 0.0) or 0.0),
    }
    for key in ("slippage_bps", "fee_bps", "min_fee_usd"):
        if key in cost_level:
            cost_model[key] = float(cost_level[key] or 0.0)
    return cost_model


def _annualized_return(return_pct: float, window_days: int) -> float:
    if window_days <= 0:
        return return_pct
    if return_pct <= -1:
        return -1.0
    return (1 + return_pct) ** (365.0 / window_days) - 1


def window_failures(stats: dict, thresholds: WalkForwardThresholds) -> list[str]:
    failures: list[str] = []
    if stats["return_pct"] < thresholds.min_return_pct:
        failures.append(
            f"return {_format_pct(stats['return_pct'])} < {_format_pct(thresholds.min_return_pct)}"
        )
    if stats["max_drawdown"] < -thresholds.max_drawdown_pct:
        failures.append(
            f"drawdown {_format_pct(stats['max_drawdown'])} exceeds -{thresholds.max_drawdown_pct:.2%}"
        )
    if stats["profit_factor"] < thresholds.min_profit_factor:
        failures.append(
            f"profit_factor {_format_ratio(stats['profit_factor'])} < {thresholds.min_profit_factor:.2f}"
        )
    if int(stats["trades"]) < thresholds.min_trades:
        failures.append(f"trades {stats['trades']} < {thresholds.min_trades}")
    if (
        thresholds.min_daily_sharpe is not None
        and stats["daily_sharpe"] < thresholds.min_daily_sharpe
    ):
        failures.append(
            f"daily_sharpe {stats['daily_sharpe']:.2f} < {thresholds.min_daily_sharpe:.2f}"
        )
    return failures


def profile_window_failures(
    stats: dict,
    thresholds: WalkForwardThresholds,
    *,
    window_days: int,
) -> list[str]:
    annualized = _annualized_return(float(stats["return_pct"]), window_days)
    stats["annualized_return_pct"] = annualized
    failures: list[str] = []
    if annualized < thresholds.min_return_pct:
        failures.append(
            "annualized_return "
            f"{_format_pct(annualized)} < {_format_pct(thresholds.min_return_pct)}"
        )
    if stats["max_drawdown"] < -thresholds.max_drawdown_pct:
        failures.append(
            f"drawdown {_format_pct(stats['max_drawdown'])} exceeds -{thresholds.max_drawdown_pct:.2%}"
        )
    if stats["profit_factor"] < thresholds.min_profit_factor:
        failures.append(
            f"profit_factor {_format_ratio(stats['profit_factor'])} < {thresholds.min_profit_factor:.2f}"
        )
    if int(stats["trades"]) < thresholds.min_trades:
        failures.append(f"trades {stats['trades']} < {thresholds.min_trades}")
    if (
        thresholds.min_daily_sharpe is not None
        and stats["daily_sharpe"] < thresholds.min_daily_sharpe
    ):
        failures.append(
            f"daily_sharpe {stats['daily_sharpe']:.2f} < {thresholds.min_daily_sharpe:.2f}"
        )
    return failures


def _coerce_today(today: datetime | date | None) -> datetime:
    if today is None:
        return datetime.now(timezone.utc)
    if isinstance(today, datetime):
        return today.astimezone(timezone.utc) if today.tzinfo else today.replace(tzinfo=timezone.utc)
    return datetime(today.year, today.month, today.day, tzinfo=timezone.utc)


def _resolve_profile_end_date(raw_value, *, today: datetime, auto_offset_days: int = 2) -> str:
    if str(raw_value).strip().lower() == "auto":
        return _format_end_date(today - timedelta(days=auto_offset_days))
    if isinstance(raw_value, datetime):
        return _format_end_date(_coerce_today(raw_value))
    if isinstance(raw_value, date):
        return _format_end_date(_coerce_today(raw_value))
    if not raw_value:
        raise ValueError("profile window is missing end_date")
    _parse_end_date(str(raw_value))
    return str(raw_value)


def _walkforward_config(cfg: dict) -> dict:
    return ((cfg.get("validation", {}) or {}).get("walkforward", {}) or {})


def get_walkforward_profile(cfg: dict, profile_name: str) -> dict:
    walkforward_cfg = _walkforward_config(cfg)
    profiles = walkforward_cfg.get("profiles", {}) or {}
    if profile_name not in profiles:
        raise ValueError(f"Unknown walk-forward profile: {profile_name}")
    profile_cfg = dict(profiles[profile_name] or {})
    if not profile_cfg.get("cost_levels"):
        raise ValueError(f"Walk-forward profile '{profile_name}' has no cost_levels")
    if not profile_cfg.get("thresholds"):
        raise ValueError(f"Walk-forward profile '{profile_name}' has no thresholds")
    if not profile_cfg.get("pass_rate"):
        raise ValueError(f"Walk-forward profile '{profile_name}' has no pass_rate")
    return profile_cfg


def _thresholds_for_level(profile_cfg: dict, level_name: str) -> WalkForwardThresholds:
    thresholds_by_level = profile_cfg.get("thresholds", {}) or {}
    if level_name not in thresholds_by_level:
        raise ValueError(f"Missing thresholds for walk-forward cost level '{level_name}'")
    pass_rate_by_level = profile_cfg.get("pass_rate", {}) or {}
    if level_name not in pass_rate_by_level:
        raise ValueError(f"Missing pass_rate for walk-forward cost level '{level_name}'")
    raw = thresholds_by_level[level_name] or {}
    return WalkForwardThresholds(
        min_return_pct=float(raw.get("min_return_pct", 0.0)),
        max_drawdown_pct=float(raw.get("max_drawdown_pct", 1.0)),
        min_profit_factor=float(raw.get("min_profit_factor", 0.0)),
        min_trades=int(raw.get("min_trades", 0)),
        min_pass_rate=float(pass_rate_by_level[level_name]),
        min_daily_sharpe=(
            float(raw["min_daily_sharpe"])
            if raw.get("min_daily_sharpe") is not None
            else None
        ),
    )


def _configured_profile_windows(
    profile_cfg: dict,
    *,
    today: datetime | date | None = None,
    oos_only: bool = False,
) -> list[dict]:
    resolved_today = _coerce_today(today)
    default_window_days = int(profile_cfg.get("window_days", 180))
    if default_window_days <= 0:
        raise ValueError("walk-forward window_days must be positive")

    if oos_only:
        oos_cfg = profile_cfg.get("oos_lock") or {}
        window_days = int(oos_cfg.get("window_days", profile_cfg.get("oos_window_days", 60)))
        if window_days <= 0:
            raise ValueError("walk-forward OOS window_days must be positive")
        return [{
            "label": oos_cfg.get("label", "locked_oos"),
            "regime": oos_cfg.get("regime", "Locked out-of-sample"),
            "end_date": _resolve_profile_end_date(
                oos_cfg.get("end_date", "auto"),
                today=resolved_today,
                auto_offset_days=int(oos_cfg.get("auto_offset_days", 2)),
            ),
            "window_days": window_days,
            "oos": True,
        }]

    windows = []
    for raw_window in profile_cfg.get("windows", []) or []:
        label = raw_window.get("label")
        if not label:
            raise ValueError("walk-forward profile window is missing label")
        window_days = int(raw_window.get("window_days", default_window_days))
        if window_days <= 0:
            raise ValueError(f"walk-forward window '{label}' has non-positive window_days")
        windows.append({
            "label": label,
            "regime": raw_window.get("regime", label),
            "end_date": _resolve_profile_end_date(
                raw_window.get("end_date"),
                today=resolved_today,
                auto_offset_days=int(raw_window.get("auto_offset_days", 2)),
            ),
            "window_days": window_days,
            "oos": False,
        })
    if not windows:
        raise ValueError("walk-forward profile has no windows")
    return windows


def _profile_cost_levels(profile_cfg: dict, *, oos_only: bool = False) -> list[dict]:
    cost_levels = [dict(level) for level in profile_cfg.get("cost_levels", [])]
    if not oos_only:
        return cost_levels
    must_pass_at = (profile_cfg.get("oos_lock") or {}).get("must_pass_at")
    if not must_pass_at:
        return cost_levels
    selected = [level for level in cost_levels if level.get("name") == must_pass_at]
    if not selected:
        raise ValueError(f"OOS lock references unknown cost level '{must_pass_at}'")
    return selected


def _profile_summary(records: list[dict], profile_cfg: dict) -> dict:
    summary: dict[str, dict] = {}
    pass_rate_cfg = profile_cfg.get("pass_rate", {}) or {}
    for level in profile_cfg.get("cost_levels", []) or []:
        level_name = level["name"]
        level_records = [record for record in records if record["cost_level"] == level_name]
        if not level_records:
            continue
        passed = sum(1 for record in level_records if record["passed"])
        pass_rate = passed / len(level_records)
        required = float(pass_rate_cfg[level_name])
        summary[level_name] = {
            "passed": passed,
            "total": len(level_records),
            "pass_rate": pass_rate,
            "required": required,
            "result": pass_rate >= required,
        }
    return summary


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "inf" if value > 0 else "-inf"
    return value


def _write_profile_artifacts(
    *,
    records: list[dict],
    profile_name: str,
    run_id: str,
    artifacts_dir: Path | None,
) -> Path:
    target = artifacts_dir or (BASE_DIR / "reports" / "walkforward" / f"{profile_name}_{run_id}")
    target.mkdir(parents=True, exist_ok=True)
    for record in records:
        safe_name = f"{record['cost_level']}_{record['window']['label']}.json"
        payload = {
            "profile": profile_name,
            "cost_level": record["cost_level"],
            "window": record["window"],
            "cost_model": record["cost_model"],
            "stats": record["stats"],
            "failures": record["failures"],
            "passed": record["passed"],
            "per_strategy": record.get("per_strategy", {}),
            "data_coverage": record.get("data_coverage", {}),
        }
        (target / safe_name).write_text(
            json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return target


def _render_summary_table(summary: dict) -> list[str]:
    lines = [
        "| Cost Level | Windows Passed | Pass Rate | Required | Result |",
        "|---|---:|---:|---:|---|",
    ]
    for level_name, record in summary.items():
        result = "PASS" if record["result"] else "FAIL"
        lines.append(
            f"| {level_name} | {record['passed']}/{record['total']} | "
            f"{record['pass_rate']:.1%} | {record['required']:.1%} | {result} |"
        )
    return lines


def _render_profile_report(
    *,
    profile_name: str,
    profile_cfg: dict,
    records: list[dict],
    summary: dict,
    run_id: str,
    oos_only: bool,
    artifact_dir: Path | None = None,
    regression_issue_output: str | None = None,
) -> str:
    if oos_only:
        title = "OOS Walk-Forward Report"
    elif profile_name == "master":
        title = "Master Walk-Forward Report"
    else:
        title = f"{profile_name.replace('_', ' ').title()} Walk-Forward Report"
    binding_level = (profile_cfg.get("oos_lock") or {}).get("must_pass_at", "stressed")
    command = f"python3 scheduler/run_walkforward.py --profile {profile_name}"
    if oos_only:
        command += " --oos-only"

    lines = [
        f"# {title} - {_format_report_date(datetime.now(timezone.utc))}",
        "",
        f"- Profile: `{profile_name}`",
        f"- Generated: `{run_id}` UTC",
        f"- Reproduction command: `{command}`",
        f"- Binding capital-scaling level: `{binding_level}`",
        "",
        "## Summary",
        "",
    ]
    lines.extend(_render_summary_table(summary))
    lines.extend([
        "",
        "## Per-Window Detail",
        "",
        "| Cost | Window | Regime | End Date | Days | Return | Annualized | Max DD | PF | Trades | Win | Sharpe | Result | Notes |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ])

    for record in records:
        stats = record["stats"]
        window = record["window"]
        result = "PASS" if record["passed"] else "FAIL"
        notes = "; ".join(record["failures"])
        lines.append(
            f"| {record['cost_level']} | {window['label']} | {window['regime']} | "
            f"{window['end_date']} | {window['window_days']} | "
            f"{_format_pct(stats['return_pct'])} | "
            f"{_format_pct(stats['annualized_return_pct'])} | "
            f"{_format_pct(stats['max_drawdown'])} | "
            f"{_format_ratio(stats['profit_factor'])} | {stats['trades']} | "
            f"{stats['win_rate']:.1%} | {stats['daily_sharpe']:.2f} | {result} | {notes} |"
        )

    attribution_level = binding_level if any(
        record["cost_level"] == binding_level for record in records
    ) else records[0]["cost_level"]
    lines.extend([
        "",
        f"## Per-Strategy Attribution ({attribution_level} cost)",
        "",
        "| Window | Strategy | Trades | Win | Avg P&L | Total P&L | Best | Worst | PF |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for record in records:
        if record["cost_level"] != attribution_level:
            continue
        for strategy, stats in sorted((record.get("per_strategy") or {}).items()):
            lines.append(
                f"| {record['window']['label']} | {strategy} | {stats['trades']} | "
                f"{stats['win_rate']:.1%} | {_format_pct(stats['avg_pnl_pct'])} | "
                f"${stats['total_pnl']:,.2f} | {_format_pct(stats['best_pnl_pct'])} | "
                f"{_format_pct(stats['worst_pnl_pct'])} | {_format_ratio(stats['profit_factor'])} |"
            )

    if len(lines) >= 2 and lines[-1].startswith("|---"):
        lines.append("| n/a | n/a | 0 | 0.0% | +0.00% | $0.00 | +0.00% | +0.00% | 0.00 |")

    lines.extend([
        "",
        "## Data Caveats",
        "",
    ])
    missing_lines = []
    execution_notes = set()
    for record in records:
        coverage = record.get("data_coverage") or {}
        missing = coverage.get("missing_history_symbols") or []
        if missing:
            preview = ", ".join(missing[:12])
            suffix = "..." if len(missing) > 12 else ""
            missing_lines.append(
                f"- {record['window']['label']} / {record['cost_level']}: "
                f"{len(missing)} missing-history symbols ({preview}{suffix})"
            )
        for note in (record.get("stats") or {}).get("execution_notes", []) or []:
            execution_notes.add(note)

    if missing_lines:
        lines.extend(missing_lines)
    else:
        lines.append("- No missing-history symbols were reported by the backtest payloads.")
    if execution_notes:
        lines.append("")
        lines.append("Backtest semantics:")
        lines.extend(f"- {note}" for note in sorted(execution_notes))
    if artifact_dir:
        try:
            artifact_text = str(artifact_dir.relative_to(BASE_DIR))
        except ValueError:
            artifact_text = str(artifact_dir)
        lines.append("")
        lines.append(f"Raw JSON artifacts: `{artifact_text}`")
    if not oos_only:
        lines.extend([
            "",
            "Locked OOS note: the latest held-out period is intentionally excluded from "
            "the master run. Run `python3 scheduler/run_walkforward.py --profile "
            f"{profile_name} --oos-only` when ready for final validation.",
        ])
    if regression_issue_output:
        lines.extend([
            "",
            "## Beads Regression Issue",
            "",
            "```",
            regression_issue_output.strip(),
            "```",
        ])

    return "\n".join(lines) + "\n"


def _maybe_file_regression_issue(
    *,
    profile_name: str,
    summary: dict,
    profile_cfg: dict,
    report_path: Path | None,
    force: bool | None,
) -> str | None:
    regression_cfg = profile_cfg.get("regression_issue", {}) or {}
    enabled = bool(regression_cfg.get("auto_file", False)) if force is None else bool(force)
    if not enabled:
        return None

    binding_level = (profile_cfg.get("oos_lock") or {}).get("must_pass_at", "stressed")
    binding_summary = summary.get(binding_level)
    if not binding_summary or binding_summary["result"]:
        return None
    if not shutil.which("bd"):
        return "bd not found; walk-forward regression issue was not filed."

    title = regression_cfg.get(
        "title",
        f"Walk-forward regression below {binding_level} pass-rate threshold",
    )
    body_lines = [
        f"Profile: {profile_name}",
        f"Cost level: {binding_level}",
        (
            "Pass rate: "
            f"{binding_summary['passed']}/{binding_summary['total']} "
            f"({binding_summary['pass_rate']:.1%})"
        ),
        f"Required: {binding_summary['required']:.1%}",
    ]
    if report_path:
        body_lines.append(f"Report: {report_path.relative_to(BASE_DIR)}")
    body_lines.append("")
    body_lines.append("Stop-the-line policy: pause new entries or capital scaling until explained.")
    completed = subprocess.run(
        ["bd", "create", title, "--body", "\n".join(body_lines), "--priority", "1"],
        cwd=BASE_DIR,
        check=False,
        text=True,
        capture_output=True,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        return f"bd create failed with code {completed.returncode}:\n{output.strip()}"
    return output.strip()


def _run_profile_walkforward(
    *,
    profile_name: str,
    initial_fund: float,
    oos_only: bool = False,
    write_report: bool = False,
    report_path: str | Path | None = None,
    write_artifacts: bool = False,
    artifacts_dir: str | Path | None = None,
    today: datetime | date | None = None,
    file_regression_issue: bool | None = None,
) -> tuple[int, str]:
    cfg = get_config()
    walkforward_cfg = _walkforward_config(cfg)
    if walkforward_cfg.get("enabled") is False:
        raise ValueError("validation.walkforward.enabled is false")

    profile_cfg = get_walkforward_profile(cfg, profile_name)
    windows = _configured_profile_windows(profile_cfg, today=today, oos_only=oos_only)
    cost_levels = _profile_cost_levels(profile_cfg, oos_only=oos_only)
    base_cost_model = (cfg.get("validation", {}) or {}).get("cost_model", {}) or {}
    selected_strategies = _as_strategy_list(profile_cfg.get("strategies"))
    use_screener = profile_cfg.get("screener")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    records: list[dict] = []
    for cost_level in cost_levels:
        level_name = cost_level["name"]
        thresholds = _thresholds_for_level(profile_cfg, level_name)
        cost_model = _cost_model_for_level(base_cost_model, cost_level)
        for window in windows:
            result = run_backtest(
                days=window["window_days"],
                initial_fund=initial_fund,
                end_date=window["end_date"],
                use_screener=use_screener,
                enabled_strategies=selected_strategies,
                cost_model=cost_model,
                return_result=True,
                write_quarterly_csv=False,
            )
            stats = dict(result["stats"])
            failures = profile_window_failures(
                stats,
                thresholds,
                window_days=window["window_days"],
            )
            records.append({
                "profile": profile_name,
                "cost_level": level_name,
                "window": dict(window),
                "cost_model": dict(cost_model),
                "thresholds": thresholds,
                "stats": stats,
                "passed": not failures,
                "failures": failures,
                "per_strategy": result.get("per_strategy", {}),
                "data_coverage": result.get("data_coverage", {}),
            })

    profile_summary_cfg = dict(profile_cfg)
    if oos_only:
        profile_summary_cfg["cost_levels"] = cost_levels
    summary = _profile_summary(records, profile_summary_cfg)

    target_report_path = Path(report_path) if report_path else None
    if target_report_path and not target_report_path.is_absolute():
        target_report_path = BASE_DIR / target_report_path
    artifact_dir = None
    if write_artifacts:
        artifact_dir = _write_profile_artifacts(
            records=records,
            profile_name=profile_name,
            run_id=run_id,
            artifacts_dir=Path(artifacts_dir) if artifacts_dir else None,
        )
    regression_issue_output = None
    if not oos_only:
        regression_issue_output = _maybe_file_regression_issue(
            profile_name=profile_name,
            summary=summary,
            profile_cfg=profile_cfg,
            report_path=target_report_path,
            force=file_regression_issue,
        )
    output = _render_profile_report(
        profile_name=profile_name,
        profile_cfg=profile_cfg,
        records=records,
        summary=summary,
        run_id=run_id,
        oos_only=oos_only,
        artifact_dir=artifact_dir,
        regression_issue_output=regression_issue_output,
    )
    if write_report:
        target_report_path = target_report_path or (BASE_DIR / "reports" / f"walkforward_{profile_name}.md")
        target_report_path.parent.mkdir(parents=True, exist_ok=True)
        target_report_path.write_text(output, encoding="utf-8")

    exit_code = 0 if all(level["result"] for level in summary.values()) else 1
    return exit_code, output


def run_walkforward(
    *,
    window_days: int = 180,
    step_days: int = 90,
    windows: int = 4,
    end_date: str | None = None,
    initial_fund: float = 10000.0,
    use_screener: bool | None = None,
    strategies: str | None = None,
    slippage_bps: float | None = None,
    fee_bps: float | None = None,
    min_fee_usd: float | None = None,
    thresholds: WalkForwardThresholds | None = None,
    profile: str | None = None,
    oos_only: bool = False,
    write_report: bool = False,
    report_path: str | Path | None = None,
    write_artifacts: bool = False,
    artifacts_dir: str | Path | None = None,
    today: datetime | date | None = None,
    file_regression_issue: bool | None = None,
) -> tuple[int, str]:
    if profile:
        return _run_profile_walkforward(
            profile_name=profile,
            initial_fund=initial_fund,
            oos_only=oos_only,
            write_report=write_report,
            report_path=report_path,
            write_artifacts=write_artifacts,
            artifacts_dir=artifacts_dir,
            today=today,
            file_regression_issue=file_regression_issue,
        )

    if oos_only:
        raise ValueError("--oos-only requires --profile")
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    cfg = get_config()
    cost_model = _cost_model_from_config(
        cfg,
        slippage_bps=slippage_bps,
        fee_bps=fee_bps,
        min_fee_usd=min_fee_usd,
    )
    selected_strategies = _as_strategy_list(strategies)
    thresholds = thresholds or WalkForwardThresholds(
        min_return_pct=0.0,
        max_drawdown_pct=0.06,
        min_profit_factor=1.0,
        min_trades=5,
        min_pass_rate=0.75,
    )

    records: list[dict] = []
    for window_end in build_rolling_windows(end_date=end_date, windows=windows, step_days=step_days):
        result = run_backtest(
            days=window_days,
            initial_fund=initial_fund,
            end_date=window_end,
            use_screener=use_screener,
            enabled_strategies=selected_strategies,
            cost_model=cost_model,
            return_result=True,
            write_quarterly_csv=False,
        )
        stats = result["stats"]
        failures = window_failures(stats, thresholds)
        records.append({
            "end_date": window_end,
            "passed": not failures,
            "failures": failures,
            "stats": stats,
        })

    passed = sum(1 for record in records if record["passed"])
    pass_rate = passed / len(records) if records else 0.0

    lines = [
        "### HawksTrade Rolling Window Validation",
        f"Window: {window_days} days | Step: {step_days} days | Windows: {len(records)}",
        (
            "Cost model: "
            f"slippage={float(cost_model.get('slippage_bps', 0.0)):.2f} bps, "
            f"fee={float(cost_model.get('fee_bps', 0.0)):.2f} bps, "
            f"min_fee=${float(cost_model.get('min_fee_usd', 0.0)):.2f}"
        ),
        "",
        "| Status | End Date | Return | Max DD | Trades | Win | PF | Sharpe | Notes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in records:
        stats = record["stats"]
        status = "PASS" if record["passed"] else "FAIL"
        notes = "; ".join(record["failures"])
        lines.append(
            f"| {status} | {record['end_date']} | {_format_pct(stats['return_pct'])} | "
            f"{_format_pct(stats['max_drawdown'])} | {stats['trades']} | "
            f"{stats['win_rate']:.1%} | {_format_ratio(stats['profit_factor'])} | "
            f"{stats['daily_sharpe']:.2f} | {notes} |"
        )

    if pass_rate >= thresholds.min_pass_rate:
        lines.append(f"\nRESULT: PASS ({passed}/{len(records)} windows passed, pass_rate={pass_rate:.1%})")
        return 0, "\n".join(lines)
    lines.append(
        f"\nRESULT: FAIL ({passed}/{len(records)} windows passed, "
        f"pass_rate={pass_rate:.1%} < required {thresholds.min_pass_rate:.1%})"
    )
    return 1, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=str, help="Config validation.walkforward profile to run")
    parser.add_argument("--quick", action="store_true", help="Shortcut for --profile quick")
    parser.add_argument("--oos-only", action="store_true", help="Run only the profile's locked OOS window")
    parser.add_argument("--window-days", type=int, default=180)
    parser.add_argument("--step-days", type=int, default=90)
    parser.add_argument("--windows", type=int, default=4)
    parser.add_argument("--end-date", type=str, help="Latest window end date (MM/DD/YYYY)")
    parser.add_argument("--fund", type=float, default=10000.0)
    parser.add_argument("--screener", dest="use_screener", action="store_true")
    parser.add_argument("--no-screener", dest="use_screener", action="store_false")
    parser.set_defaults(use_screener=None)
    parser.add_argument("--strategies", type=str, help="Comma-separated strategy names")
    parser.add_argument("--slippage-bps", type=float)
    parser.add_argument("--fee-bps", type=float)
    parser.add_argument("--min-fee-usd", type=float)
    parser.add_argument("--min-return-pct", type=float, default=0.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=0.06)
    parser.add_argument("--min-profit-factor", type=float, default=1.0)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--min-pass-rate", type=float, default=0.75)
    parser.add_argument("--min-daily-sharpe", type=float)
    parser.add_argument("--report-path", type=str, help="Markdown report path for profile runs")
    parser.add_argument("--write-report", action="store_true", help="Write the rendered profile report")
    parser.add_argument("--no-write-report", action="store_true", help="Do not write the rendered profile report")
    parser.add_argument("--write-artifacts", dest="write_artifacts", action="store_true")
    parser.add_argument("--no-artifacts", dest="write_artifacts", action="store_false")
    parser.set_defaults(write_artifacts=None)
    parser.add_argument("--artifacts-dir", type=str)
    parser.add_argument(
        "--file-regression-issue",
        action="store_true",
        help="File a beads issue when the binding profile pass rate regresses",
    )
    args = parser.parse_args()

    profile = args.profile
    if args.quick:
        if profile and profile != "quick":
            parser.error("--quick cannot be combined with a non-quick --profile")
        profile = "quick"

    write_report = args.write_report
    report_path = args.report_path
    if profile == "master" and not args.no_write_report:
        write_report = True
        report_path = report_path or str(BASE_DIR / "reports" / "walkforward_master.md")
    if args.no_write_report:
        write_report = False
    write_artifacts = args.write_artifacts
    if write_artifacts is None:
        write_artifacts = bool(profile == "master" and not args.oos_only)

    exit_code, output = run_walkforward(
        window_days=args.window_days,
        step_days=args.step_days,
        windows=args.windows,
        end_date=args.end_date,
        initial_fund=args.fund,
        use_screener=args.use_screener,
        strategies=args.strategies,
        slippage_bps=args.slippage_bps,
        fee_bps=args.fee_bps,
        min_fee_usd=args.min_fee_usd,
        thresholds=WalkForwardThresholds(
            min_return_pct=args.min_return_pct,
            max_drawdown_pct=args.max_drawdown_pct,
            min_profit_factor=args.min_profit_factor,
            min_trades=args.min_trades,
            min_pass_rate=args.min_pass_rate,
            min_daily_sharpe=args.min_daily_sharpe,
        ),
        profile=profile,
        oos_only=args.oos_only,
        write_report=write_report,
        report_path=report_path,
        write_artifacts=write_artifacts,
        artifacts_dir=args.artifacts_dir,
        file_regression_issue=True if args.file_regression_issue else None,
    )
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
