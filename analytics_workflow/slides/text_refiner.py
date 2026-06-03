from __future__ import annotations

import json
import re
import textwrap
from typing import Any


GENERIC_HEADLINES = {
    "analysis",
    "analysis results",
    "results",
    "chart",
    "data",
    "context",
    "findings",
    "summary",
    "eda results",
    "model results",
}

OUTPUT_TEXT_REPLACEMENTS = {
    "\u00a0": " ",
    "\u00b0": " deg ",
    "\u00b1": "+/-",
    "\u00b2": "2",
    "\u00b3": "3",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2022": "-",
    "\u2192": "->",
    "\u2264": "<=",
    "\u2265": ">=",
}

UNSUPPORTED_IMPACT_RE = re.compile(
    r"(\b\d+(?:\.\d+)?\s*(?:-|to)\s*\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*(?:-|to)\s*\d+(?:\.\d+)?\s*x\b|\b\d+(?:\.\d+)?\s*x\b).{0,80}\b(roi|return on investment|attrition reduction|reduce attrition|reduction)\b"
    r"|\b(roi|return on investment|attrition reduction|reduce attrition|reduction)\b.{0,80}(\b\d+(?:\.\d+)?\s*(?:-|to)\s*\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*(?:-|to)\s*\d+(?:\.\d+)?\s*x\b|\b\d+(?:\.\d+)?\s*x\b)",
    re.IGNORECASE,
)
UNSUPPORTED_ATTRITION_ACTION_RE = re.compile(
    r"\b(can|will|should)\s+(reduce|lower|cut)\s+attrition\b",
    re.IGNORECASE,
)
UNSUPPORTED_CREDIT_LOSS_ACTION_RE = re.compile(
    r"\b(can|will|should)\s+(reduce|lower|cut)\s+(credit\s+)?loss(?:es)?\b",
    re.IGNORECASE,
)
EXPLICIT_CALCULATION_RE = re.compile(
    r"\b(calculated|computed|measured|observed|modeled|modelled|simulated|estimated from|based on measured|pilot result)\b",
    re.IGNORECASE,
)

MOJIBAKE_TEXT_REPLACEMENTS = {
    "\u00c2\u00b0": " deg ",
    "\u00c3\u2014": "x",
    "\u00e2\u20ac\u201c": "-",
    "\u00e2\u20ac\u201d": "-",
    "\u00e2\u20ac\u2122": "'",
    "\u00e2\u20ac\u0153": '"',
    "\u00e2\u20ac\u009d": '"',
    "\u00e2\u2020\u2019": "->",
}


def normalize_output_text(text: Any) -> str:
    clean = stringify(text)
    if not clean:
        return ""
    for source, replacement in MOJIBAKE_TEXT_REPLACEMENTS.items():
        clean = clean.replace(source, replacement)
    for source, replacement in OUTPUT_TEXT_REPLACEMENTS.items():
        clean = clean.replace(source, replacement)
    return clean


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, default=str)


def compact_whitespace(text: Any) -> str:
    return re.sub(r"\s+", " ", normalize_output_text(text)).strip()


def soften_unsupported_impact_claim(text: Any) -> str:
    clean = compact_whitespace(text)
    if not clean:
        return ""
    if UNSUPPORTED_IMPACT_RE.search(clean) and not EXPLICIT_CALCULATION_RE.search(clean):
        return "Validate projected attrition and ROI impact through a pilot before setting numeric targets."
    if UNSUPPORTED_ATTRITION_ACTION_RE.search(clean) and not EXPLICIT_CALCULATION_RE.search(clean):
        return UNSUPPORTED_ATTRITION_ACTION_RE.sub("can focus attrition validation pilots", clean)
    if UNSUPPORTED_CREDIT_LOSS_ACTION_RE.search(clean) and not EXPLICIT_CALCULATION_RE.search(clean):
        return UNSUPPORTED_CREDIT_LOSS_ACTION_RE.sub("can focus credit-loss validation pilots", clean)
    return clean


def shorten(text: Any, width: int, placeholder: str = "...") -> str:
    clean = compact_whitespace(text)
    if not clean:
        return ""
    if len(clean) <= width:
        return clean

    for sentence_match in re.finditer(r"(?<=[.!?])\s+", clean):
        candidate = clean[: sentence_match.start()].strip()
        if width * 0.45 <= len(candidate) <= width:
            return candidate

    boundary_window = clean[: max(width - len(placeholder), 1)]
    for pattern in (r";\s+", r":\s+", r"\s+-\s+", r",\s+"):
        matches = list(re.finditer(pattern, boundary_window))
        if matches:
            candidate = boundary_window[: matches[-1].start()].strip()
            if len(candidate) >= width * 0.45:
                return f"{candidate}{placeholder}"

    return textwrap.shorten(clean, width=width, placeholder=placeholder)


def sentence_case(text: str) -> str:
    clean = compact_whitespace(text)
    if not clean:
        return ""
    return clean[0].upper() + clean[1:]


def refine_headline(headline: Any, fallback: str, width: int = 92) -> str:
    clean = compact_whitespace(headline)
    lowered = clean.lower().rstrip(":")
    if not clean or lowered in GENERIC_HEADLINES or re.fullmatch(r"finding\s+\d+", lowered):
        clean = fallback
    return sentence_case(shorten(clean.rstrip("."), width))


def refine_bullets(items: Any, max_items: int = 4, max_chars: int = 118) -> list[str]:
    if items is None:
        raw_items: list[Any] = []
    elif isinstance(items, (list, tuple)):
        raw_items = list(items)
    elif isinstance(items, dict):
        raw_items = [items]
    else:
        raw_items = [items]

    bullets: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, dict):
            pieces = []
            for key in (
                "finding",
                "limitation",
                "action",
                "rationale",
                "evidence",
                "impact",
                "priority",
                "mitigation",
                "decision_impact",
                "next_step",
            ):
                value = compact_whitespace(item.get(key))
                if value:
                    pieces.append(value)
            text = ". ".join(pieces) if pieces else compact_whitespace(item)
        else:
            text = compact_whitespace(item)
        text = soften_unsupported_impact_claim(text)
        text = shorten(text.rstrip("."), max_chars)
        if text and text not in seen:
            seen.add(text)
            bullets.append(text)
        if len(bullets) >= max_items:
            break
    return bullets


def first_nonempty(*values: Any) -> str:
    for value in values:
        clean = compact_whitespace(value)
        if clean:
            return clean
    return ""
