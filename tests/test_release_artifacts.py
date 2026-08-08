from pathlib import Path
import unittest

from scripts.extract_release_notes import extract_release_notes
from scripts.validate_release import validate_repo


class ReleaseArtifactTests(unittest.TestCase):
    def test_release_artifacts(self):
        validate_repo(Path(__file__).resolve().parents[1])

    def test_registry_release_notes_are_extractable(self):
        changelog = "# Changelog\n\n## [15.0.0] - 2026-08-08\n\n- Current\n\n## [14.0.0]\n\n- Previous\n"
        self.assertEqual(extract_release_notes(changelog, "15.0.0"), "- Current")
        self.assertEqual(extract_release_notes(changelog, "14.0.0"), "- Previous")


if __name__ == "__main__":
    unittest.main()
