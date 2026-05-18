import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from core import data_lockup


def _lockup_file(tmpdir: str) -> Path:
    path = Path(tmpdir) / "oos_lockup.json"
    path.write_text(
        json.dumps(
            {
                "current_lockup": {
                    "start_date": "2026-02-15",
                    "end_date": "2026-05-15",
                    "created_at": "2026-02-15T00:00:00+00:00",
                    "last_validation_at": None,
                    "last_validation_outcome": None,
                    "unlock_token": "token-1",
                    "unlock_token_used_at": None,
                },
                "history": [],
            }
        ),
        encoding="utf-8",
    )
    return path


class DataLockupTests(unittest.TestCase):
    def test_clamp_backtest_window_excludes_locked_range_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)

            start, end, note = data_lockup.clamp_backtest_window(
                start_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_dt=datetime(2026, 5, 1, tzinfo=timezone.utc),
                path=path,
            )

        self.assertEqual(start.date().isoformat(), "2026-01-01")
        self.assertEqual(end.date().isoformat(), "2026-02-14")
        self.assertIn("OOS lockup enforced", note)

    def test_oos_validation_allows_locked_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)

            start, end, note = data_lockup.clamp_backtest_window(
                start_dt=datetime(2026, 2, 15, tzinfo=timezone.utc),
                end_dt=datetime(2026, 5, 15, tzinfo=timezone.utc),
                allow_oos=True,
                path=path,
            )

        self.assertEqual(start.date().isoformat(), "2026-02-15")
        self.assertEqual(end.date().isoformat(), "2026-05-15")
        self.assertIsNone(note)

    def test_clamp_backtest_window_consumes_unlock_token_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)

            start, end, note = data_lockup.clamp_backtest_window(
                start_dt=datetime(2026, 2, 15, tzinfo=timezone.utc),
                end_dt=datetime(2026, 5, 15, tzinfo=timezone.utc),
                oos_unlock_token="token-1",
                path=path,
            )

            self.assertEqual(start.date().isoformat(), "2026-02-15")
            self.assertEqual(end.date().isoformat(), "2026-05-15")
            self.assertIsNone(note)
            self.assertFalse(data_lockup.validate_oos_unlock_token("token-1", path=path))

    def test_clamp_backtest_window_does_not_consume_token_without_overlap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)

            start, end, note = data_lockup.clamp_backtest_window(
                start_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_dt=datetime(2026, 2, 14, tzinfo=timezone.utc),
                oos_unlock_token="token-1",
                path=path,
            )

            self.assertEqual(start.date().isoformat(), "2026-01-01")
            self.assertEqual(end.date().isoformat(), "2026-02-14")
            self.assertIsNone(note)
            self.assertTrue(data_lockup.validate_oos_unlock_token("token-1", path=path))

    def test_filter_locked_bars_drops_only_locked_dates(self):
        index = pd.date_range("2026-02-13", periods=5, freq="D", tz=timezone.utc)
        frame = pd.DataFrame({"close": [1, 2, 3, 4, 5]}, index=index)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)
            filtered = data_lockup.filter_locked_bars(frame, path=path)

        self.assertEqual([idx.date().isoformat() for idx in filtered.index], ["2026-02-13", "2026-02-14"])

    def test_filter_locked_bars_consumes_unlock_token_once(self):
        index = pd.date_range("2026-02-13", periods=5, freq="D", tz=timezone.utc)
        frame = pd.DataFrame({"close": [1, 2, 3, 4, 5]}, index=index)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)
            filtered = data_lockup.filter_locked_bars(frame, oos_unlock_token="token-1", path=path)

            self.assertEqual(len(filtered), len(frame))
            self.assertFalse(data_lockup.validate_oos_unlock_token("token-1", path=path))

    def test_filter_locked_bars_does_not_consume_token_without_locked_dates(self):
        index = pd.date_range("2026-02-10", periods=5, freq="D", tz=timezone.utc)
        frame = pd.DataFrame({"close": [1, 2, 3, 4, 5]}, index=index)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)
            filtered = data_lockup.filter_locked_bars(frame, oos_unlock_token="token-1", path=path)

            self.assertEqual(len(filtered), len(frame))
            self.assertTrue(data_lockup.validate_oos_unlock_token("token-1", path=path))

    def test_unlock_token_permits_access_once_when_consumed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)

            self.assertTrue(data_lockup.validate_oos_unlock_token("token-1", consume=True, path=path))
            self.assertFalse(data_lockup.validate_oos_unlock_token("token-1", path=path))

    def test_unlock_token_consume_locks_read_modify_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)
            calls = []
            original_load = data_lockup.load_lockup_metadata
            original_write = data_lockup._write_lockup_metadata

            def load_metadata(path_arg=None):
                calls.append("load")
                return original_load(path_arg)

            def write_metadata(metadata, path_arg=None):
                calls.append("write")
                return original_write(metadata, path_arg)

            with (
                patch.object(data_lockup, "_lock_file", side_effect=lambda _handle: calls.append("lock")),
                patch.object(data_lockup, "_unlock_file", side_effect=lambda _handle: calls.append("unlock")),
                patch.object(data_lockup, "load_lockup_metadata", side_effect=load_metadata),
                patch.object(data_lockup, "_write_lockup_metadata", side_effect=write_metadata),
            ):
                self.assertTrue(data_lockup.validate_oos_unlock_token("token-1", consume=True, path=path))

        self.assertEqual(calls, ["lock", "load", "write", "unlock"])

    def test_report_mentions_locked_date_detects_iso_or_us_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)

            self.assertTrue(data_lockup.report_mentions_locked_date("result for 2026-03-01", path=path))
            self.assertTrue(data_lockup.report_mentions_locked_date("result for 03/01/2026", path=path))
            self.assertFalse(data_lockup.report_mentions_locked_date("result for 2026-02-14", path=path))

    def test_record_oos_validation_rolls_forward_when_new_lockup_is_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)
            metadata = data_lockup.record_oos_validation(
                outcome="passed",
                report_path="reports/oos.md",
                path=path,
                now=datetime(2026, 9, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(metadata["history"][0]["last_validation_outcome"], "passed")
        self.assertEqual(metadata["history"][0]["last_validation_report"], "reports/oos.md")
        self.assertEqual(metadata["current_lockup"]["start_date"], "2026-06-02")
        self.assertEqual(metadata["current_lockup"]["end_date"], "2026-08-30")

    def test_record_oos_validation_is_single_use_for_current_lockup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _lockup_file(tmpdir)
            data_lockup.record_oos_validation(
                outcome="passed",
                path=path,
                now=datetime(2026, 5, 17, tzinfo=timezone.utc),
            )

            with self.assertRaises(ValueError):
                data_lockup.record_oos_validation(
                    outcome="passed_again",
                    path=path,
                    now=datetime(2026, 5, 18, tzinfo=timezone.utc),
                )


if __name__ == "__main__":
    unittest.main()
