import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analytics_workflow.reporting import (
    _add_pdf_decision_tree_diagram,
    _decision_tree_image_path,
    _decision_tree_metric_keys,
    _eda_model_transition_text,
    _figure_caption_for,
    _format_analysis_findings,
    _format_data_quality_notes,
    _format_dataset_overview,
    _format_executive_decision_block,
    _format_limitations,
    _format_top_risk_signals,
    _format_workflow_trace,
    _market_claim_pairs,
    _recommendation_metric,
    _recommendation_target,
    _report_figures,
    _pdf_decision_tree_node_label,
    _source_index_map,
    _trim_trailing_spacers_and_pagebreaks,
    generate_pdf_report,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Zl8QAAAAASUVORK5CYII="
)


class ReportingFormattingTests(unittest.TestCase):
    def test_analysis_findings_include_summary_and_visual_captions(self) -> None:
        analysis_results = {
            "analysis_summary": {
                "average_return_pct": 2.8,
                "peak_volume": "2.4M shares",
                "temperature_change": "+0.081\u00b0C per year",
            },
            "figure_captions": {
                "figure_1_20260428_120000.png": "Price trend shows a steady upward move over the latest period.",
                "figure_2_20260428_120000.png": "Volume spikes line up with the strongest positive price changes.",
            },
        }

        findings = _format_analysis_findings(analysis_results)

        self.assertTrue(any("Average Return Pct: 2.8" in item for item in findings))
        self.assertTrue(any("Peak Volume: 2.4M shares" in item for item in findings))
        self.assertTrue(any("+0.081 deg C per year" in item for item in findings))
        self.assertTrue(any("Visual evidence" in item for item in findings))
        self.assertFalse(any("Figure 1 20260428 120000" in item for item in findings))
        self.assertTrue(any("Volume spikes line up" in item for item in findings))

    def test_analysis_findings_keep_tree_narrative_out_of_eda_section(self) -> None:
        findings = _format_analysis_findings(
            {
                "business_findings": [
                    "EDA driver: overtime attrition is higher.",
                    "Decision tree rule predicts Attrition.",
                ],
                "analysis_artifacts": [
                    {
                        "chart_type": "decision_tree",
                        "title": "Decision tree rules",
                        "finding": "Tree split uses Age.",
                        "data": {"nodes": [{"id": "root"}], "edges": []},
                    },
                    {
                        "chart_type": "bar",
                        "title": "Overtime attrition",
                        "finding": "Overtime attrition is 30.5%.",
                    },
                ],
            }
        )

        joined = " ".join(findings).lower()
        self.assertIn("overtime attrition", joined)
        self.assertNotIn("decision tree", joined)
        self.assertNotIn("tree split", joined)

    def test_pdf_tree_labels_prefer_human_display_label(self) -> None:
        self.assertEqual(
            _pdf_decision_tree_node_label(
                {
                    "type": "split",
                    "feature": "YearsAtCompany",
                    "threshold": "-0.753",
                    "display_label": "Years at company split (model-scaled)",
                },
                True,
            ),
            "Years at company split (model-scaled)",
        )

    def test_eda_model_transition_explains_tree_separation(self) -> None:
        transition = _eda_model_transition_text()

        self.assertIn("EDA figures", transition)
        self.assertIn("separate explanatory model check", transition)
        self.assertIn("not as a production screening tool", transition)

    def test_executive_decision_block_and_top_risk_signals_are_structured(self) -> None:
        state = {
            "decision_tree_target_column": "loan_status",
            "analysis_results": {
                "analysis_artifacts": [
                    {
                        "artifact_id": "default_by_grade",
                        "chart_type": "bar",
                        "title": "Default Rate by Loan Grade",
                        "finding": "Default rate increases for grade E.",
                    }
                ]
            },
            "agent_outputs": {
                "decision_maker": {
                    "final_recommendation": "Pilot risk-tiered pricing for high-risk grades.",
                    "recommendations": [{"action": "Pilot risk-tiered pricing"}],
                    "limitations": ["Observational data requires validation."],
                }
            },
        }

        block = _format_executive_decision_block(state)
        signals = _format_top_risk_signals(state["analysis_results"])

        self.assertTrue(block[0].startswith("Decision:"))
        self.assertTrue(any(item.startswith("Pilot metric:") for item in block))
        self.assertIn("Signal: Default Rate by Loan Grade", signals[0])
        self.assertIn("Decision implication:", signals[0])

    def test_stock_report_figures_deprioritize_dense_scatter(self) -> None:
        state = {
            "user_data_description": "STC stock price history review.",
            "saved_figures": ["price.png", "scatter.png", "volatility.png", "seasonal.png", "volume.png"],
            "analysis_results": {
                "figure_captions": {
                    "price.png": "Price trend shows support and resistance levels.",
                    "scatter.png": "Volume-return scatter shows correlation strength: 0.17.",
                    "volatility.png": "Volatility trend shows high-risk periods.",
                    "seasonal.png": "Seasonal return patterns show monthly differences.",
                    "volume.png": "Volume spike trend highlights liquidity windows.",
                }
            },
            "agent_outputs": {
                "data_understander": {
                    "datasets": {
                        "STC Stock Price History (5).csv": {
                            "columns": ["Date", "Price", "Open", "High", "Low", "Vol.", "Change %"]
                        }
                    }
                }
            },
        }

        figures = _report_figures(state, limit=4)

        self.assertEqual(figures[:3], ["price.png", "volatility.png", "seasonal.png"])
        self.assertNotIn("scatter.png", figures[:3])

    def test_report_figures_exclude_tree_png_when_tree_artifact_has_dedicated_section(self) -> None:
        state = {
            "saved_figures": ["figure_1.png", "decision_tree_rules_20260601.png", "figure_2.png"],
            "analysis_results": {
                "analysis_artifacts": [
                    {
                        "artifact_id": "decision_tree_rules",
                        "chart_type": "decision_tree",
                        "data": {
                            "nodes": [{"id": "0", "type": "split"}, {"id": "1", "type": "leaf"}, {"id": "2", "type": "leaf"}],
                            "edges": [{"source": "0", "target": "1"}, {"source": "0", "target": "2"}],
                        },
                    }
                ],
                "figure_captions": {
                    "decision_tree_rules_20260601.png": "Decision tree visualization.",
                    "figure_1.png": "EDA visual one.",
                    "figure_2.png": "EDA visual two.",
                },
            },
        }

        figures = _report_figures(state)

        self.assertEqual(figures, ["figure_1.png", "figure_2.png"])
        self.assertFalse(any("decision_tree" in figure for figure in figures))

    def test_stock_recommendation_defaults_are_time_series_specific(self) -> None:
        state = {
            "user_data_description": "STC stock price history review.",
            "analysis_results": {
                "analysis_artifacts": [
                    {
                        "chart_type": "line",
                        "title": "Price trend with volatility gates",
                        "finding": "Price and volatility define the review window.",
                    }
                ]
            },
            "agent_outputs": {
                "decision_maker": {
                    "recommendations": [{"action": "Validate drawdown trigger before hedging"}],
                    "limitations": ["Historical prices need fresh-market validation."],
                },
                "data_understander": {
                    "datasets": {
                        "STC Stock Price History (5).csv": {
                            "columns": ["Date", "Price", "Open", "High", "Low", "Vol.", "Change %"]
                        }
                    }
                },
            },
        }

        block = "\n".join(_format_executive_decision_block(state))
        target = _recommendation_target(0, "Validate drawdown trigger", "volatility and volume evidence")
        metric = _recommendation_metric("Validate drawdown trigger", "volatility and volume evidence")

        self.assertIn("rolling volatility", block)
        self.assertIn("drawdown", target)
        self.assertIn("forward return", metric)
        self.assertNotIn("precision", block.lower())
        self.assertNotIn("highest-risk segment", block.lower())

    def test_instruction_text_is_not_misclassified_as_stock_context(self) -> None:
        state = {
            "user_data_description": (
                "Teen mental health dataset. Produce dataset-specific EDA, clear visuals, "
                "decision-tree rules for depression_label, a business-readable report, and executive slides."
            ),
            "analysis_results": {},
            "agent_outputs": {
                "data_understander": {
                    "datasets": {
                        "Teen_Mental_Health_Dataset.csv": {
                            "columns": ["age", "stress_level", "sleep_hours", "depression_label"]
                        }
                    }
                },
                "decision_maker": {
                    "recommendations": [{"action": "Pilot targeted outreach"}],
                    "limitations": ["Self-report data requires validation."],
                },
            },
        }

        block = "\n".join(_format_executive_decision_block(state))

        self.assertNotIn("stock price", block.lower())
        self.assertNotIn("rolling volatility", block.lower())
        self.assertIn("outcome", block.lower())

    def test_analysis_findings_include_structured_visual_takeaways(self) -> None:
        findings = _format_analysis_findings(
            {
                "analysis_artifacts": [
                    {
                        "title": "Attrition by overtime",
                        "finding": "Employees with overtime show 2.1x the attrition rate of peers.",
                    }
                ],
                "business_findings": [],
            }
        )

        self.assertTrue(any("Attrition by overtime" in item for item in findings))
        self.assertTrue(any("2.1x" in item for item in findings))

    def test_figure_caption_lookup_accepts_basename_keys(self) -> None:
        caption = _figure_caption_for(
            r"C:\runs\latest\figure_1.png",
            {"figure_1.png": "Attrition is concentrated in overtime-heavy roles."},
        )

        self.assertIn("overtime-heavy", caption)

    def test_decision_tree_image_path_prefers_saved_artifact_png(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "decision_tree_rules.png"
            image_path.write_bytes(b"png")

            resolved = _decision_tree_image_path({"fallback_path": str(image_path), "data": {"nodes": []}})

            self.assertEqual(resolved, str(image_path))

    def test_pdf_tree_uses_saved_png_even_when_structured_graph_is_valid(self) -> None:
        class StubStyles(dict):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "decision_tree_rules.png"
            image_path.write_bytes(PNG_BYTES)
            story = []
            styles = StubStyles()
            styles["ReportFigureHeading"] = object()
            styles["ReportCaption"] = object()
            artifact = {
                "title": "Decision tree rules",
                "fallback_path": str(image_path),
                "data": {
                    "nodes": [
                        {"id": "root", "type": "split", "label": "OverTime <= 0.5"},
                        {"id": "left", "type": "leaf", "label": "Predict No"},
                        {"id": "right", "type": "leaf", "label": "Predict Yes"},
                    ],
                    "edges": [
                        {"source": "root", "target": "left", "label": "True"},
                        {"source": "root", "target": "right", "label": "False"},
                    ],
                },
            }

            with patch("analytics_workflow.reporting.Paragraph", side_effect=lambda text, style: ("paragraph", text)), patch(
                "analytics_workflow.reporting.Spacer", side_effect=lambda *args: ("spacer", args)
            ), patch("reportlab.platypus.Image", side_effect=lambda path, width, height: ("image", path)):
                _add_pdf_decision_tree_diagram(story, artifact, styles)

            self.assertTrue(any(item == ("image", str(image_path)) for item in story))
            self.assertFalse(any(item.__class__.__name__ == "Drawing" for item in story))

    def test_decision_tree_metric_keys_include_train_and_test_scores(self) -> None:
        keys = _decision_tree_metric_keys()

        self.assertIn("train_accuracy", keys)
        self.assertIn("test_accuracy", keys)
        self.assertIn("train_r2", keys)
        self.assertIn("test_r2", keys)

    def test_analysis_findings_have_fallback_when_empty(self) -> None:
        findings = _format_analysis_findings({})

        self.assertEqual(len(findings), 1)
        self.assertIn("no structured analysis findings were captured", findings[0].lower())

    def test_market_citations_preserve_declared_source_indexes(self) -> None:
        market = {
            "market_findings": [{"claim": "Benchmark growth is accelerating", "source_index": 3}],
            "sources_cited": [
                {"index": 3, "title": "Industry Outlook", "url": "https://example.com/outlook"},
                {"index": 7, "title": "Risk Note", "url": "https://example.com/risk"},
            ],
        }

        pairs = _market_claim_pairs(market)
        source_map = _source_index_map(market)

        self.assertIn("[3]", pairs[0][0])
        self.assertIn("Source [3]", pairs[0][1])
        self.assertEqual(source_map[3]["title"], "Industry Outlook")

    def test_workflow_trace_and_limitations_capture_decision_guardrails(self) -> None:
        state = {
            "status": "completed",
            "run_manifest": {
                "datasets": [{"name": "sample.csv"}],
                "figures": ["figure_1.png"],
                "agent_outputs": ["planner", "decision_maker"],
                "analysis_loop_iterations": 2,
                "final_code_present": True,
            },
            "agent_outputs": {
                "decision_maker": {
                    "limitations": [
                        {"limitation": "Sample ends before the latest quarter.", "mitigation": "Refresh the data."}
                    ]
                }
            },
        }

        trace = _format_workflow_trace(state)
        limitations = _format_limitations(state)

        self.assertTrue(any("coder-review loop iterations used: 2" in item for item in trace))
        self.assertTrue(any("Refresh the data" in item for item in limitations))

    def test_data_quality_notes_use_data_understander_findings(self) -> None:
        notes = _format_data_quality_notes(
            {
                "datasets": {
                    "sample.csv": {
                        "cleaning_priorities": ["Parse missing dates."],
                        "type_notes": ["Customer id is an identifier."],
                    }
                }
            }
        )

        self.assertTrue(any("Parse missing dates" in item for item in notes))
        self.assertTrue(any("identifier" in item for item in notes))

    def test_dataset_overview_and_quality_notes_tolerate_text_dataset_entries(self) -> None:
        data_understander = {
            "datasets": {
                "attrition.csv": "1470 rows profiled with the target field available."
            }
        }

        overview = _format_dataset_overview(data_understander)
        notes = _format_data_quality_notes(data_understander)

        self.assertIn("attrition.csv", overview)
        self.assertIn("1470 rows profiled", overview)
        self.assertTrue(any("target field available" in item for item in notes))

    def test_pdf_recommendations_include_owner_trigger_timeline_guardrail(self) -> None:
        from analytics_workflow.reporting import _format_recommendations

        recommendations = _format_recommendations(
            [
                {
                    "action": "Use tactical entry only after confirmation",
                    "owner": "investment committee",
                    "trigger": "price closes above resistance with volume support",
                    "timeline": "next monthly review",
                    "expected_impact": "better risk-adjusted entry discipline",
                    "validation_metric": "forward return, drawdown, and liquidity",
                    "risk": "stop if drawdown breaches the limit",
                    "evidence": "price and volume trend signals",
                }
            ]
        )
        text = " ".join(recommendations)

        self.assertIn("Owner: investment committee", text)
        self.assertIn("Trigger: price closes above resistance", text)
        self.assertIn("Timeline: next monthly review", text)
        self.assertIn("Expected impact: better risk-adjusted entry discipline", text)
        self.assertIn("Guardrail: forward return, drawdown, and liquidity", text)
        self.assertIn("Risk: stop if drawdown breaches the limit", text)

    def test_pdf_story_does_not_end_with_pagebreak_or_only_spacers(self) -> None:
        class Spacer:
            pass

        class PageBreak:
            pass

        story = ["content", Spacer(), PageBreak(), Spacer()]

        _trim_trailing_spacers_and_pagebreaks(story)

        self.assertEqual(story, ["content"])

    def test_pdf_generation_saves_report_outline_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "report.pdf"
            state = {
                "workflow_objective": {"raw_description": "Prioritize retention actions."},
                "saved_figures": [],
                "analysis_results": {
                    "analysis_summary": {"attrition_rate": "16%"},
                    "business_findings": ["Attrition is 16% overall."],
                    "analysis_artifacts": [
                        {
                            "artifact_id": "attrition_by_overtime",
                            "chart_type": "bar",
                            "finding": "Overtime employees have higher attrition.",
                            "data": [{"category": "Yes", "value": 31.0}],
                        }
                    ],
                },
                "agent_outputs": {
                    "data_understander": {"executive_summary": "Employee dataset", "datasets": {}},
                    "market_researcher": {},
                    "planner": {"objectives": ["Estimate attrition drivers."]},
                    "business_translator": {"key_findings": ["Overtime is the first retention lever."]},
                    "decision_maker": {
                        "executive_summary": "Act on overtime risk.",
                        "recommendations": [{"action": "Target overtime-heavy teams", "evidence": "31% attrition"}],
                        "final_recommendation": "Prioritize overtime-heavy teams.",
                    },
                },
            }

            generated_path = generate_pdf_report(state, str(output_path))
            outline_path = Path(temp_dir) / "report_outline.json"
            outline = json.loads(outline_path.read_text(encoding="utf-8"))

            self.assertEqual(generated_path, str(output_path))
            self.assertTrue(outline_path.exists())
            self.assertEqual(state["generated_reports"]["report_outline"], str(outline_path))
            self.assertEqual(outline["structured_chart_count"], 1)
            self.assertEqual(outline["decision_tree_artifact_count"], 0)
            self.assertFalse(any(section["title"] == "Decision Tree Model" for section in outline["sections"]))
            self.assertTrue(any(section["title"] == "Decision Recommendations" for section in outline["sections"]))

    def test_pdf_report_records_decision_tree_artifact_in_outline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "report.pdf"
            state = {
                "workflow_objective": {"raw_description": "Explain attrition drivers."},
                "decision_tree_target_column": "Attrition",
                "saved_figures": [],
                "analysis_results": {
                    "analysis_summary": {"decision_tree_accuracy": "78%", "sample": 100},
                    "business_findings": ["Decision tree accuracy is 78%."],
                    "analysis_artifacts": [
                        {
                            "artifact_id": "decision_tree_rules",
                            "artifact_type": "chart_spec",
                            "chart_type": "decision_tree",
                            "title": "Decision tree rules for Attrition",
                            "finding": "A shallow tree explains the main attrition split with 78% accuracy.",
                            "data": {
                                "train_accuracy": "82%",
                                "test_accuracy": "78%",
                                "baseline_accuracy": "83%",
                                "performance_note": "Explanatory model only: test accuracy 78% trails the baseline 83%; use the rules to guide investigation, not production prediction.",
                                "model_verified": True,
                                "rules_match_model": True,
                                "nodes": [
                                    {"id": "root", "type": "split", "label": "OverTime <= 0.5", "depth": 0},
                                    {"id": "left", "type": "leaf", "label": "Predict No", "depth": 1},
                                    {"id": "right", "type": "leaf", "label": "Predict Yes", "depth": 1},
                                ],
                                "edges": [
                                    {"source": "root", "target": "left", "label": "True"},
                                    {"source": "root", "target": "right", "label": "False"},
                                ],
                            },
                        }
                    ],
                },
                "agent_outputs": {
                    "data_understander": {"executive_summary": "Employee dataset", "datasets": {}},
                    "market_researcher": {},
                    "planner": {"objectives": ["Estimate attrition drivers."]},
                    "business_translator": {"key_findings": ["Overtime is the first split."]},
                    "decision_maker": {
                        "executive_summary": "Use the model as triage evidence.",
                        "recommendations": [{"action": "Target overtime-heavy teams", "evidence": "78% accuracy"}],
                        "final_recommendation": "Prioritize overtime-heavy teams.",
                    },
                },
            }

            generated_path = generate_pdf_report(state, str(output_path))
            outline = json.loads((Path(temp_dir) / "report_outline.json").read_text(encoding="utf-8"))

            self.assertEqual(generated_path, str(output_path))
            self.assertEqual(outline["decision_tree_artifact_count"], 1)
            self.assertIn("Explanatory model only", " ".join(outline["decision_tree_performance_notes"]))
            self.assertTrue(any(section["title"] == "Decision Tree Model" for section in outline["sections"]))


if __name__ == "__main__":
    unittest.main()
