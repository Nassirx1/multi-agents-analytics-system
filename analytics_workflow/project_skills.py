from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROJECT_SKILL_DIR = Path(__file__).resolve().parents[1] / "skills"

PROJECT_SKILL_FILES = {
    "data_profiling": "data_profiling.SKILL.md",
    "market_research": "market_research.SKILL.md",
    "analysis_planning": "analysis_planning.SKILL.md",
    "code_generation": "code_generation.SKILL.md",
    "code_review": "code_review.SKILL.md",
    "business_translation": "business_translation.SKILL.md",
    "recommendation_generation": "recommendation_generation.SKILL.md",
    "report_generation": "report_generation.SKILL.md",
    "slide_generation": "slide_generation.SKILL.md",
    "consulting_pptx": "build-consulting-pptx/SKILL.md",
    "self_evolution": "self_evolution.SKILL.md",
}

PROJECT_SKILL_REFERENCES = {
    "consulting_pptx": (
        "build-consulting-pptx/references/story-and-layout.md",
        "build-consulting-pptx/references/qa-and-repair.md",
        "build-consulting-pptx/references/mcp-tool-playbook.md",
    ),
}


def project_skill_path(skill_name: str) -> Path:
    try:
        filename = PROJECT_SKILL_FILES[skill_name]
    except KeyError as exc:
        raise KeyError(f"Unknown project skill: {skill_name}") from exc
    return PROJECT_SKILL_DIR / filename


@lru_cache(maxsize=None)
def load_project_skill(skill_name: str) -> str:
    path = project_skill_path(skill_name)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@lru_cache(maxsize=None)
def load_project_skill_bundle(skill_name: str) -> str:
    parts = [load_project_skill(skill_name)]
    for relative_path in PROJECT_SKILL_REFERENCES.get(skill_name, ()):
        path = PROJECT_SKILL_DIR / relative_path
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            content = ""
        if content:
            parts.append(content)
    return "\n\n".join(part for part in parts if part)


def required_project_skill_paths() -> list[Path]:
    return [project_skill_path(name) for name in PROJECT_SKILL_FILES]
