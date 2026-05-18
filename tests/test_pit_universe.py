import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from screener.pit_universe import PITUniverseBuilder
from scheduler.run_backtest import _backtest_scan_universe


def _csv(tmpdir: str) -> Path:
    path = Path(tmpdir) / "universe.csv"
    path.write_text(
        "\n".join(
            [
                "symbol,added_date,removed_date,ipo_date,first_liquid_date,delisted_date,source,notes",
                "OLD,2000-01-01,2020-01-01,,,,index,removed member",
                "NEW,2020-01-01,,,,,index,new member",
                "IPO,,,2024-01-01,2024-04-01,,non_index,ipo grace",
                "DEAD,2000-01-01,,,,2022-01-01,index,delisted",
                "FLASH,,,2024-05-01,2024-05-15,2024-05-20,non_index,short-lived member",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class PITUniverseTests(unittest.TestCase):
    def test_members_as_of_respects_add_and_remove_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PITUniverseBuilder(_csv(tmpdir))

            before_add = builder.members_as_of("2019-12-31")
            on_add = builder.members_as_of("2020-01-01")

        self.assertIn("OLD", before_add)
        self.assertNotIn("NEW", before_add)
        self.assertNotIn("OLD", on_add)
        self.assertIn("NEW", on_add)

    def test_non_index_members_respect_liquidity_or_ipo_grace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PITUniverseBuilder(_csv(tmpdir))

            before_liquid = builder.members_as_of("2024-03-31")
            after_liquid = builder.members_as_of("2024-04-01")

        self.assertNotIn("IPO", before_liquid)
        self.assertIn("IPO", after_liquid)

    def test_delisted_members_are_excluded_after_delist_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PITUniverseBuilder(_csv(tmpdir))

            before_delist = builder.members_as_of("2021-12-31")
            after_delist = builder.members_as_of("2022-01-01")

        self.assertIn("DEAD", before_delist)
        self.assertNotIn("DEAD", after_delist)

    def test_members_between_includes_short_lived_non_index_overlap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PITUniverseBuilder(_csv(tmpdir))

            start_members = builder.members_as_of("2024-05-01")
            end_members = builder.members_as_of("2024-05-31")
            window_members = builder.members_between("2024-05-01", "2024-05-31")

        self.assertNotIn("FLASH", start_members)
        self.assertNotIn("FLASH", end_members)
        self.assertIn("FLASH", window_members)

    def test_backtest_scan_universe_filters_screener_result_by_pit_membership(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PITUniverseBuilder(_csv(tmpdir))

            class Strategy:
                asset_class = "stocks"

            class Screener:
                def get_universe(self, as_of_date=None):
                    return ["OLD", "NEW", "IPO"]

            universe = _backtest_scan_universe(
                Strategy(),
                {"stocks": {"scan_universe": ["OLD", "NEW", "IPO"]}, "crypto": {"scan_universe": []}},
                screener=Screener(),
                screener_enabled=True,
                pit_universe=builder,
                current_date=datetime(2019, 12, 31, tzinfo=timezone.utc),
                market_open=True,
            )

        self.assertEqual(universe, ["OLD"])


if __name__ == "__main__":
    unittest.main()
