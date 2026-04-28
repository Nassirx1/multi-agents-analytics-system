import json
import unittest
from unittest.mock import patch, MagicMock

from analytics_workflow.clients import OpenRouterClient


def _response(json_body, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class OpenRouterContentExtractionTests(unittest.TestCase):
    def test_extracts_string_content(self) -> None:
        data = {"choices": [{"message": {"content": "  hello world  "}}]}
        content, reason = OpenRouterClient._extract_message_content(data)
        self.assertEqual(content, "hello world")
        self.assertEqual(reason, "")

    def test_extracts_list_content(self) -> None:
        data = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "hello "},
                            {"type": "text", "text": "world"},
                        ]
                    }
                }
            ]
        }
        content, reason = OpenRouterClient._extract_message_content(data)
        self.assertEqual(content, "hello world")
        self.assertEqual(reason, "")

    def test_none_content_reports_finish_reason(self) -> None:
        data = {"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]}
        content, reason = OpenRouterClient._extract_message_content(data)
        self.assertEqual(content, "")
        self.assertIn("finish_reason=content_filter", reason)

    def test_empty_choices_reports_clear_reason(self) -> None:
        content, reason = OpenRouterClient._extract_message_content({"choices": []})
        self.assertEqual(content, "")
        self.assertIn("no choices", reason)

    def test_upstream_error_field_is_surfaced(self) -> None:
        data = {"error": {"message": "rate limited"}}
        content, reason = OpenRouterClient._extract_message_content(data)
        self.assertEqual(content, "")
        self.assertIn("rate limited", reason)

    def test_refusal_is_surfaced(self) -> None:
        data = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "", "refusal": "I cannot help with that."},
                }
            ]
        }
        content, reason = OpenRouterClient._extract_message_content(data)
        self.assertEqual(content, "")
        self.assertIn("refusal=", reason)


class OpenRouterRetryBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OpenRouterClient("k", "deepseek/deepseek-v3.2")

    def test_retries_on_empty_content_then_returns_valid(self) -> None:
        responses = [
            _response({"choices": [{"finish_reason": "stop", "message": {"content": None}}]}),
            _response({"choices": [{"message": {"content": "good"}}]}),
        ]
        with patch.object(self.client.session, "post", side_effect=responses), patch(
            "analytics_workflow.clients.time.sleep"
        ):
            result = self.client.chat_completion("sys", "user", max_retries=3)
        self.assertEqual(result, "good")

    def test_raises_clear_error_when_all_retries_return_empty(self) -> None:
        empty = _response({"choices": [{"finish_reason": "length", "message": {"content": None}}]})
        with patch.object(
            self.client.session, "post", side_effect=[empty, empty, empty]
        ), patch("analytics_workflow.clients.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "finish_reason=length"):
                self.client.chat_completion("sys", "user", max_retries=3)
        self.assertEqual(self.client.cost_tracker.failed_calls, 1)

    def test_chat_completion_escalates_max_tokens_on_length_truncation(self) -> None:
        length_empty = _response(
            {"choices": [{"finish_reason": "length", "message": {"content": None}}]}
        )
        good = _response({"choices": [{"message": {"content": "ok"}}]})
        captured_max_tokens: list[int] = []

        def fake_post(url, json=None, timeout=None):
            captured_max_tokens.append(json["max_tokens"])
            return [length_empty, length_empty, good][len(captured_max_tokens) - 1]

        with patch.object(self.client.session, "post", side_effect=fake_post), patch(
            "analytics_workflow.clients.time.sleep"
        ):
            result = self.client.chat_completion("sys", "user", max_retries=3)
        self.assertEqual(result, "ok")
        self.assertEqual(captured_max_tokens, [4000, 6000, 8000])

    def test_chat_completion_does_not_escalate_on_non_length_reasons(self) -> None:
        stop_empty = _response(
            {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
        )
        good = _response({"choices": [{"message": {"content": "ok"}}]})
        captured_max_tokens: list[int] = []

        def fake_post(url, json=None, timeout=None):
            captured_max_tokens.append(json["max_tokens"])
            return [stop_empty, good][len(captured_max_tokens) - 1]

        with patch.object(self.client.session, "post", side_effect=fake_post), patch(
            "analytics_workflow.clients.time.sleep"
        ):
            self.client.chat_completion("sys", "user", max_retries=3)
        self.assertEqual(captured_max_tokens, [4000, 4000])


class OpenRouterJsonRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OpenRouterClient("k", "deepseek/deepseek-v3.2")

    def test_chat_completion_json_retries_once_on_decode_error_then_logs(self) -> None:
        responses = ["not json at all", json.dumps({"answer": 42})]
        captured_user_prompts: list[str] = []

        def fake_chat(system_prompt, user_prompt):
            captured_user_prompts.append(user_prompt)
            return responses[len(captured_user_prompts) - 1]

        with patch.object(self.client, "chat_completion", side_effect=fake_chat), \
             self.assertLogs("OpenRouterClient", level="ERROR") as captured_logs:
            result = self.client.chat_completion_json("sys", "user", {"answer": "int"})

        self.assertEqual(result, {"answer": 42})
        self.assertEqual(len(captured_user_prompts), 2)
        self.assertIn("previous response was not valid JSON", captured_user_prompts[1])
        self.assertTrue(
            any("failed to parse JSON" in line for line in captured_logs.output),
            captured_logs.output,
        )

    def test_chat_completion_json_returns_sentinel_when_retry_also_invalid(self) -> None:
        with patch.object(self.client, "chat_completion", side_effect=["bad", "still bad"]), \
             self.assertLogs("OpenRouterClient", level="ERROR"):
            result = self.client.chat_completion_json("sys", "user", {"a": "b"})
        self.assertIn("raw_text", result)
        self.assertIn("parse_error", result)
        self.assertEqual(result["raw_text"], "still bad")


if __name__ == "__main__":
    unittest.main()
