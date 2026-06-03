from __future__ import annotations

from pathlib import Path
from typing import Callable


def find_csv_files(workspace: Path) -> list[Path]:
    return sorted(workspace.glob("*.csv"))


def resolve_dataset_paths(dataset_input: str, workspace: Path) -> list[Path]:
    normalized = dataset_input.strip().strip('"')
    if not normalized:
        return find_csv_files(workspace)

    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    candidate = candidate.expanduser()

    if candidate.is_file():
        if candidate.suffix.lower() != ".csv":
            raise ValueError("The selected file must be a .csv file.")
        return [candidate]

    if candidate.is_dir():
        csv_files = sorted(candidate.glob("*.csv"))
        if not csv_files:
            raise FileNotFoundError("The selected folder does not contain any CSV files.")
        return csv_files

    raise FileNotFoundError("The provided dataset path does not exist.")


def prompt_dataset_paths(workspace: Path, input_fn: Callable[[str], str] = input) -> list[Path]:
    while True:
        dataset_input = input_fn(
            "CSV file path or folder path (press Enter to use CSV files in the current workspace): "
        )
        try:
            csv_files = resolve_dataset_paths(dataset_input, workspace)
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc))
            continue
        if not csv_files:
            print("No CSV files were found. Please enter a valid CSV file or folder path.")
            continue
        return csv_files
