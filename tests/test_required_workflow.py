from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zipviz-mcp-required.yml"


class RequiredWorkflowTests(unittest.TestCase):
    def test_one_always_running_gate_contains_the_complete_pilot_check(self) -> None:
        self.assertTrue(WORKFLOW.exists(), "central required workflow must exist")
        text = WORKFLOW.read_text()

        self.assertIn("pull_request:", text)
        self.assertIn("merge_group:", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertIn("jobs:\n  gate:", text)
        self.assertNotIn("\n  verify:", text)
        self.assertNotIn("\n  governance-self-test:", text)
        self.assertIn("npm ci", text)
        self.assertIn("npm test", text)
        self.assertIn("npm run build", text)

    def test_unrecognized_repository_fails_before_checkout(self) -> None:
        text = WORKFLOW.read_text()

        guard = text.index('case "$REPOSITORY" in')
        checkout = text.index("uses: actions/checkout@")
        self.assertLess(guard, checkout)
        self.assertIn("Elusor8/zipviz-mcp|Elusor8/.github)", text)
        self.assertIn('echo "::error::unrecognized required-workflow target: $REPOSITORY"', text)
        self.assertIn("exit 1", text)

    def test_workflow_has_one_read_only_permission_block_and_no_secret_access(self) -> None:
        text = WORKFLOW.read_text()

        self.assertEqual(text.count("permissions:"), 1)
        self.assertIn("permissions:\n  contents: read\n", text)
        self.assertNotIn("write-all", text)
        self.assertNotRegex(text, r"(?m)^\s+[A-Za-z-]+:\s*write\s*$")
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("${{ secrets.", text)

    def test_every_action_is_immutably_pinned(self) -> None:
        text = WORKFLOW.read_text()
        uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)\s*$", text, flags=re.MULTILINE)

        self.assertGreaterEqual(len(uses), 2)
        self.assertEqual(text.count("uses:"), len(uses))
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_workflow_has_time_and_concurrency_bounds(self) -> None:
        text = WORKFLOW.read_text()

        self.assertIn("timeout-minutes:", text)
        self.assertIn("cancel-in-progress:", text)


if __name__ == "__main__":
    unittest.main()
