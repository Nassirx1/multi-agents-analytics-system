import unittest

from analytics_workflow.pipeline_runtime import BusinessInsightsTranslatorAgent


class FakeOpenRouterClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat_completion_json(self, system_prompt, user_prompt, schema):
        self.calls += 1
        return {
            "executive_summary": "Technical findings were translated into business meaning.",
            "key_findings": [
                {
                    "finding": "Volume increased alongside price strength.",
                    "business_implication": "Momentum may support near-term confidence.",
                    "priority": "High",
                }
            ],
            "business_narrative": "The analysis supports a focused business interpretation.",
            "risks": ["Volatility could reverse recent gains."],
            "opportunities": ["Use momentum-aware monitoring."],
            "immediate_actions": ["Review the strongest signal weekly."],
        }


class BusinessTranslatorTests(unittest.TestCase):
    def test_execute_returns_after_single_client_call(self) -> None:
        client = FakeOpenRouterClient()
        agent = BusinessInsightsTranslatorAgent(
            "Business Translator",
            "Business Intelligence Expert",
            "executive translation",
            client,
        )

        result = agent.execute(
            analysis_results={"analysis_summary": {"average_return_pct": 2.4}},
            data_context={"dataset": "stc_prices"},
            market_context={"industry_overview": "Telecom momentum remains relevant."},
        )

        self.assertEqual(client.calls, 1)
        self.assertIn("executive_summary", result)
        self.assertIn("business_narrative", result)


if __name__ == "__main__":
    unittest.main()
