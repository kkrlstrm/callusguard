import json
import os
import tempfile
import unittest

from callusguard.learn.artifacts import write_skill
from callusguard.learn.distill import distill
from callusguard.learn.models import EvidencePacket, Intervention, Pattern
from callusguard.learn.sources import normalize_attempt
from callusguard.learn.store import KnowledgeStore


class FakeProvider:
    def complete(self, system, payload):
        self.system = system
        self.payload = payload
        return json.dumps({
            "finding": "agents retry a dead path",
            "confidence": "medium",
            "root_cause": "fallback is not encoded",
            "intervention_type": "skill",
            "intervention": "After one failed direct fetch, switch retrieval strategy.",
            "why": "successful traces use a fallback",
            "expected_effect": "fewer repeated failures",
            "validation": "compare held-out traces",
            "evidence_refs": [],
        })


class TestLearningLayer(unittest.TestCase):
    def test_normalizes_host_specific_rows(self):
        attempt = normalize_attempt("codex", {
            "session_id": "s1", "call_id": "c1", "tool_name": "shell",
            "arguments": "curl x", "output": "boom", "exit_code": 1,
        })
        self.assertEqual(attempt.source, "codex")
        self.assertEqual(attempt.status, "failure")
        self.assertEqual(attempt.tool_call_id, "c1")

    def test_knowledge_survives_rejected_intervention(self):
        with tempfile.TemporaryDirectory() as td:
            store = KnowledgeStore(td)
            store.init()
            store.add_pattern(Pattern("dead-fetch", "direct fetch repeatedly fails"))
            store.add_intervention(Intervention(
                "try-flag", "dead-fetch", "rule", "recommend --foo",
                status="rejected", result="flag does not exist"))
            self.assertEqual(store.patterns()[0]["id"], "dead-fetch")
            self.assertEqual(store.interventions()[0]["status"], "rejected")

    def test_distillation_is_structured_but_not_activated(self):
        provider = FakeProvider()
        proposal = distill(EvidencePacket("why?", "30d"), provider)
        self.assertEqual(proposal.intervention_type, "skill")
        self.assertIn("least restrictive", provider.system)

    def test_skill_has_separate_purpose_provenance(self):
        provider = FakeProvider()
        proposal = distill(EvidencePacket("why?", "30d"), provider)
        pattern = Pattern("fetch-fallback", "retries cluster after direct fetch failure")
        with tempfile.TemporaryDirectory() as td:
            out = write_skill(proposal, pattern, td)
            self.assertTrue((out / "SKILL.md").exists())
            self.assertTrue((out / "PURPOSE.md").exists())
            self.assertIn("Pattern: `fetch-fallback`", (out / "PURPOSE.md").read_text())


if __name__ == "__main__":
    unittest.main()
