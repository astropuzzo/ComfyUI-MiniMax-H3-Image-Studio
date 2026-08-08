from pathlib import Path
import unittest

from scripts.validate_release import validate_repo


class ReleaseArtifactTests(unittest.TestCase):
    def test_release_artifacts(self):
        validate_repo(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    unittest.main()
