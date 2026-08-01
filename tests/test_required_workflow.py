from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zipviz-mcp-required.yml"


class RequiredWorkflowTests(unittest.TestCase):
    def test_one_central_workflow_contains_the_complete_pilot_gate(self) -> None:
        self.assertTrue(WORKFLOW.exists(), "central required workflow must exist")
        text = WORKFLOW.read_text()

        self.assertIn("pull_request:", text)
        self.assertIn("merge_group:", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertIn("github.repository == 'Elusor8/zipviz-mcp'", text)
        self.assertIn("npm ci", text)
        self.assertIn("npm test", text)
        self.assertIn("npm run build", text)

    def test_workflow_is_read_only_secret_free_and_drops_checkout_credentials(self) -> None:
        text = WORKFLOW.read_text()

        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)

    def test_every_action_is_immutably_pinned(self) -> None:
        text = WORKFLOW.read_text()
        uses = re.findall(r"^\s*- uses: ([^\s]+)$", text, flags=re.MULTILINE)

        self.assertGreaterEqual(len(uses), 2)
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_workflow_has_time_and_concurrency_bounds(self) -> None:
        text = WORKFLOW.read_text()

        self.assertIn("timeout-minutes:", text)
        self.assertIn("cancel-in-progress:", text)


if __name__ == "__main__":
    unittest.main()
