import base64
import os
import tempfile
import unittest
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from analytics_workflow.pipeline_runtime import generate_slide_deck


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Zl8QAAAAASUVORK5CYII="
)


class SlideDeckTests(unittest.TestCase):
    def test_saved_figures_become_visual_slides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            figure_paths = []
            for index in range(1, 5):
                figure_path = temp_path / f"figure_{index}.png"
                figure_path.write_bytes(PNG_BYTES)
                figure_paths.append(str(figure_path))

            output_path = temp_path / "analytics_report.pptx"
            workflow_state = {
                "saved_figures": figure_paths,
                "analysis_results": {
                    "analysis_summary": {
                        "average_return_pct": 3.1,
                        "peak_volume": "2.7M shares",
                    },
                    "figure_captions": {
                        figure_paths[0]: "Sales trend improved in the latest period.",
                        figure_paths[1]: "Volume spikes align with price volatility.",
                        figure_paths[2]: "Returns cluster around a narrow band.",
                        figure_paths[3]: "Drawdown periods are limited in duration.",
                    }
                },
                "agent_outputs": {
                    "presentation_architect": {
                        "presentation_title": "STC Analytics Review",
                        "presentation_subtitle": "Executive summary",
                        "slides": [
                            {
                                "slide_number": 1,
                                "title": "Context",
                                "main_message": "The analysis highlights performance and risk patterns.",
                                "details": ["Dataset loaded and reviewed.", "Market and business implications summarized."],
                                "visual_element": "",
                            },
                            {
                                "slide_number": 2,
                                "title": "Recommendation",
                                "main_message": "Focus on evidence-backed actions.",
                                "details": ["Preserve the strongest signal from the analysis."],
                                "visual_element": "",
                            },
                        ],
                    },
                    "decision_maker": {},
                    "business_translator": {},
                },
            }

            generate_slide_deck(workflow_state, str(output_path))

            presentation = Presentation(str(output_path))
            self.assertEqual(len(presentation.slides), 8)

            picture_slides = 0
            all_text = []
            for slide in presentation.slides:
                if any(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes):
                    picture_slides += 1
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        all_text.append(shape.text)

            full_text = "\n".join(all_text)
            self.assertEqual(picture_slides, 4)
            self.assertNotIn("Insight Visual / Evidence", full_text)
            self.assertIn("Technical Analysis Findings", full_text)
            self.assertIn("Average Return Pct: 3.1", full_text)
            self.assertIn("Peak Volume: 2.7M shares", full_text)
            self.assertEqual(full_text.count("Sales trend improved in the latest period."), 1)
            self.assertEqual(full_text.count("Volume spikes align with price volatility."), 1)


if __name__ == "__main__":
    unittest.main()
