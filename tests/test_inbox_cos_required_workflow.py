from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "inbox-cos-required.yml"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


class InboxCosRequiredWorkflowTests(unittest.TestCase):
    def workflow_text(self) -> str:
        self.assertTrue(WORKFLOW.exists(), "inbox-cos central required workflow must exist")
        return WORKFLOW.read_text()

    def test_one_always_running_gate_executes_the_full_contract(self) -> None:
        text = self.workflow_text()

        self.assertIn("pull_request:", text)
        self.assertIn("merge_group:", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertIn("jobs:\n  gate:", text)
        self.assertEqual(len(re.findall(r"(?m)^  [a-zA-Z0-9_-]+:\s*$", text.split("jobs:\n", 1)[1])), 1)
        self.assertIn('python -m pip install ".[dev]"', text)
        self.assertIn("python -m pytest tests/ -q", text)
        self.assertIn("python -m unittest discover -s tests -v", text)

    def test_unrecognized_repository_fails_before_checkout(self) -> None:
        text = self.workflow_text()

        guard = text.index('case "$REPOSITORY" in')
        checkout = text.index("uses: actions/checkout@")
        self.assertLess(guard, checkout)
        self.assertIn("Elusor8/inbox-cos|Elusor8/.github)", text)
        self.assertIn('echo "::error::unrecognized required-workflow target: $REPOSITORY"', text)
        self.assertIn("exit 1", text)

    def test_repository_contract_has_no_conditional_skip_path(self) -> None:
        text = self.workflow_text()

        self.assertNotRegex(text, r"(?m)^\s+if:\s*")
        self.assertIn('case "$REPOSITORY" in', text)
        self.assertIn("Elusor8/inbox-cos)", text)
        self.assertIn("Elusor8/.github)", text)
        self.assertIn("python -m pytest tests/ -q", text)
        self.assertIn("python -m unittest discover -s tests -v", text)

    def test_permissions_checkout_and_secret_boundary_are_fail_closed(self) -> None:
        text = self.workflow_text()

        self.assertEqual(text.count("permissions:"), 1)
        self.assertIn("permissions:\n  contents: read\n", text)
        self.assertNotIn("write-all", text)
        self.assertNotRegex(text, r"(?m)^\s+[A-Za-z-]+:\s*write\s*$")
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("${{ secrets.", text)

    def test_actions_are_exact_verified_immutable_pins(self) -> None:
        text = self.workflow_text()
        uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)\s*$", text, flags=re.MULTILINE)

        self.assertEqual(
            uses,
            [
                f"actions/checkout@{CHECKOUT_SHA}",
                f"actions/setup-python@{SETUP_PYTHON_SHA}",
            ],
        )
        self.assertEqual(text.count("uses:"), len(uses))
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_python_runtime_time_and_concurrency_are_bounded(self) -> None:
        text = self.workflow_text()

        self.assertIn("python-version: '3.11'", text)
        self.assertIn("timeout-minutes:", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("concurrency:", text)


if __name__ == "__main__":
    unittest.main()
