from __future__ import annotations

import textwrap
import re
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .tree_diagram import build_tree_layout, clean_tree_text


def build_sklearn_tree_artifact(
    model: Any = None,
    feature_names: Any = None,
    *,
    target: str = "",
    model_type: str = "classification",
    train_score: Any = None,
    test_score: Any = None,
    baseline_score: Any = None,
    balanced_accuracy: Any = None,
    precision: Any = None,
    recall: Any = None,
    f1: Any = None,
    confusion_matrix: Any = None,
    positive_test_support: Any = None,
    cross_validation: Any = None,
    train_mae: Any = None,
    test_mae: Any = None,
    class_names: list[str] | None = None,
    title: str = "",
    finding: str = "",
    max_nodes: int = 9,
    **aliases: Any,
) -> dict[str, Any]:
    """Build a verified chart artifact directly from a fitted sklearn decision tree."""
    if model is None and "fitted_model_or_pipeline" in aliases:
        model = aliases.pop("fitted_model_or_pipeline")
    if feature_names is None and "feature_names" in aliases:
        feature_names = aliases.pop("feature_names")
    balanced_accuracy = balanced_accuracy if balanced_accuracy is not None else aliases.pop("balanced_accuracy_score", None)
    precision = precision if precision is not None else aliases.pop("test_precision", None)
    recall = recall if recall is not None else aliases.pop("test_recall", None)
    f1 = f1 if f1 is not None else aliases.pop("f1_score", aliases.pop("test_f1", None))
    if model is None:
        raise ValueError("model must be a fitted sklearn decision tree instance or Pipeline")
    original_model = model
    model = _extract_tree_estimator(model)
    tree = getattr(model, "tree_", None)
    if not _is_sklearn_tree_object(tree):
        raise ValueError(
            "model must be a fitted sklearn decision tree instance or Pipeline; "
            "do not pass DecisionTreeClassifier/DecisionTreeRegressor classes or sklearn tree_ properties"
        )

    resolved_feature_names = _resolve_feature_names(original_model, model, feature_names)
    safe_features = [clean_tree_text(name) or f"feature_{index}" for index, name in enumerate(resolved_feature_names)]
    children_left = list(getattr(tree, "children_left"))
    children_right = list(getattr(tree, "children_right"))
    features = list(getattr(tree, "feature"))
    thresholds = list(getattr(tree, "threshold"))
    values = getattr(tree, "value")
    sample_counts = list(getattr(tree, "n_node_samples", []))
    classes = class_names or [clean_tree_text(item) for item in getattr(model, "classes_", [])]
    normalized_model_type = clean_tree_text(model_type).lower() or "classification"
    scaler_params = _pipeline_scaler_parameters(original_model)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    rules: list[str] = []

    def walk(node_index: int, depth: int, rule_parts: list[str]) -> None:
        if len(nodes) >= max_nodes:
            return
        node_id = f"node_{node_index}"
        left = int(children_left[node_index])
        right = int(children_right[node_index])
        is_leaf = left == right or left < 0 or right < 0
        sample_text = f"n={sample_counts[node_index]}" if node_index < len(sample_counts) else ""
        if is_leaf:
            prediction = _prediction_label(values[node_index], normalized_model_type, classes)
            rule_text = " and ".join(rule_parts) if rule_parts else "all rows"
            label = f"Leaf: predict {prediction}\nRule: {rule_text}"
            if sample_text:
                label += f"\n{sample_text}"
            nodes.append(
                {
                    "id": node_id,
                    "label": label,
                    "depth": depth,
                    "type": "leaf",
                    "prediction": prediction,
                    "rule": rule_text,
                    "samples": sample_counts[node_index] if node_index < len(sample_counts) else "",
                }
            )
            rules.append(f"If {rule_text}, then predict {prediction}.")
            return

        feature_index = int(features[node_index])
        feature_name = safe_features[feature_index] if 0 <= feature_index < len(safe_features) else f"feature_{feature_index}"
        model_threshold = _format_number(thresholds[node_index])
        threshold = model_threshold
        threshold_unit = "model"
        if feature_name in scaler_params:
            mean, scale = scaler_params[feature_name]
            threshold = _format_number(float(thresholds[node_index]) * scale + mean)
            threshold_unit = "original"
        raw_split_label = f"{feature_name} <= {threshold}"
        raw_model_label = f"{feature_name} <= {model_threshold} (model-scaled)"
        split_label = humanize_decision_tree_condition(feature_name, threshold, operator="<=", split_label=True)
        if sample_text:
            split_label += f"\n{sample_text}"
        nodes.append(
            {
                "id": node_id,
                "label": split_label,
                "raw_label": raw_split_label,
                "display_label": split_label,
                "depth": depth,
                "type": "split",
                "feature": feature_name,
                "threshold": threshold,
                "threshold_unit": threshold_unit,
                "model_threshold": model_threshold,
                "rule": split_label,
                "raw_rule": raw_split_label,
                "raw_model_rule": raw_model_label,
            }
        )
        left_condition = humanize_decision_tree_condition(feature_name, threshold, operator="<=")
        right_condition = humanize_decision_tree_condition(feature_name, threshold, operator=">")
        raw_left_condition = f"{feature_name} <= {threshold}"
        raw_right_condition = f"{feature_name} > {threshold}"
        if len(nodes) < max_nodes:
            edges.append(
                {
                    "source": node_id,
                    "target": f"node_{left}",
                    "label": "True",
                    "condition": left_condition,
                    "raw_condition": raw_left_condition,
                    "model_condition": f"{feature_name} <= {model_threshold}",
                }
            )
            walk(left, depth + 1, rule_parts + [left_condition])
        if len(nodes) < max_nodes:
            edges.append(
                {
                    "source": node_id,
                    "target": f"node_{right}",
                    "label": "False",
                    "condition": right_condition,
                    "raw_condition": raw_right_condition,
                    "model_condition": f"{feature_name} > {model_threshold}",
                }
            )
            walk(right, depth + 1, rule_parts + [right_condition])

    walk(0, 0, [])
    data: dict[str, Any] = {
        "target": target,
        "model_type": normalized_model_type,
        "nodes": nodes,
        "edges": [edge for edge in edges if any(node.get("id") == edge.get("target") for node in nodes)],
        "rules": rules,
        "rules_source": "sklearn_tree_",
        "rules_match_model": True,
        "model_verified": True,
    }
    if normalized_model_type.startswith("class"):
        data.update(
            {
                "train_accuracy": _format_percent(train_score),
                "test_accuracy": _format_percent(test_score),
                "baseline_accuracy": _format_percent(baseline_score),
                "balanced_accuracy": _format_percent(balanced_accuracy),
                "precision": _format_percent(precision),
                "recall": _format_percent(recall),
                "f1": _format_percent(f1),
                "confusion_matrix": confusion_matrix,
                "positive_test_support": positive_test_support,
                "cross_validation": cross_validation,
            }
        )
    else:
        data.update(
            {
                "train_r2": _format_number(train_score),
                "test_r2": _format_number(test_score),
                "train_mae": _format_number(train_mae),
                "test_mae": _format_number(test_mae),
            }
        )
    performance_note = decision_tree_performance_note(data)
    if performance_note:
        data["performance_note"] = performance_note

    return {
        "artifact_id": "decision_tree_rules",
        "artifact_type": "chart_spec",
        "slide_candidate": True,
        "chart_type": "decision_tree",
        "title": title or f"Decision tree rules for {target or 'target'}",
        "finding": finding or performance_note or "The displayed rules were extracted from the fitted decision tree model.",
        "takeaway": finding or performance_note or "Train/test metrics and leaf rules come from the fitted tree.",
        "fallback_path": "decision_tree_rules.png",
        "data": data,
        "recommended_template": "full_width_chart_takeaway",
    }


