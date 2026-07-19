from __future__ import annotations

import unittest

from analytics_workflow.agents import _load_repo_skill_text
from analytics_workflow.project_skills import load_project_skill, load_project_skill_bundle


class ProjectSkillTests(unittest.TestCase):
    def test_original_consulting_skill_and_references_load_as_bundle(self) -> None:
        skill = load_project_skill("consulting_pptx")
        bundle = load_project_skill_bundle("consulting_pptx")
        self.assertIn("name: build-consulting-pptx", skill)
        self.assertIn("# Story and layout reference", bundle)
        self.assertIn("# QA and repair reference", bundle)
        self.assertIn("# PowerPoint MCP tool playbook", bundle)

    def test_agent9_slide_skill_alias_uses_original_consulting_skill(self) -> None:
        loaded = _load_repo_skill_text("generate-slide-deck")
        self.assertIn("name: build-consulting-pptx", loaded)
        self.assertIn("PowerPoint MCP tool playbook", loaded)


if __name__ == "__main__":
    unittest.main()
