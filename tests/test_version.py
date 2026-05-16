import re
import unittest
from pathlib import Path

from core.version import __release_date__, __version__


class VersionMetadataTests(unittest.TestCase):
    def test_changelog_exists(self):
        changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
        self.assertTrue(changelog.exists())
        self.assertIn("# HawksTrade Changelog", changelog.read_text())

    def test_version_is_semver_string(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")

    def test_release_date_is_iso_date(self):
        self.assertTrue(re.match(r"^\d{4}-\d{2}-\d{2}$", __release_date__))


if __name__ == "__main__":
    unittest.main()
