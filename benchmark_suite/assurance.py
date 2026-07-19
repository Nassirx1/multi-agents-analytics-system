"""Fail-closed assurance for benchmark evidence, gates, and score inputs.

The base rubric is deliberately a pure numeric scorer. Acceptance runs must use
this module so an agent cannot earn credit with a made-up evidence string or a
self-asserted hard-gate boolean.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .rubrics import (
    OUTPUT_QUALITY_RUBRICS,
    QualityScore,
    hard_gates_for_route,
    score_output_quality,
)


ASSURANCE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ArtifactInventory:
    root: Path
    records: Mapping[str, ArtifactRecord]

    @property
    def hashes(self) -> frozenset[str]:
        return frozenset(item.sha256 for item in self.records.values())


EVIDENCE_KINDS_BY_SIGNAL: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        criterion.signal: ("deterministic_check", "human_observation")
        for rubric in OUTPUT_QUALITY_RUBRICS.values()
        for criterion in rubric.criteria
    }
)


INDEPENDENT_GATE_EVALUATORS: Mapping[str, str] = MappingProxyType(
    {
        gate: f"benchmark_suite.gate.{gate}"
        for route in ("analytics_report", "html_dashboard")
        for gate in hard_gates_for_route(route)
    }
)


def build_artifact_inventory(root: Path, relative_paths: Sequence[str] | None = None) -> ArtifactInventory:
    """Hash files under ``root`` and reject path escapes before scoring."""

    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise ValueError(f"Artifact inventory root is not a directory: {resolved_root}")
    selected = tuple(relative_paths) if relative_paths is not None else tuple(
        path.relative_to(resolved_root).as_posix()
        for path in sorted(resolved_root.rglob("*"))
        if path.is_file()
    )
    records: dict[str, ArtifactRecord] = {}
    for raw_path in selected:
        relative = Path(str(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Artifact path must be confined and relative: {raw_path}")
        absolute = (resolved_root / relative).resolve()
        if not absolute.is_relative_to(resolved_root) or not absolute.is_file():
            raise ValueError(f"Artifact path is missing or outside the run: {raw_path}")
        normalized = absolute.relative_to(resolved_root).as_posix()
        payload = absolute.read_bytes()
        records[normalized] = ArtifactRecord(
            relative_path=normalized,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
    return ArtifactInventory(root=resolved_root, records=MappingProxyType(records))


def score_output_quality_assured(
    observations: Mapping[str, Any],
    gate_receipts: Mapping[str, Any],
    *,
    route: str,
    inventory: ArtifactInventory,
) -> QualityScore:
    """Resolve typed evidence and independent gate receipts before scoring.

    Invalid evidence earns no evidence credit. Invalid or missing gate receipts
    fail closed. The same locator cannot be reused for semantically different
    criteria, which prevents one generic receipt from inflating every score.
    """

    assurance_diagnostics: list[str] = []
    verified_observations: dict[str, Any] = {}
    locator_owners: dict[tuple[str, str], str] = {}
    known_signals = {
        f"{dimension}.{criterion.signal}": criterion.signal
        for dimension, rubric in OUTPUT_QUALITY_RUBRICS.items()
        for criterion in rubric.criteria
    }
    for qualified_signal, signal in known_signals.items():
        raw = observations.get(qualified_signal, observations.get(signal))
        if not isinstance(raw, Mapping):
            raw_value = getattr(raw, "value", raw)
            verified_observations[qualified_signal] = 0
            if _positive_value(raw_value):
                assurance_diagnostics.append(
                    f"{qualified_signal}: assured scoring requires a structured observation with typed evidence."
                )
            continue
        normalized = dict(raw)
        raw_evidence = raw.get("evidence", ())
        if isinstance(raw_evidence, Mapping):
            candidates = (raw_evidence,)
        elif isinstance(raw_evidence, Sequence) and not isinstance(raw_evidence, (str, bytes)):
            candidates = tuple(raw_evidence)
        else:
            candidates = ()
        accepted: list[str] = []
        for index, locator in enumerate(candidates):
            ok, canonical, diagnostic = verify_evidence_locator(locator, inventory, expected_signal=signal)
            if not ok:
                assurance_diagnostics.append(f"{qualified_signal}.evidence[{index}]: {diagnostic}")
                continue
            owner = locator_owners.get(canonical)
            if owner is not None and owner != qualified_signal:
                assurance_diagnostics.append(
                    f"{qualified_signal}.evidence[{index}]: locator is already assigned to semantically different criterion {owner}."
                )
                continue
            locator_owners[canonical] = qualified_signal
            accepted.append(f"{canonical[0]}#{canonical[1]}")
        normalized["evidence"] = accepted
        if _positive_value(normalized.get("value")) and not accepted:
            normalized["value"] = 0
            original = str(normalized.get("diagnostic", "")).strip()
            normalized["diagnostic"] = (
                original + " Positive observation received zero because evidence did not resolve against the confined inventory."
            ).strip()
        verified_observations[qualified_signal] = normalized

    required_gates = hard_gates_for_route(route)
    verified_gates: dict[str, bool] = {}
    for gate in required_gates:
        ok, diagnostic = verify_gate_receipt(gate, gate_receipts.get(gate), inventory, route=route)
        verified_gates[gate] = ok
        if not ok:
            assurance_diagnostics.append(f"hard_gate.{gate}: {diagnostic}")

    result = score_output_quality(
        verified_observations,
        verified_gates,
        required_gates=required_gates,
    )
    return replace(
        result,
        diagnostics=tuple(dict.fromkeys((*assurance_diagnostics, *result.diagnostics))),
    )


def verify_evidence_locator(
    locator: Any,
    inventory: ArtifactInventory,
    *,
    expected_signal: str,
) -> tuple[bool, tuple[str, str], str]:
    """Validate a typed, hash-bound locator and resolve its JSON pointer."""

    empty = ("", "")
    if not isinstance(locator, Mapping):
        return False, empty, "typed evidence locator must be a mapping"
    required = ("path", "sha256", "kind", "selector", "signal")
    missing = [field for field in required if not str(locator.get(field, "")).strip()]
    if missing:
        return False, empty, "missing locator field(s): " + ", ".join(missing)
    relative = Path(str(locator["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        return False, empty, "artifact path is absolute or escapes the run root"
    normalized = relative.as_posix()
    record = inventory.records.get(normalized)
    if record is None:
        return False, empty, "artifact is not present in the confined inventory"
    if str(locator["sha256"]).lower() != record.sha256:
        return False, empty, "artifact hash is stale or does not match inventory"
    if str(locator["signal"]) != expected_signal:
        return False, empty, "locator signal does not match the scored criterion"
    kind = str(locator["kind"])
    if kind not in EVIDENCE_KINDS_BY_SIGNAL.get(expected_signal, ()):
        return False, empty, f"evidence kind {kind!r} is not allowed for {expected_signal}"
    selector = str(locator["selector"])
    try:
        payload = json.loads((inventory.root / normalized).read_text(encoding="utf-8"))
        resolved = _resolve_json_pointer(payload, selector)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        return False, empty, f"selector does not resolve: {type(exc).__name__}"
    if not _meaningful_evidence(resolved, expected_signal):
        return False, empty, "resolved evidence is empty, false, or names a different signal"
    return True, (normalized, selector), ""


def verify_gate_receipt(
    gate: str,
    receipt: Any,
    inventory: ArtifactInventory,
    *,
    route: str,
) -> tuple[bool, str]:
    """Reject raw booleans and circular, stale, or unknown gate assertions."""

    if not isinstance(receipt, Mapping):
        return False, "independent structured gate receipt is required; bare booleans are invalid"
    expected_evaluator = INDEPENDENT_GATE_EVALUATORS.get(gate)
    required = (
        "check_id",
        "passed",
        "evaluator_id",
        "evaluator_version",
        "artifact_sha256",
        "observed_facts",
        "evaluated_at",
        "route",
        "issuer_kind",
    )
    missing = [field for field in required if field not in receipt]
    if missing:
        return False, "gate receipt is missing: " + ", ".join(missing)
    if receipt.get("passed") is not True:
        return False, "independent evaluator did not pass the gate"
    if str(receipt.get("check_id")) != gate:
        return False, "gate receipt check_id mismatch"
    if str(receipt.get("evaluator_id")) != expected_evaluator:
        return False, "gate evaluator is not allow-listed and independent"
    if str(receipt.get("evaluator_version")) != ASSURANCE_VERSION:
        return False, "gate evaluator version is not the frozen assurance version"
    if str(receipt.get("issuer_kind")) != "independent_deterministic":
        return False, "gate receipt is self-asserted or has unknown issuer kind"
    if str(receipt.get("route")) != route:
        return False, "gate receipt route mismatch"
    if str(receipt.get("artifact_sha256", "")).lower() not in inventory.hashes:
        return False, "gate receipt artifact hash is stale or outside the inventory"
    facts = receipt.get("observed_facts")
    if not isinstance(facts, Mapping) or not facts:
        return False, "gate receipt must record observed facts"
    try:
        parsed = datetime.fromisoformat(str(receipt.get("evaluated_at")).replace("Z", "+00:00"))
    except ValueError:
        return False, "gate receipt timestamp is invalid"
    if parsed.tzinfo is None:
        return False, "gate receipt timestamp must include a timezone"
    return True, ""


def _resolve_json_pointer(payload: Any, pointer: str) -> Any:
    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must begin with '/'")
    current = payload
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            current = current[int(token)]
        else:
            raise KeyError(token)
    return current


def _meaningful_evidence(value: Any, expected_signal: str) -> bool:
    if isinstance(value, Mapping):
        if value.get("signal") not in (None, expected_signal):
            return False
        value = value.get("passed", value.get("value", value.get("observation")))
    if value is False or value is None:
        return False
    if isinstance(value, (str, bytes, Sequence, Mapping)) and not value:
        return False
    return True


def _positive_value(value: Any) -> bool:
    if value is True:
        return True
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False
