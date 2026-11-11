import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from flashtrustfulrsi.deepseek_debug import DebugBudget, DeepSeekProvider, MODEL, preflight
from flashtrustfulrsi.transport import AmbiguousRequest, BudgetExceeded, ReplayConflict, TransportError


def response(content="PROBE_OK", calls=None):
    message = {"content": content}
    if calls:
        message["tool_calls"] = calls
    return {"id": "fixture", "model": MODEL, "choices": [
        {"message": message, "finish_reason": "tool_calls" if calls else "stop"}
    ], "usage": {"prompt_tokens": 12, "completion_tokens": 4}}


class DeepSeekDebugTests(unittest.TestCase):
    def test_undeclared_tool_response_is_archived_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'DEEPSEEK_API_KEY':'fixture'}):
            provider=DeepSeekProvider(Path(tmp)/'provider','contract')
            wrong=response(None,[{'id':'one','type':'function','function':{'name':'read_file','arguments':'{}'}}])
            body={'model':MODEL,'messages':[{'role':'user','content':'finish'}],'max_tokens':128,
                  'tools':[{'type':'function','function':{'name':'checkpoint','parameters':{'type':'object'}}}]}
            with patch.object(provider,'_send',return_value=wrong) as send:
                with self.assertRaises(TransportError):provider.invoke('chat',body,'one')
                with self.assertRaises(TransportError):provider.invoke('chat',body,'one')
                self.assertEqual(send.call_count,1)
            self.assertEqual(provider.summary()['calls'][0]['state'],'invalid_response')
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-only"})
        env.start()
        self.addCleanup(env.stop)
        self.path = Path(self.temp.name) / "run"

    def test_preflight_tool_round_trip_replay_and_secret_absence(self):
        provider = DeepSeekProvider(self.path, "preflight")
        calls = [{"id": "tool-fixture", "type": "function", "function":
                  {"name": "echo_probe", "arguments": '{"value":"PROBE_OK"}'}}]
        fixtures = [response(), response('{"ok":true}'), response(None, calls), response()]
        with patch.object(provider, "_send", side_effect=fixtures) as send:
            self.assertEqual(preflight(provider)["requests"], 4)
            self.assertEqual(preflight(provider)["reported_input"], 48)
            self.assertEqual(send.call_count, 4)
            last = json.loads(send.call_args.args[0])
            self.assertEqual(last["messages"][-1]["tool_call_id"], "tool-fixture")
            self.assertEqual(last["thinking"], {"type": "disabled"})
        self.assertNotIn(b"unit-test-only", provider.db.read_bytes())

    def test_pending_call_reserves_budget_across_threads(self):
        provider = DeepSeekProvider(self.path, "one", DebugBudget(requests=1))
        started, release = threading.Event(), threading.Event()

        def send(*args):
            started.set()
            self.assertTrue(release.wait(5))
            return response()

        messages = [{"role": "user", "content": "test"}]
        with patch.object(provider, "_send", side_effect=send), ThreadPoolExecutor() as pool:
            first = pool.submit(provider.complete_sync, messages, request_id="first")
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaises(BudgetExceeded):
                    provider.complete_sync(messages, request_id="second")
                with self.assertRaises(AmbiguousRequest):
                    provider.complete_sync(messages, request_id="first")
            finally:
                release.set()
            self.assertEqual(first.result()["id"], "fixture")

    def test_timeout_preserves_unknown_usage_and_disallows_resend(self):
        provider = DeepSeekProvider(self.path, "ambiguous")
        messages = [{"role": "user", "content": "test"}]
        with patch.object(provider, "_send", side_effect=TimeoutError) as send:
            for _ in range(2):
                with self.assertRaises(AmbiguousRequest):
                    provider.complete_sync(messages, request_id="first")
            self.assertEqual(send.call_count, 1)
        report = provider.summary()
        self.assertEqual(report["usage_unknown_calls"], 1)
        self.assertEqual(report["accounted_output"], 256)
        self.assertEqual(report["calls"][0]["state"], "transport_unknown")
        with self.assertRaises(ReplayConflict):
            DeepSeekProvider(self.path, "ambiguous", DebugBudget(requests=5))
        with self.assertRaises(ReplayConflict):
            provider.complete_sync([{"role": "user", "content": "changed"}], request_id="first")

    def test_invalid_model_and_response_are_not_successes(self):
        provider = DeepSeekProvider(self.path, "invalid")
        messages = [{"role": "user", "content": "test"}]
        with patch.object(provider, "_send", return_value=[]) as send:
            with self.assertRaises(ValueError):
                provider.invoke("chat", {"model": "other", "messages": messages, "max_tokens": 5}, "bad")
            self.assertEqual(send.call_count, 0)
            with self.assertRaises(TransportError):
                provider.complete_sync(messages, request_id="first")
        self.assertEqual(provider.summary()["calls"][0]["state"], "invalid_response")

    def test_input_and_output_caps_stop_before_network(self):
        provider = DeepSeekProvider(self.path, "tokens", DebugBudget(input_tokens=100, output_tokens=64, per_call_output=32))
        messages = [{"role": "user", "content": "test"}]
        with patch.object(provider, "_send") as send:
            for cap in (32, 33):
                with self.assertRaises(BudgetExceeded):
                    provider.complete_sync(messages, max_tokens=cap, request_id="blocked")
            send.assert_not_called()
        self.assertEqual(provider.summary()["requests"], 0)


if __name__ == "__main__":
    unittest.main()
