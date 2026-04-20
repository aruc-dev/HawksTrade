import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class CheckSystemdScriptTests(unittest.TestCase):
    def test_timer_validation_uses_timerscalendar_property(self):
        """Valid timers should not fail when direct OnCalendar properties are empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            project = tmp / "project"
            scripts_dir = project / "scripts"
            config_dir = project / "config"
            fake_bin = tmp / "bin"
            unit_dir = tmp / "units"

            scripts_dir.mkdir(parents=True)
            config_dir.mkdir()
            fake_bin.mkdir()
            unit_dir.mkdir()

            shutil.copy2(BASE_DIR / "scripts" / "check_systemd.sh", scripts_dir / "check_systemd.sh")
            (config_dir / "config.yaml").write_text("mode: paper\nsecrets_source: dotenv\n", encoding="utf-8")

            runner = scripts_dir / "run_hawkstrade_job.sh"
            runner.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            runner.chmod(0o755)

            (unit_dir / "hawkstrade-crypto-scan.service").write_text(
                "[Service]\n"
                f"ExecStart={runner} scheduler/run_scan.py --crypto-only\n"
                f"WorkingDirectory={project}\n",
                encoding="utf-8",
            )
            (unit_dir / "hawkstrade-crypto-scan.timer").write_text(
                "[Timer]\n"
                "OnCalendar=hourly\n"
                "Unit=hawkstrade-crypto-scan.service\n",
                encoding="utf-8",
            )

            systemctl = fake_bin / "systemctl"
            systemctl.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -u
                    cmd="${1:-}"
                    shift || true

                    parse_show() {
                      prop=""
                      unit=""
                      while [[ $# -gt 0 ]]; do
                        case "$1" in
                          -p) prop="$2"; shift 2 ;;
                          --value) shift ;;
                          *) unit="$1"; shift ;;
                        esac
                      done
                    }

                    case "$cmd" in
                      list-unit-files)
                        echo "hawkstrade-crypto-scan.service enabled"
                        echo "hawkstrade-crypto-scan.timer enabled"
                        ;;
                      show)
                        parse_show "$@"
                        case "${prop}:${unit}" in
                          FragmentPath:*) echo "${FAKE_UNIT_DIR}/${unit}" ;;
                          LoadState:*) echo "loaded" ;;
                          ExecStart:hawkstrade-crypto-scan.service)
                            echo "{ path=${FAKE_RUNNER}; argv[]=${FAKE_RUNNER} scheduler/run_scan.py --crypto-only ; }"
                            ;;
                          User:hawkstrade-crypto-scan.service) echo "" ;;
                          WorkingDirectory:hawkstrade-crypto-scan.service) echo "${FAKE_PROJECT_ROOT}" ;;
                          TimersCalendar:hawkstrade-crypto-scan.timer)
                            echo "{ OnCalendar=hourly ; next_elapse=n/a }"
                            ;;
                          TimersMonotonic:hawkstrade-crypto-scan.timer) echo "" ;;
                          OnCalendar:hawkstrade-crypto-scan.timer) echo "" ;;
                          OnBootSec:hawkstrade-crypto-scan.timer) echo "" ;;
                          Unit:hawkstrade-crypto-scan.timer) echo "hawkstrade-crypto-scan.service" ;;
                          NextElapseUSecRealtime:hawkstrade-crypto-scan.timer) echo "Mon 2026-04-20 01:00:00 UTC" ;;
                          LastTriggerUSec:hawkstrade-crypto-scan.timer) echo "Mon 2026-04-20 00:00:00 UTC" ;;
                          ActiveState:hawkstrade-crypto-scan.service) echo "inactive" ;;
                          SubState:hawkstrade-crypto-scan.service) echo "dead" ;;
                          Result:hawkstrade-crypto-scan.service) echo "success" ;;
                          ExecMainStatus:hawkstrade-crypto-scan.service) echo "0" ;;
                          ExecMainCode:hawkstrade-crypto-scan.service) echo "1" ;;
                          InactiveEnterTimestamp:hawkstrade-crypto-scan.service) echo "Mon 2026-04-20 00:00:05 UTC" ;;
                          *) echo "" ;;
                        esac
                        ;;
                      cat)
                        cat "${FAKE_UNIT_DIR}/$1"
                        ;;
                      *)
                        echo "unexpected systemctl call: $cmd $*" >&2
                        exit 1
                        ;;
                    esac
                    """
                ),
                encoding="utf-8",
            )
            systemctl.chmod(0o755)

            fake_python = fake_bin / "python3"
            fake_python.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "${1:-}" == "-c" ]]; then
                      if [[ "${2:-}" == *"['ok']"* ]]; then
                        echo "True"
                      elif [[ "${2:-}" == *"len(json.loads"* ]]; then
                        echo "0"
                      else
                        echo "unsupported python -c call" >&2
                        exit 1
                      fi
                    elif [[ "${1:-}" == "-" ]]; then
                      if [[ "$#" -eq 1 ]]; then
                        echo '{"ok": true, "mode_status": "ACTIVE", "portfolio_value": 100000.0, "cash": 50000.0, "buying_power": 200000.0, "positions": [], "total_pl": 0.0, "position_count": 0}'
                      else
                        echo "        Account status : ACTIVE"
                        echo "        Portfolio value: $    100,000.00"
                        echo "        Cash           : $     50,000.00"
                        echo "        Buying power   : $    200,000.00"
                        echo "        Open positions : 0"
                        echo "        Total unreal P/L: $        +0.00"
                      fi
                    else
                      echo "unsupported python call: $*" >&2
                      exit 1
                    fi
                    """
                ),
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
                    "FAKE_UNIT_DIR": str(unit_dir),
                    "FAKE_RUNNER": str(runner),
                    "FAKE_PROJECT_ROOT": str(project),
                }
            )

            result = subprocess.run(
                ["bash", str(scripts_dir / "check_systemd.sh")],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("hawkstrade-crypto-scan.timer: schedule='{ OnCalendar=hourly", output)
        self.assertNotIn("no OnCalendar", output)
        self.assertNotIn("[FAIL] hawkstrade-crypto-scan.timer", output)


if __name__ == "__main__":
    unittest.main()
