import unittest

from analytics_workflow.reporting import _format_analysis_findings


class ReportingFormattingTests(unittest.TestCase):
    def test_analysis_findings_include_summary_and_visual_captions(self) -> None:
        analysis_results = {
            "analysis_summary": {
                "average_return_pct": 2.8,
                "peak_volume": "2.4M shares",
            },
            "figure_captions": {
                "figure_1_20260428_120000.png": "Price trend shows a steady upward move over the latest period.",
                "figure_2_20260428_120000.png": "Volume spikes line up with the strongest positive price changes.",
            },
        }

        findings = _format_analysis_findings(analysis_results)

        self.assertTrue(any("Average Return Pct: 2.8" in item for item in findings))
        self.assertTrue(any("Peak Volume: 2.4M shares" in item for item in findings))
        self.assertTrue(any("Figure 1 20260428 120000" in item for item in findings))
        self.assertTrue(any("Volume spikes line up" in item for item in findings))

    def test_analysis_findings_have_fallback_when_empty(self) -> None:
        findings = _format_analysis_findings({})

        self.assertEqual(len(findings), 1)
        self.assertIn("no structured analysis findings were captured", findings[0].lower())


if __name__ == "__main__":
    unittest.main()