def decision_tree_underperforms_baseline(data: dict[str, Any]) -> bool:
    test_score = decision_tree_numeric_score(data, ("test_accuracy", "test_score"))
    baseline_score = decision_tree_numeric_score(data, ("baseline_accuracy", "baseline_score", "majority_baseline"))
    return test_score is not None and baseline_score is not None and test_score < baseline_score


def decision_tree_performance_note(data: dict[str, Any]) -> str:
    model_type = clean_tree_text(data.get("model_type")).lower()
    if model_type.startswith("reg"):
        return ""
    imbalance_note = decision_tree_imbalance_note(data)
    if imbalance_note:
        return imbalance_note
    test_score = decision_tree_numeric_score(data, ("test_accuracy", "test_score"))
    baseline_score = decision_tree_numeric_score(data, ("baseline_accuracy", "baseline_score", "majority_baseline"))
    if test_score is None or baseline_score is None:
        return ""
    test_text = _format_percent(test_score)
    baseline_text = _format_percent(baseline_score)
    if test_score < baseline_score:
        return (
            f"Explanatory model only: test accuracy {test_text} trails the baseline {baseline_text}; "
            "use the rules to guide investigation, not production prediction."
        )
    return (
        f"Decision tree test accuracy {test_text} is compared with the baseline {baseline_text}; "
        "use the rules as interpretable decision evidence."
    )


