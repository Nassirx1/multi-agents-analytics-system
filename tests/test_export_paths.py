import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analytics_workflow.pipeline_runtime import _preferred_output_path


class PreferredOutputPathTests(unittest.TestCase):
    def test_returns_original_path_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "analytics_report.pdf"
            self.assertEqual(_preferred_output_path(str(output_path)), str(output_path))

    def test_returns_original_path_when_file_is_writable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "analytics_report.pdf"
            output_path.write_bytes(b"existing")
            self.assertEqual(_preferred_output_path(str(output_path)), str(output_path))

    def test_returns_timestamped_path_when_existing_file_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "analytics_report.pdf"
            output_path.write_bytes(b"existing")

            with patch("builtins.open", side_effect=PermissionError("locked")):
                preferred = _preferred_output_path(str(output_path))

            self.assertNotEqual(preferred, str(output_path))
            self.assertTrue(preferred.endswith(".pdf"))
            self.assertIn("analytics_report_", Path(preferred).name)


if __name__ == "__main__":
    unittest.main()
