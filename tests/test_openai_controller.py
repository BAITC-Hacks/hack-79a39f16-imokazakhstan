"""No network or API credentials are needed for these controller checks."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wind_forecast.agent.openai_controller import OpenAIController


def response(name=None, *, arguments="{}", call_id="call-1", report="", extra=None):
    output = list(extra or [])
    if name is not None:
        output.append(SimpleNamespace(
            type="function_call", name=name, arguments=arguments, call_id=call_id,
        ))
    return SimpleNamespace(
        output=output, output_text=report, status="completed",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class FakeClient:
    def __init__(self, responses):
        self.remaining = iter(responses)
        self.requests = []
        self.responses = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        result = next(self.remaining)
        if isinstance(result, Exception):
            raise result
        return result


class OpenAIControllerTests(unittest.TestCase):
    def controller(self, client, **kwargs):
        return OpenAIController("test-secret-key", "test-model", client=client, **kwargs)

    def test_sequential_tools_preserve_reasoning_call_ids_and_summary(self):
        names = [
            "fetch_weather", "audit_inputs", "predict_power", "inspect_forecast", "save_forecast",
        ]
        reasoning = SimpleNamespace(type="reasoning", id="reason-1", summary=[])
        responses = [
            response(name, call_id=f"call-{i}", extra=[reasoning] if i == 0 else [])
            for i, name in enumerate(names)
        ]
        client = FakeClient(responses + [response(report="Synthetic fixture saved.")])
        called = []

        def tool(name):
            def run():
                self.assertEqual(name, names[len(called)])
                called.append(name)
                return {"status": "ok", "synthetic": True}
            return run

        outcome = self.controller(client).run(
            {"mode": "fixture"}, {name: tool(name) for name in names},
            lambda: called == names,
        )
        self.assertTrue(outcome.completed)
        self.assertIsNone(outcome.fallback_reason)
        self.assertEqual(called, names)
        self.assertIn("AI commentary", outcome.report)
        self.assertIn("Synthetic fixture saved.", outcome.report)
        self.assertEqual(outcome.usage, {
            "input_tokens": 60, "output_tokens": 30, "total_tokens": 90, "api_calls": 6,
        })
        self.assertIs(client.requests[1]["input"][1], reasoning)
        tool_output = client.requests[1]["input"][-1]
        self.assertEqual(tool_output["call_id"], "call-0")
        self.assertEqual(json.loads(tool_output["output"]), {"status": "ok", "synthetic": True})
        for request in client.requests:
            self.assertFalse(request["parallel_tool_calls"])
            self.assertFalse(request["store"])
            self.assertEqual(request["max_output_tokens"], 900)
            for schema in request["tools"]:
                self.assertTrue(schema["strict"])
                self.assertEqual(schema["parameters"]["properties"], {})
                self.assertFalse(schema["parameters"]["additionalProperties"])
        self.assertEqual(client.requests[-1]["tool_choice"], "none")
        self.assertNotIn("test-secret-key", json.dumps(outcome.events))
        self.assertNotIn("test-model", json.dumps(outcome.events))

    def test_unknown_tool_does_not_execute_or_echo_model_input(self):
        called = []
        client = FakeClient([response("test-secret-key")])
        outcome = self.controller(client).run(
            {}, {"fetch_weather": lambda: called.append(True)}, lambda: False,
        )
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.fallback_reason, "unknown_tool")
        self.assertEqual(called, [])
        self.assertNotIn("test-secret-key", json.dumps(outcome.events))

    def test_arguments_must_be_empty_object(self):
        for arguments in ('{"path":"test-secret-key"}', "[]", "null", "broken", " "):
            with self.subTest(arguments=arguments):
                called = []
                client = FakeClient([response("fetch_weather", arguments=arguments)])
                outcome = self.controller(client).run(
                    {}, {"fetch_weather": lambda: called.append(True)}, lambda: False,
                )
                self.assertEqual(outcome.fallback_reason, "invalid_tool_arguments")
                self.assertFalse(outcome.completed)
                self.assertEqual(called, [])

    def test_round_limit_and_final_round_completion(self):
        for complete in (False, True):
            with self.subTest(complete=complete):
                called = []
                client = FakeClient([response("fetch_weather")])

                def tool():
                    called.append(True)
                    return {"ok": True}

                outcome = self.controller(client, max_rounds=1).run(
                    {}, {"fetch_weather": tool}, lambda: complete and bool(called),
                )
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(outcome.completed, complete)
                self.assertEqual(outcome.fallback_reason, None if complete else "round_limit")

    def test_api_exception_is_sanitized(self):
        for error in (RuntimeError("test-secret-key"), TimeoutError("test-model")):
            client = FakeClient([error])
            outcome = self.controller(client).run(
                {}, {"fetch_weather": lambda: {}}, lambda: False,
            )
            self.assertFalse(outcome.completed)
            self.assertEqual(outcome.fallback_reason, f"api_error:{type(error).__name__}")
            self.assertNotIn("test-secret-key", json.dumps(outcome.events))
            self.assertNotIn("test-model", json.dumps(outcome.events))

    def test_no_calls_before_completion_falls_back(self):
        client = FakeClient([response(report="I made up a forecast.")])
        outcome = self.controller(client).run(
            {}, {"fetch_weather": lambda: {}}, lambda: False,
        )
        self.assertEqual(outcome.fallback_reason, "no_tool_before_completion")
        self.assertEqual(outcome.report, "")

    def test_order_rejection_by_local_tool_is_sanitized(self):
        def rejected():
            raise ValueError("test-secret-key: audit must run first")

        client = FakeClient([response("predict_power")])
        outcome = self.controller(client).run({}, {"predict_power": rejected}, lambda: False)
        self.assertEqual(outcome.fallback_reason, "tool_error:ValueError")
        self.assertNotIn("test-secret-key", json.dumps(outcome.events))

    def test_missing_key_does_not_create_client(self):
        client = FakeClient([])
        outcome = OpenAIController("", "test-model", client=client).run(
            {}, {"fetch_weather": lambda: {}}, lambda: False,
        )
        self.assertEqual(outcome.fallback_reason, "missing_api_key")
        self.assertEqual(client.requests, [])

    def test_sdk_constructor_sets_timeout_and_disables_retries(self):
        client = FakeClient([response(report="Done.")])
        constructor = Mock(return_value=client)
        with patch.dict("sys.modules", {"openai": SimpleNamespace(OpenAI=constructor)}):
            controller = OpenAIController("test-secret-key", "test-model", timeout_seconds=11)
            self.assertEqual(constructor.call_count, 0)
            outcome = controller.run({}, {"fetch_weather": lambda: {}}, lambda: True)
        self.assertTrue(outcome.completed)
        constructor.assert_called_once_with(api_key="test-secret-key", timeout=11, max_retries=0)

    def test_incomplete_api_output_does_not_execute_tools(self):
        truncated = response("fetch_weather")
        truncated.status = "incomplete"
        client = FakeClient([truncated])
        called = []
        outcome = self.controller(client).run(
            {}, {"fetch_weather": lambda: called.append(True)}, lambda: False,
        )
        self.assertEqual(outcome.fallback_reason, "incomplete_response")
        self.assertEqual(called, [])

    def test_reused_call_id_does_not_execute_twice(self):
        client = FakeClient([response("fetch_weather"), response("fetch_weather")])
        called = []

        def tool():
            called.append(True)
            return {"ok": True}

        outcome = self.controller(client).run({}, {"fetch_weather": tool}, lambda: False)
        self.assertEqual(outcome.fallback_reason, "invalid_call_id")
        self.assertEqual(len(called), 1)

    def test_parallel_calls_rejected_before_execution(self):
        first = response("fetch_weather", call_id="a")
        first.output.extend(response("audit_inputs", call_id="b").output)
        client = FakeClient([first])
        called = []
        outcome = self.controller(client).run(
            {}, {"fetch_weather": lambda: called.append(True)}, lambda: False,
        )
        self.assertEqual(outcome.fallback_reason, "unexpected_tool_calls")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