def decision_tree_imbalance_note(data: dict[str, Any]) -> str:
    positive_rate = decision_tree_numeric_score(
        data,
        ("positive_class_rate", "positive_rate", "minority_class_rate", "event_rate", "target_rate"),
    )
    test_score = decision_tree_numeric_score(data, ("test_accuracy", "test_score", "accuracy"))
    baseline_score = decision_tree_numeric_score(data, ("baseline_accuracy", "baseline_score", "majority_baseline"))
    precision = decision_tree_numeric_score(data, ("precision", "test_precision", "positive_precision"))
    recall = decision_tree_numeric_score(data, ("recall", "test_recall", "positive_recall"))
    f1 = decision_tree_numeric_score(data, ("f1", "f1_score", "test_f1", "positive_f1"))
    support = data.get("support") or data.get("positive_support") or data.get("test_positive_support")
    high_baseline = baseline_score is not None and baseline_score >= 0.9
    if positive_rate is None and not high_baseline and not any(value is not None for value in (precision, recall, f1)):
        return ""
    if positive_rate is not None and positive_rate > 0.1 and test_score != 1.0:
        return ""
    rate_text = _format_percent(positive_rate) if positive_rate is not None else "a likely small positive class"
    metric_bits = []
    if precision is not None:
        metric_bits.append(f"precision {_format_percent(precision)}")
    if recall is not None:
        metric_bits.append(f"recall {_format_percent(recall)}")
    if f1 is not None:
        metric_bits.append(f"F1 {_format_percent(f1)}")
    if support not in (None, ""):
        metric_bits.append(f"support {support}")
    metric_text = ", ".join(metric_bits) if metric_bits else "precision/recall/F1 and support"
    accuracy_text = f" Accuracy {_format_percent(test_score)}" if test_score is not None else ""
    baseline_text = f" versus baseline {_format_percent(baseline_score)}" if baseline_score is not None else ""
    return (
        f"Exploratory screening only: the positive class is {rate_text};{accuracy_text}{baseline_text} "
        f"must be interpreted with {metric_text}, not as diagnostic or deployment-ready performance."
    )


