import csv
import tempfile
import unittest
from pathlib import Path

from status_ui import generate_status


class StatusUITests(unittest.TestCase):
    def test_load_trades_uses_shared_csv_lock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trades_path = Path(tmpdir) / "trades.csv"
            with open(trades_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["symbol", "status"])
                writer.writeheader()
                writer.writerow({"symbol": "AAPL", "status": "open"})

            class FakeFcntl:
                LOCK_SH = 1
                LOCK_UN = 2

                def __init__(self):
                    self.operations = []

                def flock(self, _fileno, operation):
                    self.operations.append(operation)

            fake_fcntl = FakeFcntl()
            original_fcntl = generate_status.fcntl
            generate_status.fcntl = fake_fcntl
            self.addCleanup(setattr, generate_status, "fcntl", original_fcntl)

            rows = generate_status.load_trades(trades_path)

            self.assertEqual(rows[0]["symbol"], "AAPL")
            self.assertEqual(fake_fcntl.operations, [fake_fcntl.LOCK_SH, fake_fcntl.LOCK_UN])
            self.assertTrue(trades_path.with_name("trades.csv.lock").exists())


if __name__ == "__main__":
    unittest.main()
