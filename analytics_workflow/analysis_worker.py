from __future__ import annotations

import json
import os
import pickle
import sys
import traceback
from pathlib import Path
from typing import Any


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _install_audit_policy(output_dir: Path) -> None:
    """Install a process-local, fail-closed policy for generated analysis code.

    The worker is already isolated from the parent process. The audit hook adds a
    second boundary that denies network/process creation and confines data-file
    access to the worker output directory. Python and installed-package files
    remain readable so approved imports and lazy imports continue to work.
    """

    read_roots = {
        Path(sys.base_prefix).resolve(),
        Path(sys.prefix).resolve(),
    }
    windows_root = os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT")
    if windows_root:
        fonts_root = Path(windows_root) / "Fonts"
        if fonts_root.exists():
            read_roots.add(fonts_root.resolve())
    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry)
        lowered = str(candidate).lower()
        if "site-packages" in lowered or "dist-packages" in lowered:
            try:
                read_roots.add(candidate.resolve())
            except OSError:
                continue

    denied_prefixes = (
        "socket.",
        "subprocess.",
    )
    denied_events = {
        "os.system",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "webbrowser.open",
    }

    def allowed_read(path: Path) -> bool:
        return _is_within(path, output_dir) or any(_is_within(path, root) for root in read_roots)

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event in denied_events or event.startswith(denied_prefixes):
            raise PermissionError(f"analysis sandbox blocked audit event: {event}")

        if event == "ctypes.dlopen" and args:
            raw_path = args[0]
            if not raw_path:
                raise PermissionError("analysis sandbox blocked loading the current process as a native library")
            path = Path(os.fsdecode(raw_path))
            if not path.is_absolute():
                path = output_dir / path
            if not allowed_read(path):
                raise PermissionError(f"analysis sandbox blocked native library outside approved roots: {path}")

        if event == "open" and args:
            raw_path = args[0]
            if isinstance(raw_path, int):
                return
            try:
                path = Path(os.fspath(raw_path))
            except TypeError:
                return
            if not path.is_absolute():
                path = output_dir / path
            mode = str(args[1] or "r") if len(args) > 1 else "r"
            flags = int(args[2] or 0) if len(args) > 2 and isinstance(args[2], int) else 0
            writing = any(token in mode for token in ("w", "a", "x", "+")) or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            if writing and not _is_within(path, output_dir):
                raise PermissionError(f"analysis sandbox blocked write outside run directory: {path}")
            if not writing and not allowed_read(path):
                raise PermissionError(f"analysis sandbox blocked read outside run directory: {path}")

        if event in {"os.chdir", "os.listdir", "os.scandir"} and args:
            raw_path = args[0]
            if isinstance(raw_path, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(raw_path))
                if not path.is_absolute():
                    path = output_dir / path
                if not allowed_read(path):
                    raise PermissionError(f"analysis sandbox blocked filesystem access: {path}")

        if event in {"os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.replace"} and args:
            for raw_path in args[:2]:
                if not isinstance(raw_path, (str, bytes, os.PathLike)):
                    continue
                path = Path(os.fsdecode(raw_path))
                if not path.is_absolute():
                    path = output_dir / path
                if not _is_within(path, output_dir):
                    raise PermissionError(f"analysis sandbox blocked mutation outside run directory: {path}")

    sys.addaudithook(audit)


def run_worker(input_path: Path, output_path: Path) -> int:
    try:
        with input_path.open("rb") as handle:
            payload = pickle.load(handle)

        output_dir = Path(payload["output_dir"]).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(output_dir)

        # Import the application and initialize trusted runtime objects before
        # installing the audit hook. Generated code runs only after the hook.
        from analytics_workflow.pipeline_runtime import MultiAgentOrchestrator
        from analytics_workflow.runtime_config import build_runtime_config

        orchestrator = MultiAgentOrchestrator(
            build_runtime_config("worker-openrouter-key", "worker-brave-key"),
            workspace=output_dir,
            create_run_directory=False,
        )
        orchestrator.workflow_state["csv_data"] = payload.get("csv_data", {})
        orchestrator.set_decision_tree_target_column(str(payload.get("decision_tree_target_column", "")))

        _install_audit_policy(output_dir)
        result = orchestrator._execute_code_in_process(str(payload.get("code", "")))
    except BaseException as exc:  # Worker must always return a bounded failure envelope.
        result = {
            "execution_status": "failed",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }

    try:
        output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    except BaseException:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 2:
        return 2
    return run_worker(Path(args[0]), Path(args[1]))


if __name__ == "__main__":
    raise SystemExit(main())
