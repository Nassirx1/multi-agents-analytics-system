import tempfile
import unittest
from pathlib import Path

from analytics_workflow.pipeline_runtime import prompt_dataset_paths, resolve_dataset_paths


class DatasetPathResolutionTests(unittest.TestCase):
    def test_blank_input_uses_workspace_csv_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "a.csv").write_text("x\n1\n", encoding="utf-8")
            (workspace / "b.csv").write_text("x\n2\n", encoding="utf-8")

            resolved = resolve_dataset_paths("", workspace)

            self.assertEqual([path.name for path in resolved], ["a.csv", "b.csv"])

    def test_resolves_relative_csv_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            datasets = workspace / "datasets"
            datasets.mkdir()
            target = datasets / "sample.csv"
            target.write_text("x\n1\n", encoding="utf-8")

            resolved = resolve_dataset_paths("datasets/sample.csv", workspace)

            self.assertEqual(resolved, [target])

    def test_prompt_retries_until_valid_path_is_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            target = workspace / "sample.csv"
            target.write_text("x\n1\n", encoding="utf-8")
            responses = iter(["missing.csv", "sample.csv"])

            resolved = prompt_dataset_paths(workspace, input_fn=lambda _: next(responses))

            self.assertEqual(resolved, [target])


if __name__ == "__main__":
    unittest.main()