def decision_tree_numeric_score(data: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            text = value.strip()
            try:
                if text.endswith("%"):
                    return float(text[:-1].strip()) / 100.0
                number = float(text)
            except ValueError:
                continue
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
        if 1.0 < number <= 100.0:
            return number / 100.0
        return number
    return None


def _extract_tree_estimator(model: Any) -> Any:
    tree = getattr(model, "tree_", None)
    if _is_sklearn_tree_object(tree):
        return model

    steps = getattr(model, "steps", None)
    if isinstance(steps, list):
        for _, estimator in reversed(steps):
            if _is_sklearn_tree_object(getattr(estimator, "tree_", None)):
                return estimator

    named_steps = getattr(model, "named_steps", None)
    if isinstance(named_steps, dict):
        for estimator in reversed(list(named_steps.values())):
            if _is_sklearn_tree_object(getattr(estimator, "tree_", None)):
                return estimator

    return model


def _is_sklearn_tree_object(tree: Any) -> bool:
    return all(hasattr(tree, name) for name in ("children_left", "children_right", "feature", "threshold", "value"))


def _resolve_feature_names(original_model: Any, tree_model: Any, feature_names: Any) -> list[str]:
    supplied_names = _names_from_candidate(feature_names)
    if supplied_names:
        return supplied_names

    for candidate in (
        getattr(tree_model, "feature_names_in_", None),
    ):
        names = _names_from_candidate(candidate)
        if names:
            return names

    pipeline_names = _feature_names_from_pipeline(original_model, tree_model)
    if pipeline_names:
        return pipeline_names

    original_names = _names_from_candidate(getattr(original_model, "feature_names_in_", None))
    if original_names:
        return original_names

    feature_count = int(getattr(tree_model, "n_features_in_", 0) or getattr(getattr(tree_model, "tree_", None), "n_features", 0) or 0)
    return [f"feature_{index}" for index in range(feature_count)]


def _feature_names_from_pipeline(pipeline: Any, tree_model: Any) -> list[str]:
    steps = getattr(pipeline, "steps", None)
    if not isinstance(steps, list):
        return []
    names: list[str] = []
    for _, estimator in steps:
        if estimator is tree_model:
            break
        if hasattr(estimator, "get_feature_names_out"):
            try:
                names = _names_from_candidate(estimator.get_feature_names_out())
            except Exception:
                names = []
        elif hasattr(estimator, "feature_names_in_"):
            names = _names_from_candidate(getattr(estimator, "feature_names_in_"))
    return names


def _pipeline_scaler_parameters(pipeline: Any) -> dict[str, tuple[float, float]]:
    """Map transformed numeric feature names to inverse StandardScaler parameters."""
    steps = getattr(pipeline, "steps", None)
    if not isinstance(steps, list):
        return {}
    parameters: dict[str, tuple[float, float]] = {}
    for _, estimator in steps:
        transformers = getattr(estimator, "transformers_", None)
        if not isinstance(transformers, list):
            continue
        for _, transformer, columns in transformers:
            if isinstance(transformer, str) and transformer in {"drop", "passthrough"}:
                continue
            scaler = transformer
            nested_steps = getattr(transformer, "steps", None)
            if isinstance(nested_steps, list):
                scaler = next(
                    (candidate for _, candidate in nested_steps if hasattr(candidate, "mean_") and hasattr(candidate, "scale_")),
                    transformer,
                )
            means = getattr(scaler, "mean_", None)
            scales = getattr(scaler, "scale_", None)
            if means is None or scales is None:
                continue
            try:
                names = [clean_tree_text(item) for item in list(columns)]
                for name, mean, scale in zip(names, list(means), list(scales)):
                    if name:
                        parameters[name] = (float(mean), float(scale) or 1.0)
            except (TypeError, ValueError):
                continue
    return parameters


def _names_from_candidate(candidate: Any) -> list[str]:
    if candidate is None:
        return []
    try:
        values = list(candidate)
    except TypeError:
        return []
    names: list[str] = []
    for index, value in enumerate(values):
        text = clean_tree_text(value)
        if "__" in text:
            text = text.split("__", 1)[1]
        names.append(text or f"feature_{index}")
    return names


def render_decision_tree_rules_figure(
    artifact: dict[str, Any],
    output_path: str = "decision_tree_rules.png",
) -> str:
    """Render a compact decision tree rules diagram as a saved PNG."""
    nodes, edges = _tree_nodes_edges(artifact)
    if not nodes:
        return ""

    layout = build_tree_layout(nodes, edges)
    layout_nodes = layout.get("nodes", [])
    layout_edges = layout.get("edges", [])
    normalized_positions = layout.get("positions", {})
    if not layout_nodes or not normalized_positions:
        return ""

    leaf_count = _tree_leaf_count(layout_nodes, layout_edges)
    max_depth = int(layout.get("max_depth", 0) or 0)
    fig_width = max(8.0, min(15.0, 1.55 * leaf_count + 2.0))
    fig_height = max(4.0, min(8.5, 1.25 * (max_depth + 1) + 1.4))
    fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=180)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    node_w = min(max(0.72 / max(leaf_count, 2), 0.13), 0.26)
    node_h = min(max(0.13, 0.34 / max(max_depth + 1, 2)), 0.18)
    top_margin = 0.08
    bottom_margin = 0.1
    positions: dict[str, tuple[float, float, float, float]] = {}
    for node in layout_nodes:
        node_id = clean_tree_text(node.get("_layout_id"))
        if node_id not in normalized_positions:
            continue
        x_norm, y_norm = normalized_positions[node_id]
        x = 0.05 + (0.9 - node_w) * float(x_norm)
        y = 1.0 - top_margin - node_h - (1.0 - top_margin - bottom_margin - node_h) * float(y_norm)
        positions[node_id] = (x, y, node_w, node_h)

    split_node_ids = {edge["source"] for edge in layout_edges}
    line_color = "#7a9bc3"
    blue = "#1f4e79"
    navy = "#16324f"
    leaf_fill = "#f7f9fc"

    for edge in layout_edges:
        source = edge["source"]
        target = edge["target"]
        if source not in positions or target not in positions:
            continue
        sx, sy, sw, _ = positions[source]
        tx, ty, tw, th = positions[target]
        start = (sx + sw / 2, sy)
        end = (tx + tw / 2, ty + th)
        joint_y = start[1] - max((start[1] - end[1]) * 0.42, 0.035)
        ax.plot([start[0], start[0]], [start[1], joint_y], color=line_color, linewidth=1.2)
        ax.plot([start[0], end[0]], [joint_y, joint_y], color=line_color, linewidth=1.2)
        ax.plot([end[0], end[0]], [joint_y, end[1]], color=line_color, linewidth=1.2)
        edge_label = clean_tree_text(edge.get("label"))
        if edge_label:
            ax.text(
                (start[0] + end[0]) / 2,
                joint_y + 0.012,
                textwrap.shorten(edge_label, width=24, placeholder="..."),
                ha="center",
                va="bottom",
                fontsize=7.5,
                color=blue,
                weight="bold",
            )

    for node in layout_nodes:
        node_id = clean_tree_text(node.get("_layout_id"))
        if node_id not in positions:
            continue
        x, y, w, h = positions[node_id]
        is_split = node_id in split_node_ids
        rect = Rectangle(
            (x, y),
            w,
            h,
            facecolor=blue if is_split else leaf_fill,
            edgecolor=blue,
            linewidth=1.15,
        )
        ax.add_patch(rect)
        label = _node_label(node, is_split)
        wrapped = "\n".join(textwrap.wrap(label, width=max(16, int(w * 95)))[:4])
        ax.text(
            x + w / 2,
            y + h / 2,
            wrapped,
            ha="center",
            va="center",
            fontsize=7.1 if leaf_count > 5 else 8.0,
            color="white" if is_split else navy,
            weight="bold" if is_split else "normal",
        )

    title = clean_tree_text(artifact.get("title") or "Decision Tree Rules")
    ax.text(0.0, 1.015, title, transform=ax.transAxes, ha="left", va="bottom", fontsize=13, weight="bold", color=navy)
    metric_text = _metric_text(artifact)
    if metric_text:
        ax.text(0.5, 0.01, metric_text, transform=ax.transAxes, ha="center", va="bottom", fontsize=8, color="#5b6570")

    fig.savefig(output_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return output_path


def _tree_nodes_edges(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
    nodes = [node for node in data.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in data.get("edges", []) if isinstance(edge, dict)]
    if nodes:
        return nodes[:15], edges[:20]

    rules = data.get("rules", []) or artifact.get("rules", [])
    rule_texts = [clean_tree_text(rule) for rule in rules if clean_tree_text(rule)]
    if not rule_texts and isinstance(artifact.get("data"), list):
        rule_texts = [
            clean_tree_text(row.get("rule") or row.get("label"))
            for row in artifact.get("data", [])
            if isinstance(row, dict)
        ]
    if not rule_texts:
        return [], []

    nodes = [{"id": "root", "label": "Decision tree rules", "depth": 0}]
    edges = []
    for index, rule in enumerate(rule_texts[:8], start=1):
        node_id = f"leaf_{index}"
        nodes.append({"id": node_id, "label": f"Leaf rule {index}: {rule}", "depth": 1, "type": "leaf"})
        edges.append({"source": "root", "target": node_id, "label": ""})
    return nodes, edges


def _tree_leaf_count(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> int:
    sources = {edge.get("source") for edge in edges}
    return max(sum(1 for node in nodes if node.get("_layout_id") not in sources), 1)


def _node_label(node: dict[str, Any], is_split: bool) -> str:
    keys = ("display_label", "human_label", "label", "rule", "name", "condition", "prediction", "value", "class")
    for key in keys:
        text = clean_tree_text(node.get(key))
        if text:
            return text
    return "Split rule" if is_split else "Leaf prediction"


def humanize_decision_tree_condition(
    feature: Any,
    threshold: Any,
    *,
    operator: str = "<=",
    split_label: bool = False,
) -> str:
    """Return a stakeholder-readable tree split without hiding the raw metadata.

    Generated workflow code often trains shallow trees through preprocessing
    pipelines. When the threshold is in standardized model units, a raw value
    like ``Age <= 0.279`` is truthful but not useful for business readers. This
    helper keeps the raw threshold on the node while making visible labels
    readable and honest.
    """
    raw_feature = clean_tree_text(feature)
    label = _friendly_feature_label(raw_feature)
    raw_threshold = clean_tree_text(threshold)
    numeric = _to_float(raw_threshold)
    normalized_op = "<=" if operator not in {">", ">=", "<", "<="} else operator
    if not label:
        return f"Split {normalized_op} {raw_threshold}".strip()

    binary_value = _binary_feature_value(raw_feature, numeric)
    if binary_value:
        base = f"{label}: {binary_value}"
        if split_label:
            return f"{base} split"
        if normalized_op in {"<=", "<"}:
            return f"{label} is not {binary_value}"
        return f"{label} is {binary_value}"

    if numeric is not None and -3.5 <= numeric <= 3.5:
        if split_label:
            return f"{label} split (model-scaled)"
        if normalized_op in {"<=", "<"}:
            return f"{label} in the lower model range"
        return f"{label} above the lower model range"

    threshold_text = _friendly_threshold(raw_threshold)
    if split_label:
        return f"{label} {normalized_op} {threshold_text}"
    direction = "at or below" if normalized_op in {"<=", "<"} else "above"
    return f"{label} {direction} {threshold_text}"


def _friendly_feature_label(feature: str) -> str:
    clean = clean_tree_text(feature)
    if "__" in clean:
        clean = clean.rsplit("__", 1)[-1]
    clean = clean.replace("_", " ")
    clean = clean.replace(" Yes", "").replace(" No", "")
    clean = " ".join(re.sub(r"(?<!^)(?=[A-Z])", " ", part).strip() for part in clean.split())
    replacements = {
        "Monthly Income": "Monthly income",
        "Years At Company": "Years at company",
        "Years Since Last Promotion": "Years since last promotion",
        "Stock Option Level None": "Stock option level",
        "Over Time": "Overtime",
    }
    return replacements.get(clean, clean)


def _binary_feature_value(feature: str, threshold: float | None) -> str:
    if threshold is None or abs(threshold - 0.5) > 0.05:
        return ""
    clean = clean_tree_text(feature)
    if "_" not in clean:
        return ""
    value = clean.rsplit("_", 1)[-1]
    if value.lower() in {"yes", "true", "1"}:
        return "Yes"
    if value.lower() in {"no", "false", "0"}:
        return "No"
    if value:
        return value.replace("_", " ")
    return ""


def _friendly_threshold(value: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return clean_tree_text(value)
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    return _format_number(number)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_text(artifact: dict[str, Any]) -> str:
    data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
    parts = []
    for key in (
        "train_accuracy",
        "test_accuracy",
        "train_score",
        "test_score",
        "accuracy",
        "baseline_accuracy",
        "baseline_score",
        "train_r2",
        "test_r2",
        "r2",
        "train_mae",
        "test_mae",
        "mae",
    ):
        value = data.get(key) or artifact.get(key)
        if value not in (None, ""):
            parts.append(f"{_metric_label(key, data)}: {_metric_value(key, value)}")
    return " | ".join(parts[:4])


def _metric_label(key: str, data: dict[str, Any]) -> str:
    model_type = clean_tree_text(data.get("model_type")).lower()
    if key == "train_score" and not model_type.startswith("reg"):
        return "Train Accuracy"
    if key == "test_score" and not model_type.startswith("reg"):
        return "Test Accuracy"
    if key == "baseline_score" and not model_type.startswith("reg"):
        return "Baseline Accuracy"
    return key.replace("_", " ").title()


def _metric_value(key: str, value: Any) -> str:
    if key in {"train_score", "test_score", "baseline_score", "train_accuracy", "test_accuracy", "baseline_accuracy", "accuracy"}:
        return _format_percent(value)
    return clean_tree_text(value)


def _prediction_label(value: Any, model_type: str, classes: list[str]) -> str:
    try:
        flat = list(value[0]) if hasattr(value[0], "__iter__") else list(value)
    except Exception:
        flat = [value]
    if model_type.startswith("class") and flat:
        best_index = max(range(len(flat)), key=lambda index: flat[index])
        if 0 <= best_index < len(classes) and classes[best_index]:
            return classes[best_index]
        return str(best_index)
    try:
        return _format_number(float(flat[0]))
    except Exception:
        return clean_tree_text(flat[0] if flat else "")


def _format_percent(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return clean_tree_text(value)
    if 0 <= number <= 1:
        number *= 100
    return f"{number:.1f}%"


def _format_number(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return clean_tree_text(value)
    if abs(number) >= 100:
        return f"{number:.0f}"
    if abs(number) >= 10:
        return f"{number:.1f}"
    return f"{number:.3g}"
