"""Parser + store tests. Stdlib unittest, no DB or network. Runs against a
synthetic rollout that mirrors the real Codex 0.140 event shapes."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from callusguard.telemetry.codex.parse import parse_session  # noqa: E402
from callusguard.telemetry.codex.store import SQLiteStore  # noqa: E402

ROLLOUT = [
    {"timestamp": "2026-07-06T13:34:11.648Z", "type": "session_meta",
     "payload": {"id": "test-sess-0001", "parent_thread_id": "parent-0009",
                 "cwd": "/repo", "originator": "codex_vscode",
                 "cli_version": "0.140.0-alpha.2", "model_provider": "openai",
                 "thread_source": "subagent",
                 "source": {"subagent": {"other": "guardian"}}}},
    {"timestamp": "2026-07-06T13:34:11.700Z", "type": "event_msg",
     "payload": {"type": "task_started", "turn_id": "turn-1"}},
    {"timestamp": "2026-07-06T13:34:11.750Z", "type": "turn_context",
     "payload": {"turn_id": "turn-1", "model": "gpt-5.4-mini", "cwd": "/repo"}},
    {"timestamp": "2026-07-06T13:34:12.000Z", "type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "do the thing"}]}},
    {"timestamp": "2026-07-06T13:34:12.100Z", "type": "response_item",
     "payload": {"type": "message", "role": "assistant", "phase": "commentary",
                 "content": [{"type": "output_text", "text": "on it"}]}},
    {"timestamp": "2026-07-06T13:34:12.200Z", "type": "response_item",
     "payload": {"type": "function_call", "name": "exec_command",
                 "arguments": "{\"cmd\":\"echo hi\"}", "call_id": "call_A"}},
    {"timestamp": "2026-07-06T13:34:12.300Z", "type": "response_item",
     "payload": {"type": "function_call_output", "call_id": "call_A",
                 "output": "Output:\nhi\nProcess exited with code 0"}},
    {"timestamp": "2026-07-06T13:34:12.400Z", "type": "response_item",
     "payload": {"type": "function_call", "name": "apply_patch",
                 "arguments": "{\"input\":\"*** Begin Patch\"}", "call_id": "call_B"}},
    {"timestamp": "2026-07-06T13:34:12.500Z", "type": "response_item",
     "payload": {"type": "function_call_output", "call_id": "call_B",
                 "output": "Process exited with code 1"}},
    {"timestamp": "2026-07-06T13:34:13.000Z", "type": "event_msg",
     "payload": {"type": "token_count", "info": {
         "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 10,
                               "output_tokens": 20, "reasoning_output_tokens": 5,
                               "total_tokens": 120},
         "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 10,
                              "output_tokens": 20, "reasoning_output_tokens": 5,
                              "total_tokens": 120}}}},
]


def _write_rollout(lines):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for o in lines:
            f.write(json.dumps(o) + "\n")
    return path


class TestParse(unittest.TestCase):
    def setUp(self):
        self.path = _write_rollout(ROLLOUT)
        self.s = parse_session(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_session_identity(self):
        self.assertEqual(self.s.session_id, "test-sess-0001")
        self.assertEqual(self.s.parent_thread_id, "parent-0009")
        self.assertEqual(self.s.subagent_type, "guardian")
        self.assertEqual(self.s.originator, "codex_vscode")
        self.assertEqual(self.s.cli_version, "0.140.0-alpha.2")

    def test_model_from_turn_context(self):
        self.assertEqual(self.s.model, "gpt-5.4-mini")

    def test_tool_calls_all_types(self):
        names = {c.tool_name for c in self.s.tool_calls}
        self.assertEqual(names, {"exec_command", "apply_patch"})

    def test_exit_code_and_status(self):
        by = {c.call_id: c for c in self.s.tool_calls}
        self.assertEqual(by["call_A"].exit_code, 0)
        self.assertEqual(by["call_A"].status, "success")
        self.assertEqual(by["call_B"].exit_code, 1)
        self.assertEqual(by["call_B"].status, "failure")

    def test_tokens(self):
        self.assertEqual(self.s.total_tokens, 120)
        self.assertEqual(self.s.reasoning_tokens, 5)
        self.assertEqual(self.s.turns[0].total_tokens, 120)

    def test_messages(self):
        roles = [m.role for m in self.s.messages]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_malformed_line_skipped(self):
        path = _write_rollout(ROLLOUT)
        with open(path, "a") as f:
            f.write("{not json\n")
        s = parse_session(path)
        self.assertIsNotNone(s)
        os.remove(path)


class TestStore(unittest.TestCase):
    def test_roundtrip_and_idempotent(self):
        path = _write_rollout(ROLLOUT)
        s = parse_session(path)
        dbfd, dbpath = tempfile.mkstemp(suffix=".db")
        os.close(dbfd)
        try:
            store = SQLiteStore(dbpath)
            store.upsert_session(s)
            store.upsert_session(s)  # second time must not duplicate
            store.commit()
            rows = store.query("SELECT COUNT(*) c FROM tool_calls")
            self.assertEqual(rows[0]["c"], 2)
            srow = store.query("SELECT total_tokens FROM sessions")[0]
            self.assertEqual(srow["total_tokens"], 120)
            store.close()
        finally:
            os.remove(dbpath)
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
