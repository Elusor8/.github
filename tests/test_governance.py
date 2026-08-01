from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "governance" / "repositories.json"
RENDERER = ROOT / "scripts" / "render_callers.py"
RULESET_RENDERER = ROOT / "scripts" / "render_ruleset.py"
REUSABLE_WORKFLOW = ROOT / ".github" / "workflows" / "ci-reusable.yml"


class GovernanceManifestTests(unittest.TestCase):
    def test_manifest_contains_exact_seven_live_repositories(self) -> None:
        self.assertTrue(MANIFEST.exists(), "governance manifest must exist")
        data = json.loads(MANIFEST.read_text())
        names = {repo["name"] for repo in data["repositories"]}
        self.assertEqual(
            names,
            {
                "zipviz-api",
                "zipviz-workers",
                "zipviz-mcp",
                "zipviz-mailguard",
                "inbox-cos",
                "zipviz-behavior-api",
                "zipviz-site",
            },
        )

    def test_renderer_exists(self) -> None:
        self.assertTrue(RENDERER.exists(), "caller renderer must exist")

    def test_renderer_rejects_moving_workflow_reference(self) -> None:
        with tempfile.TemporaryDirectory() as output_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--workflow-ref",
                    "main",
                    "--output-dir",
                    output_dir,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("40-character commit SHA", result.stderr)

    def test_renderer_creates_pinned_secret_free_callers_for_all_repositories(self) -> None:
        workflow_ref = "a" * 40
        with tempfile.TemporaryDirectory() as output_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--workflow-ref",
                    workflow_ref,
                    "--output-dir",
                    output_dir,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            callers = list(Path(output_dir).glob("*/.github/workflows/ci.yml"))
            self.assertEqual(len(callers), 7)
            for caller in callers:
                text = caller.read_text()
                self.assertIn(f"@{workflow_ref}", text)
                self.assertIn("contents: read", text)
                self.assertNotIn("secrets:", text)
            inbox = Path(output_dir) / "inbox-cos" / ".github/workflows/ci.yml"
            self.assertIn("branches: [master]", inbox.read_text())
            mcp = Path(output_dir) / "zipviz-mcp" / ".github/workflows/ci.yml"
            self.assertIn("profile: node-mcp", mcp.read_text())

    def test_reusable_workflow_has_no_secret_or_arbitrary_command_inputs(self) -> None:
        self.assertTrue(REUSABLE_WORKFLOW.exists(), "reusable workflow must exist")
        text = REUSABLE_WORKFLOW.read_text()
        self.assertIn("workflow_call:", text)
        self.assertIn("contents: read", text)
        self.assertIn("timeout-minutes:", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("command:", text)
        for profile in (
            "node-api",
            "node-workers",
            "node-mcp",
            "node-site",
            "python-service",
        ):
            self.assertIn(profile, text)

    def test_ruleset_plan_is_evaluate_only_additive_and_reversible(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(RULESET_RENDERER),
                "plan",
                "--repo",
                "zipviz-mcp",
                "--check-context",
                "verify / verify",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["operation"], "create_org_ruleset")
        self.assertEqual(plan["payload"]["enforcement"], "evaluate")
        self.assertEqual(
            plan["payload"]["conditions"]["repository_name"]["include"],
            ["zipviz-mcp"],
        )
        self.assertEqual(
            plan["payload"]["conditions"]["ref_name"]["include"],
            ["~DEFAULT_BRANCH"],
        )
        self.assertEqual(plan["payload"]["bypass_actors"], [])
        rules = {rule["type"]: rule for rule in plan["payload"]["rules"]}
        pull_request = rules["pull_request"]["parameters"]
        self.assertTrue(pull_request["require_code_owner_review"])
        self.assertTrue(pull_request["dismiss_stale_reviews_on_push"])
        self.assertEqual(pull_request["required_approving_review_count"], 0)
        status = rules["required_status_checks"]["parameters"]
        self.assertTrue(status["strict_required_status_checks_policy"])
        self.assertEqual(
            status["required_status_checks"], [{"context": "verify / verify"}]
        )

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json") as plan_file:
            json.dump(plan, plan_file)
            plan_file.flush()
            rollback_result = subprocess.run(
                [
                    sys.executable,
                    str(RULESET_RENDERER),
                    "rollback",
                    "--plan",
                    plan_file.name,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(rollback_result.returncode, 0, rollback_result.stderr)
        rollback = json.loads(rollback_result.stdout)
        self.assertEqual(rollback["operation"], "disable_created_ruleset")
        self.assertEqual(rollback["payload"]["enforcement"], "disabled")
        self.assertEqual(rollback["payload"]["conditions"], plan["payload"]["conditions"])
        self.assertEqual(rollback["payload"]["rules"], plan["payload"]["rules"])


if __name__ == "__main__":
    unittest.main()
