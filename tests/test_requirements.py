import re
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _requirement_names(path: Path) -> set[str]:
    names = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


class RequirementsTests(unittest.TestCase):
    def test_root_requirements_include_hawkscapitol_runtime_dependencies(self):
        root_names = _requirement_names(BASE_DIR / "requirements.txt")
        capitol_names = _requirement_names(BASE_DIR / "integrations" / "HawksCapitol" / "requirements.txt")

        self.assertFalse(capitol_names - root_names)


if __name__ == "__main__":
    unittest.main()
