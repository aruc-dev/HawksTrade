import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPT = BASE_DIR / "scripts" / "run_hawkscapitol_refresh.sh"


class HawksCapitolRefreshScriptTests(unittest.TestCase):
    def _write(self, path: Path, text: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _make_workspace(self, tmp: str) -> tuple[Path, Path]:
        root = Path(tmp)
        hawkstrade = root / "HawksTrade"
        capitol = root / "HawksCapitol"
        self._write(capitol / "scheduler" / "run_scan.py", "print('scan placeholder')\n")
        (hawkstrade / "logs").mkdir(parents=True, exist_ok=True)
        (hawkstrade / "local" / "locks").mkdir(parents=True, exist_ok=True)
        return hawkstrade, capitol

    def _run(self, tmp: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        hawkstrade, capitol = self._make_workspace(tmp)
        env = os.environ.copy()
        env.update(
            {
                "HAWKSTRADE_DIR": str(hawkstrade),
                "HAWKSTRADE_CAPITOL_DIR": str(capitol),
                "HAWKSTRADE_CAPITOL_SIGNAL_PATH": str(capitol / "data" / "signals" / "latest.json"),
                "HAWKSCAPITOL_PYTHON": sys.executable,
            }
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(SCRIPT)],
            cwd=BASE_DIR,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_custom_refresh_command_fails_fast_under_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, capitol = self._make_workspace(tmp)
            fail_script = capitol / "fail.py"
            marker_script = capitol / "marker.py"
            self._write(fail_script, "import sys\nsys.exit(42)\n")
            self._write(marker_script, "from pathlib import Path\nPath('marker-ran').write_text('x')\n")

            result = self._run(
                tmp,
                {
                    "HAWKSTRADE_CAPITOL_REFRESH_COMMAND": (
                        f"{sys.executable} {fail_script}; {sys.executable} {marker_script}"
                    )
                },
            )

            self.assertEqual(result.returncode, 42, result.stdout)
            self.assertNotIn("status=ok", result.stdout)
            self.assertFalse((root / "HawksCapitol" / "marker-ran").exists())

    def test_default_non_dry_run_blocks_sample_data_without_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(tmp)

            self.assertEqual(result.returncode, 70, result.stdout)
            self.assertIn("demo sample data", result.stdout)
            self.assertFalse((Path(tmp) / "HawksCapitol" / "data" / "signals" / "latest.json").exists())

    def test_custom_refresh_must_update_signal_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, capitol = self._make_workspace(tmp)
            signal_file = capitol / "data" / "signals" / "latest.json"
            self._write(signal_file, "[]\n")

            result = self._run(tmp, {"HAWKSTRADE_CAPITOL_REFRESH_COMMAND": "true"})

            self.assertEqual(result.returncode, 70, result.stdout)
            self.assertIn("signal_file_not_updated", result.stdout)


if __name__ == "__main__":
    unittest.main()
