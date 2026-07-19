from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HIGH_STAKES_TERMS = {
    "mental health",
    "depression",
    "medical",
    "patient",
    "diagnosis",
    "clinical",
    "credit",
    "loan",
    "underwriting",
    "employment",
    "employee",
    "attrition",
}


def normalize_analysis_evidence(analysis_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Correct structured artifact prose when the attached values contradict it."""
    corrections: list[dict[str, Any]] = []
    artifacts = analysis_results.get("analysis_artifacts", []) or []
    target = _target_from_artifacts(artifacts)
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        chart_type = str(artifact.get("chart_type", "")).lower()
        artifact_id = str(artifact.get("artifact_id") or artifact.get("id") or "artifact")
        if chart_type in {"correlation", "heatmap", "correlation_heatmap"}:
            corrected = _correlation_claim(artifact, target)
            existing_finding = str(artifact.get("finding", "")).strip()
            strongest_feature = corrected.split(" has the strongest", 1)[0].lower() if corrected else ""
            should_correct = bool(corrected) and (
                not existing_finding
                or (
                    any(term in existing_finding.lower() for term in ("strongest", "highest correlation", "most correlated"))
                    and strongest_feature not in existing_finding.lower().replace("_", " ")
                )
            )
            if should_correct:
                corrections.append(
                    {
                        "artifact_id": artifact_id,
                        "field": "finding",
                        "previous": str(artifact.get("finding", "")),
                        "corrected": corrected,
                        "reason": "recomputed_from_structured_correlation_rows",
                    }
                )
                artifact["finding"] = corrected
                artifact["takeaway"] = corrected
                _replace_matching_caption(analysis_results, ("correlation", "heatmap"), corrected)
        if chart_type == "decision_tree":
            data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
            features = _tree_features(data)
            feature_text = ", ".join(_human_name(item) for item in features[:3])
            if feature_text:
                corrected_takeaway = f"The exploratory tree splits on {feature_text}."
                if corrected_takeaway != str(artifact.get("takeaway", "")).strip():
                    corrections.append(
                        {
                            "artifact_id": artifact_id,
                            "field": "takeaway",
                            "previous": str(artifact.get("takeaway", "")),
                            "corrected": corrected_takeaway,
                            "reason": "derived_from_fitted_tree_split_nodes",
                        }
                    )
                    artifact["takeaway"] = corrected_takeaway
            test = _score(data.get("test_accuracy") or data.get("test_score"))
            baseline = _score(data.get("baseline_accuracy") or data.get("baseline_score"))
            if test is not None and baseline is not None and test < baseline:
                artifact["finding"] = (
                    f"The exploratory tree scores {test:.1%} accuracy versus a {baseline:.1%} majority baseline; "
                    "use its branches as hypotheses, not predictive lift."
                )
    return corrections


def build_evidence_bundle(workflow_state: dict[str, Any]) -> dict[str, Any]:
    analysis = workflow_state.get("analysis_results", {}) or {}
    corrections = normalize_analysis_evidence(analysis)
    if corrections:
        analysis["_evidence_corrections"] = corrections
    elif isinstance(analysis.get("_evidence_corrections"), list):
        corrections = list(analysis.get("_evidence_corrections") or [])
    outputs = workflow_state.get("agent_outputs", {}) or {}
    datasets = _dataset_sources(workflow_state)
    external_sources = _external_sources(outputs.get("market_researcher", {}) or {})
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in list(analysis.get("analysis_artifacts", []) or []) + list(analysis.get("chart_specs", []) or []):
        if not isinstance(raw, dict):
            continue
        evidence_id = str(raw.get("artifact_id") or raw.get("id") or raw.get("chart_spec_id") or "").strip()
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        data = raw.get("data")
        association = _association_audit(raw, workflow_state, _target_from_artifacts(analysis.get("analysis_artifacts", []) or []))
        claim = _clean_text(raw.get("finding") or raw.get("takeaway"))
        caveat = _artifact_caveat(raw)
        if association:
            p_value = association["p_value"]
            if p_value >= 0.05:
                caution = f"The observed ranking is descriptive; the association test is not statistically reliable (p={p_value:.3f})."
                if caution not in claim:
                    claim = f"{claim} {caution}".strip()
                caveat = caution
                raw["finding"] = claim
                raw["takeaway"] = caution
            else:
                caveat = f"Association test p={p_value:.3f}; effect size and practical importance still require review."
        evidence.append(
            {
                "evidence_id": evidence_id,
                "kind": "model" if str(raw.get("chart_type", "")).lower() == "decision_tree" else "eda",
                "title": _clean_text(raw.get("title")),
                "claim": claim,
                "chart_type": _clean_text(raw.get("chart_type")),
                "metric": _clean_text(raw.get("y_label") or raw.get("y")),
                "cohort": _clean_text(raw.get("x_label") or raw.get("x")),
                "sample_size": association.get("sample_size") if association else _sample_size(data),
                "denominator": association.get("denominator") if association else "",
                "data_reference": _clean_text(raw.get("data_reference")),
                "source_ids": [item["source_id"] for item in datasets],
                "method": association.get("method") if association else _method_for_artifact(raw),
                "confidence": "directional" if association and association.get("p_value", 0) >= 0.05 else _confidence_for_artifact(raw),
                "caveat": caveat,
                "p_value": association.get("p_value") if association else None,
            }
        )
    high_stakes = _is_high_stakes(workflow_state)
    bundle = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "high_stakes": high_stakes,
        "decision_policy": "human_review_required" if high_stakes else "standard_validation",
        "datasets": datasets,
        "external_sources": external_sources,
        "evidence": evidence,
        "corrections": corrections,
    }
    bundle["bundle_hash"] = _stable_hash(
        {key: value for key, value in bundle.items() if key not in {"generated_at", "corrections"}}
    )
    return bundle


def validate_and_sanitize_workflow(workflow_state: dict[str, Any]) -> dict[str, Any]:
    bundle = build_evidence_bundle(workflow_state)
    workflow_state["evidence_bundle"] = bundle
    outputs = workflow_state.get("agent_outputs", {}) or {}
    analysis_state = workflow_state.get("analysis_results", {}) or {}
    issues: list[dict[str, Any]] = [
        dict(item) for item in analysis_state.get("_semantic_validation_issues", []) or [] if isinstance(item, dict)
    ]
    replacements: list[dict[str, Any]] = [
        dict(item) for item in analysis_state.get("_sanitized_recommendations", []) or [] if isinstance(item, dict)
    ]

    for agent_name in ("business_translator", "decision_maker"):
        payload = outputs.get(agent_name)
        if isinstance(payload, dict):
            outputs[agent_name] = _sanitize_payload_language(payload, issues, path=agent_name)

    market = outputs.get("market_researcher")
    if isinstance(market, dict) and _payload_contains_cjk(market):
        market["industry_overview"] = (
            "External market context was captured from search snippets. Review the numbered source pages "
            "before using that context in a decision."
        )
        market["market_findings"] = []
        market["key_trends"] = []
        market["opportunities"] = []
        outputs["market_researcher"] = market
        issues.append(
            {
                "severity": "medium",
                "code": "output_language_normalized",
                "detail": "Non-English market prose was excluded while preserving its source inventory.",
            }
        )

    tree = next((item for item in bundle["evidence"] if item.get("kind") == "model"), None)
    tree_artifact = _tree_artifact(workflow_state)
    tree_features = _tree_features((tree_artifact or {}).get("data", {}))
    candidate_features = _candidate_feature_phrases(workflow_state)
    for agent_name in ("business_translator", "decision_maker"):
        payload = outputs.get(agent_name)
        if isinstance(payload, dict) and bundle["high_stakes"]:
            payload = _sanitize_high_stakes_language(payload, issues, path=agent_name)
        if isinstance(payload, dict) and tree_artifact:
            outputs[agent_name] = _sanitize_model_claims(
                payload,
                tree_artifact,
                tree_features,
                candidate_features,
                issues,
                path=agent_name,
            )
    decision = outputs.get("decision_maker", {}) or {}
    recommendations = decision.get("recommendations", []) or []
    evidence_records = bundle.get("evidence", [])
    sanitized_recommendations: list[dict[str, Any]] = []
    seen_actions: set[str] = set()
    for index, raw in enumerate(recommendations, start=1):
        if not isinstance(raw, dict):
            raw = {"rank": index, "action": _clean_text(raw)}
        item = copy.deepcopy(raw)
        action = _clean_text(item.get("action"))
        evidence_text = _clean_text(item.get("evidence"))
        evidence_ids = _match_evidence_ids(f"{action} {evidence_text}", evidence_records)
        threshold_features = {
            match.group(1).lower()
            for match in re.finditer(r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:>=|<=|>|<)\s*-?\d", action)
        }
        unsupported_thresholds = bool(threshold_features) and not threshold_features.issubset(
            {feature.lower() for feature in tree_features}
        )
        claims_tree = "tree" in f"{action} {evidence_text}".lower()
        unsupported_tree_feature = claims_tree and any(
            token in f"{action} {evidence_text}".lower().replace("_", " ")
            for token in _candidate_feature_phrases(workflow_state)
            if token not in {_human_name(feature) for feature in tree_features}
        )
        if claims_tree and (unsupported_thresholds or unsupported_tree_feature):
            feature_text = ", ".join(_human_name(value) for value in tree_features[:3]) or "the verified tree branches"
            replacement = (
                f"Use the exploratory tree profile ({feature_text}) only to structure a human-reviewed validation study; "
                "do not convert model-scaled splits into operational thresholds without validation."
            )
            replacements.append({"recommendation": index, "previous": action, "corrected": replacement})
            issues.append(
                {
                    "severity": "high",
                    "code": "unsupported_tree_threshold_or_feature",
                    "recommendation": index,
                    "detail": "Recommendation referenced a threshold or feature not supported by fitted tree split nodes.",
                }
            )
            item["action"] = replacement
            item["evidence"] = tree.get("claim", "") if tree else evidence_text
            evidence_ids = [tree["evidence_id"]] if tree else evidence_ids
        item["evidence_ids"] = evidence_ids
        item.setdefault("owner", "accountable business owner with domain-expert oversight")
        item.setdefault("trigger", "approved validation protocol and baseline are documented")
        item.setdefault("target_segment", "the population supported by the cited evidence")
        item.setdefault("validation_metric", _default_validation_metric(bundle))
        item.setdefault("stop_condition", "stop or redesign if safety, fairness, or outcome guardrails deteriorate")
        item["decision_status"] = "human_review_required" if bundle["high_stakes"] else "validation_required"
        action_key = re.sub(r"\W+", " ", _clean_text(item.get("action")).lower()).strip()
        if action_key and action_key in seen_actions:
            rationale_text = _clean_text(item.get("rationale")).lower()
            if "imbalance" in rationale_text or "false negative" in rationale_text:
                item["action"] = (
                    "Quantify precision, recall, false-negative rates, and subgroup performance on fresh data "
                    "before any expert-led pilot."
                )
            else:
                item["action"] = (
                    "Collect a larger, more representative validation sample and document outcome, safety, "
                    "and fairness guardrails before proceeding."
                )
            replacements.append({"recommendation": index, "previous": action, "corrected": item["action"]})
            action_key = re.sub(r"\W+", " ", item["action"].lower()).strip()
        if action_key:
            seen_actions.add(action_key)
        sanitized_recommendations.append(item)
    if isinstance(decision, dict):
        decision["recommendations"] = sanitized_recommendations
        if bundle["high_stakes"]:
            decision["final_recommendation"] = _high_stakes_final_recommendation(decision, tree_features)
            decision["conclusion"] = (
                "The evidence identifies exploratory associations and model segments for expert review. "
                "It does not establish diagnosis, causality, or deployment-ready rules; the next decision is "
                "whether to approve a supervised validation study with safety and fairness gates."
            )
        limitations = decision.get("limitations", []) or []
        for limitation in limitations:
            if not isinstance(limitation, dict):
                continue
            text = _clean_text(limitation.get("limitation"))
            text = re.sub(
                r"cannot\s+is\s+associated\s+with\s+cause-effect\s+relationships?",
                "do not establish cause and effect",
                text,
                flags=re.IGNORECASE,
            )
            limitation["limitation"] = text
            mitigation = _clean_text(limitation.get("mitigation"))
            mitigation = re.sub(r"\s+instead\s+of\s*$", "", mitigation, flags=re.IGNORECASE).rstrip(" ,;:")
            limitation["mitigation"] = mitigation
        outputs["decision_maker"] = decision

    external_snippets = [source for source in bundle["external_sources"] if source.get("evidence_level") == "search_snippet"]
    if external_snippets:
        issues.append(
            {
                "severity": "medium" if not bundle["high_stakes"] else "high",
                "code": "search_snippet_context_only",
                "detail": "External search snippets are context only and cannot support high-impact recommendations.",
            }
        )
    if bundle["corrections"]:
        issues.extend(
            {
                "severity": "high",
                "code": "artifact_claim_corrected",
                "detail": f"Corrected {item['artifact_id']} {item['field']} from structured evidence.",
            }
            for item in bundle["corrections"]
        )
    deduped_issues: list[dict[str, Any]] = []
    seen_issue_keys: set[str] = set()
    for item in issues:
        key = json.dumps(item, sort_keys=True, default=str)
        if key in seen_issue_keys:
            continue
        seen_issue_keys.add(key)
        deduped_issues.append(item)
    issues = deduped_issues
    if isinstance(analysis_state, dict):
        analysis_state["_semantic_validation_issues"] = issues
        analysis_state["_sanitized_recommendations"] = replacements
    blockers = [item for item in issues if item.get("severity") == "blocker"]
    receipt = {
        "schema_version": "1.0",
        "status": "needs_revision" if blockers else "share_with_caveats" if issues else "ready_to_share",
        "high_stakes": bundle["high_stakes"],
        "evidence_bundle_hash": bundle["bundle_hash"],
        "issues": issues,
        "sanitized_recommendations": replacements,
        "required_caveats": _required_caveats(bundle),
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    workflow_state["quality_receipt"] = receipt
    return receipt


def artifact_dependency_hash(workflow_state: dict[str, Any], artifact_type: str) -> str:
    bundle = workflow_state.get("evidence_bundle", {}) or {}
    outputs = workflow_state.get("agent_outputs", {}) or {}
    quality = dict(workflow_state.get("quality_receipt", {}) or {})
    quality.pop("validated_at", None)
    payload = {
        "artifact_type": artifact_type,
        "evidence_bundle_hash": bundle.get("bundle_hash"),
        "decision": outputs.get("decision_maker", {}),
        "business": outputs.get("business_translator", {}),
        "quality": quality,
    }
    return _stable_hash(payload)


def _correlation_claim(artifact: dict[str, Any], target: str) -> str:
    rows = artifact.get("data")
    if not isinstance(rows, list) or not rows:
        return ""
    variable_key = next((key for key in ("variable", "feature", "index", "label") if key in rows[0]), "")
    if not variable_key:
        return ""
    target_key = target if target and target in rows[0] else next(
        (key for key in rows[0] if any(term in str(key).lower() for term in ("target", "label", "outcome", "attrition", "default", "churn", "depression"))),
        "",
    )
    if not target_key:
        return ""
    candidates: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        variable = str(row.get(variable_key, ""))
        if variable == target_key:
            continue
        try:
            value = float(row.get(target_key))
        except (TypeError, ValueError):
            continue
        candidates.append((variable, value))
    if not candidates:
        return ""
    variable, value = max(candidates, key=lambda pair: abs(pair[1]))
    return f"{_human_name(variable)} has the strongest absolute correlation with {_human_name(target_key)} (r={value:.2f})."


def _replace_matching_caption(analysis: dict[str, Any], terms: tuple[str, ...], replacement: str) -> None:
    captions = analysis.get("figure_captions", {})
    if not isinstance(captions, dict):
        return
    for key, value in list(captions.items()):
        text = f"{key} {value}".lower()
        if any(term in text for term in terms):
            captions[key] = replacement


def _dataset_sources(workflow_state: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = workflow_state.get("run_manifest", {}) or {}
    datasets = manifest.get("datasets", []) or []
    if not datasets:
        for name, frame in (workflow_state.get("csv_data", {}) or {}).items():
            if frame is None:
                datasets.append({"name": name, "rows": 0, "columns": 0})
                continue
            datasets.append({"name": name, "rows": len(frame), "columns": len(getattr(frame, "columns", []))})
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(datasets, start=1):
        if not isinstance(item, dict):
            continue
        as_of = _clean_text(item.get("as_of") or item.get("modified_at"))
        path_text = _clean_text(item.get("path"))
        if not as_of and path_text:
            try:
                as_of = datetime.fromtimestamp(Path(path_text).stat().st_mtime, timezone.utc).isoformat()
            except (OSError, ValueError):
                pass
        normalized.append({
            "source_id": f"dataset:{index}",
            "name": _clean_text(item.get("name")),
            "sha256": _clean_text(item.get("sha256")),
            "rows": item.get("rows"),
            "columns": item.get("columns"),
            "as_of": as_of,
        })
    return normalized


def _external_sources(market: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for fallback, item in enumerate(market.get("sources_cited", []) or [], start=1):
        if not isinstance(item, dict):
            continue
        index = item.get("index") or fallback
        sources.append(
            {
                "source_id": f"external:{index}",
                "index": index,
                "title": _clean_text(item.get("title")),
                "url": _clean_text(item.get("url")),
                "published_date": _clean_text(item.get("published_date") or item.get("date")),
                "evidence_level": _clean_text(item.get("evidence_level") or market.get("market_evidence_level")),
            }
        )
    return sources


def _sanitize_payload_language(value: Any, issues: list[dict[str, Any]], path: str) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_payload_language(item, issues, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_payload_language(item, issues, f"{path}[{index}]") for index, item in enumerate(value)]
    if not isinstance(value, str):
        return value
    text = value
    replacements = {
        r"\brepresentative(?:\s+for|\s+of)?": "descriptive of",
        r"\bvalidated starting point\b": "analysis-backed starting point",
        r"\bdata-validated\b": "observed in this dataset",
        r"\bproves?\b": "is associated with",
    }
    for pattern, replacement in replacements.items():
        updated = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if updated != text:
            issues.append({"severity": "medium", "code": "unsupported_language_softened", "path": path})
            text = updated
    return text


def _sanitize_model_claims(
    value: Any,
    tree_artifact: dict[str, Any],
    tree_features: list[str],
    candidate_features: set[str],
    issues: list[dict[str, Any]],
    path: str,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_model_claims(item, tree_artifact, tree_features, candidate_features, issues, f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _sanitize_model_claims(item, tree_artifact, tree_features, candidate_features, issues, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if not isinstance(value, str):
        return value
    lowered = value.lower().replace("_", " ")
    if not any(term in lowered for term in ("tree", "model", "rule")):
        return value
    data = tree_artifact.get("data", {}) if isinstance(tree_artifact.get("data"), dict) else {}
    actual = {_human_name(feature).lower() for feature in tree_features}
    target_phrase = _human_name(data.get("target", "")).lower()
    if target_phrase:
        actual.add(target_phrase)
    unsupported = [feature for feature in candidate_features if _phrase_in_text(feature, lowered) and feature not in actual]
    test = _score(data.get("test_accuracy") or data.get("test_score"))
    baseline = _score(data.get("baseline_accuracy") or data.get("baseline_score"))
    false_lift = baseline is not None and test is not None and test < baseline and any(
        phrase in lowered for phrase in ("better than", "outperform", "predictive lift", "high accuracy")
    )
    threshold_claim = bool(re.search(r"\b[A-Za-z][A-Za-z0-9_ ]*\s*(?:>=|<=|>|<)\s*-?\d", value))
    native_thresholds = any(
        isinstance(node, dict) and node.get("threshold_unit") == "original"
        for node in data.get("nodes", []) or []
    )
    if unsupported or false_lift or (threshold_claim and not native_thresholds):
        issues.append(
            {
                "severity": "high",
                "code": "unsupported_model_claim_replaced",
                "path": path,
                "unsupported_features": unsupported,
            }
        )
        return _safe_model_sentence(tree_features, test, baseline)
    return value


def _sanitize_high_stakes_language(value: Any, issues: list[dict[str, Any]], path: str) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_high_stakes_language(item, issues, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_high_stakes_language(item, issues, f"{path}[{index}]") for index, item in enumerate(value)]
    if not isinstance(value, str):
        return value
    replacements = {
        r"\bA/B testing\b": "an ethically approved controlled evaluation with expert oversight",
        r"\bautomated screening\b": "human-reviewed assessment",
        r"\bpilot intervention program targeting\b": "professionally supervised validation study involving",
    }
    text = value
    for pattern, replacement in replacements.items():
        updated = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if updated != text:
            issues.append({"severity": "high", "code": "high_stakes_language_restricted", "path": path})
            text = updated
    return text


def _safe_model_sentence(features: list[str], test: float | None, baseline: float | None) -> str:
    feature_text = ", ".join(_human_name(item) for item in features[:3]) or "the fitted split features"
    metric_text = ""
    if test is not None and baseline is not None:
        comparison = "below" if test < baseline else "compared with"
        metric_text = f" Accuracy is {test:.1%}, {comparison} the {baseline:.1%} majority baseline."
    return (
        f"The exploratory tree splits on {feature_text}.{metric_text} "
        "Use the branches as hypotheses for human-reviewed validation, not as operational thresholds or automated decisions."
    )


def _match_evidence_ids(text: str, evidence: list[dict[str, Any]]) -> list[str]:
    source = _tokens(text)
    ranked: list[tuple[float, str]] = []
    for item in evidence:
        candidate = _tokens(f"{item.get('title', '')} {item.get('claim', '')}")
        if not candidate:
            continue
        score = len(source & candidate) / max(1, min(len(source), len(candidate)))
        if score >= 0.18:
            ranked.append((score, str(item.get("evidence_id"))))
    return [identifier for _, identifier in sorted(ranked, reverse=True)[:3]]


def _tree_artifact(workflow_state: dict[str, Any]) -> dict[str, Any] | None:
    for item in workflow_state.get("analysis_results", {}).get("analysis_artifacts", []) or []:
        if isinstance(item, dict) and str(item.get("chart_type", "")).lower() == "decision_tree":
            return item
    return None


def _tree_features(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    features: list[str] = []
    for node in data.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        feature = str(node.get("feature", "")).strip()
        if feature and feature not in features:
            features.append(feature)
    return features


def _target_from_artifacts(artifacts: list[Any]) -> str:
    for artifact in artifacts:
        if not isinstance(artifact, dict) or str(artifact.get("chart_type", "")).lower() != "decision_tree":
            continue
        data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
        target = str(data.get("target") or artifact.get("target") or "").strip()
        if target:
            return target
    return ""


def _candidate_feature_phrases(workflow_state: dict[str, Any]) -> set[str]:
    phrases: set[str] = set()
    for frame in (workflow_state.get("csv_data", {}) or {}).values():
        for column in getattr(frame, "columns", []):
            phrase = _human_name(str(column))
            if phrase:
                phrases.add(phrase)
        break
    return phrases


def _sample_size(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        values = [node.get("samples") or node.get("n") for node in data.get("nodes", []) if isinstance(node, dict)]
        numeric = [int(value) for value in values if str(value).isdigit()]
        return max(numeric) if numeric else None
    return None


def _method_for_artifact(artifact: dict[str, Any]) -> str:
    chart_type = str(artifact.get("chart_type", "")).lower()
    return {
        "decision_tree": "shallow decision-tree model",
        "heatmap": "pairwise correlation",
        "correlation": "pairwise correlation",
        "distribution": "descriptive distribution comparison",
        "box": "descriptive distribution comparison",
        "horizontal_bar": "grouped descriptive rate",
        "bar": "grouped descriptive comparison",
    }.get(chart_type, "descriptive analysis")


def _association_audit(
    artifact: dict[str, Any],
    workflow_state: dict[str, Any],
    target: str,
) -> dict[str, Any] | None:
    if str(artifact.get("chart_type", "")).lower() not in {"bar", "column", "horizontal_bar", "ranking", "grouped_bar"}:
        return None
    rows = artifact.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict) or not target:
        return None
    frame = next(iter((workflow_state.get("csv_data", {}) or {}).values()), None)
    if frame is None or target not in getattr(frame, "columns", []):
        return None
    normalized_columns = {_human_name(column).lower(): str(column) for column in frame.columns}
    category_key = next(
        (
            str(key)
            for key, value in rows[0].items()
            if not isinstance(value, (int, float)) and _human_name(key).lower() in normalized_columns
        ),
        "",
    )
    category_column = normalized_columns.get(_human_name(category_key).lower(), "")
    if not category_column or category_column == target:
        return None
    try:
        from scipy.stats import chi2_contingency

        table = frame.groupby([category_column, target], observed=False).size().unstack(fill_value=0)
        if table.shape[0] < 2 or table.shape[1] < 2:
            return None
        _, p_value, _, _ = chi2_contingency(table)
        target_counts = frame[target].value_counts(dropna=False).to_dict()
        denominator = ", ".join(f"{key}={int(value)}" for key, value in target_counts.items())
        return {
            "method": "chi-square association test",
            "p_value": round(float(p_value), 6),
            "sample_size": int(len(frame)),
            "denominator": denominator,
        }
    except Exception:
        return None


def _confidence_for_artifact(artifact: dict[str, Any]) -> str:
    if str(artifact.get("chart_type", "")).lower() == "decision_tree":
        return "exploratory"
    return "descriptive"


def _artifact_caveat(artifact: dict[str, Any]) -> str:
    if str(artifact.get("chart_type", "")).lower() == "decision_tree":
        return "Model branches are exploratory and require class-sensitive validation before operational use."
    return "Observed association does not establish causality."


def _required_caveats(bundle: dict[str, Any]) -> list[str]:
    caveats = ["Observed relationships are associative unless a credible causal design is documented."]
    if bundle.get("high_stakes"):
        caveats.append("Use domain-expert and human review; do not automate high-impact decisions from this output.")
    if any(item.get("evidence_level") == "search_snippet" for item in bundle.get("external_sources", [])):
        caveats.append("Search-snippet sources provide context only and should be replaced with reviewed primary sources.")
    return caveats


def _default_validation_metric(bundle: dict[str, Any]) -> str:
    if bundle.get("high_stakes"):
        return "precision, recall, false-positive and false-negative rates, subgroup fairness, safety, and expert-reviewed outcomes"
    return "outcome lift against baseline, adoption, implementation quality, and adverse effects"


def _high_stakes_final_recommendation(decision: dict[str, Any], tree_features: list[str]) -> str:
    features = ", ".join(_human_name(item) for item in tree_features[:3])
    profile = f" ({features})" if features else ""
    return (
        f"Use the observed evidence and exploratory model profile{profile} to design a professionally supervised validation study. "
        "Do not use this analysis for diagnosis, eligibility, or automated high-impact decisions; require human review, safety and fairness checks, and fresh validation data."
    )


def _is_high_stakes(workflow_state: dict[str, Any]) -> bool:
    text = " ".join(
        str(value)
        for value in (
            workflow_state.get("user_data_description", ""),
            workflow_state.get("decision_tree_target_column", ""),
            workflow_state.get("workflow_objective", {}),
        )
    ).lower()
    return any(term in text for term in HIGH_STAKES_TERMS)


def _score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("%"):
            return float(text[:-1]) / 100
        number = float(text)
        return number / 100 if number > 1 else number
    except (TypeError, ValueError):
        return None


def _tokens(value: Any) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value).lower().replace("_", " ")) if len(token) >= 3}


def _phrase_in_text(phrase: str, text: str) -> bool:
    pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(part) for part in phrase.lower().split()) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def _payload_contains_cjk(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_payload_contains_cjk(item) for item in value.values())
    if isinstance(value, list):
        return any(_payload_contains_cjk(item) for item in value)
    if not isinstance(value, str):
        return False
    return bool(re.search(r"[\u3400-\u9fff]", value))


def _human_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip()


def _clean_text(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
