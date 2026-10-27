from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "evidence-test.yml"
TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"

_spec = importlib.util.spec_from_file_location("evidence_check", ROOT / "evidence-test" / "check.py")
check = importlib.util.module_from_spec(_spec)
sys.modules["evidence_check"] = check
_spec.loader.exec_module(check)

F = check.ChangedFile
BASE = "b" * 40
REPO = "example-org/sample-service"

# A synthetic table. The paths are invented and describe no real repository.
SAMPLE = {
    "version": 1,
    "trust_boundary": {
        "signing/verify": ["pkg/seal/**", "pkg/core.go"],
        "key custody": ["pkg/vault/*.go"],
        "relay inbound gate": ["pkg/core.go", "pkg/gate/{inbound,limits}.go"],
        "MCP tool allowlist": [],
        "auth": ["pkg/core.go", "pkg/login/**"],
        "egress/SSRF": ["pkg/outbound/client.go"],
    },
    "test_globs": ["spec/**/*_spec.{go,rb}"],
}
TABLE = check.parse_table(json.dumps(SAMPLE))
EXEMPT = check.parse_table(json.dumps({**SAMPLE, "test_globs": [], "test_leg_exempt": "no test suite yet"}))


def body(path: str) -> str:
    return f"## Summary\nSynthetic.\n\nEvidence test added or updated: {path}\n"


class GlobMatcherTests(unittest.TestCase):
    def test_single_star_stays_inside_one_segment(self) -> None:
        self.assertTrue(check.matches("pkg/vault/keys.go", ["pkg/vault/*.go"]))
        self.assertFalse(check.matches("pkg/vault/deep/keys.go", ["pkg/vault/*.go"]))

    def test_double_star_spans_directories_and_zero_directories(self) -> None:
        self.assertTrue(check.matches("pkg/seal/a/b/c.go", ["pkg/seal/**"]))
        self.assertTrue(check.matches("x_spec.go", ["**/*_spec.go"]))
        self.assertTrue(check.matches("spec/a/b/x_spec.rb", ["spec/**/*_spec.{go,rb}"]))
        self.assertFalse(check.matches("spec/a/x_spec.py", ["spec/**/*_spec.{go,rb}"]))

    def test_braces_alternate_whole_options(self) -> None:
        self.assertTrue(check.matches("pkg/gate/limits.go", ["pkg/gate/{inbound,limits}.go"]))
        self.assertFalse(check.matches("pkg/gate/limitsx.go", ["pkg/gate/{inbound,limits}.go"]))

    def test_match_is_anchored_and_literal(self) -> None:
        self.assertFalse(check.matches("x/pkg/core.go", ["pkg/core.go"]))
        self.assertFalse(check.matches("pkg/core.gox", ["pkg/core.go"]))
        self.assertFalse(check.matches("pkgXcore.go", ["pkg/core.go"]))


class TableTests(unittest.TestCase):
    def test_missing_table_fails_closed(self) -> None:
        with self.assertRaisesRegex(check.TableError, "missing on the base branch"):
            check.parse_table(None)

    def test_unparsable_or_invalid_tables_fail_closed(self) -> None:
        bad = [
            "{not json",
            "[]",
            json.dumps({**SAMPLE, "version": 2}),
            json.dumps({k: v for k, v in SAMPLE.items() if k != "trust_boundary"}),
            json.dumps({**SAMPLE, "trust_boundary": {k: v for k, v in SAMPLE["trust_boundary"].items() if k != "auth"}}),
            json.dumps({**SAMPLE, "trust_boundary": {**SAMPLE["trust_boundary"], "extra": []}}),
            json.dumps({**SAMPLE, "trust_boundary": {**SAMPLE["trust_boundary"], "auth": "pkg/login/**"}}),
            json.dumps({**SAMPLE, "trust_boundary": {**SAMPLE["trust_boundary"], "auth": ["/abs/path"]}}),
            json.dumps({**SAMPLE, "trust_boundary": {**SAMPLE["trust_boundary"], "auth": ["pkg/{a,b.go"]}}),
            json.dumps({**SAMPLE, "test_globs": []}),
            json.dumps({**SAMPLE, "test_globs": [], "test_leg_exempt": "  "}),
            json.dumps({**SAMPLE, "test_globs": "spec/**"}),
        ]
        for text in bad:
            with self.subTest(text=text[:60]):
                with self.assertRaises(check.TableError):
                    check.parse_table(text)


class DecisionTests(unittest.TestCase):
    def test_no_trust_boundary_change_passes_without_a_line(self) -> None:
        self.assertTrue(check.decide(TABLE, "", [F("docs/readme.md", "modified")]).ok)

    def test_trust_change_with_changed_test_passes(self) -> None:
        files = [F("pkg/outbound/client.go", "modified"), F("spec/outbound/client_spec.go", "modified")]
        result = check.decide(TABLE, body("spec/outbound/client_spec.go::blocks private ranges"), files)
        self.assertTrue(result.ok, result.messages)

    def test_labels_play_no_part(self) -> None:
        # decide() has no label input at all: an unlabelled trust change fails exactly like a labelled one.
        self.assertNotIn("label", " ".join(check.decide.__code__.co_varnames))
        self.assertFalse(check.decide(TABLE, "No evidence here.", [F("pkg/core.go", "modified")]).ok)

    def test_missing_line_fails_and_names_category_and_paths(self) -> None:
        result = check.decide(TABLE, "Synthetic body.", [F("pkg/gate/inbound.go", "modified")])
        self.assertFalse(result.ok)
        text = " ".join(result.messages)
        self.assertIn("relay inbound gate", text)
        self.assertIn("pkg/gate/inbound.go", text)
        self.assertIn("no `Evidence test added or updated:` line", text)

    def test_line_naming_a_non_test_file_fails(self) -> None:
        files = [F("pkg/vault/keys.go", "modified"), F("docs/readme.md", "modified")]
        result = check.decide(TABLE, body("docs/readme.md"), files)
        self.assertFalse(result.ok)
        self.assertIn("is not a test file changed in this PR", " ".join(result.messages))

    def test_line_naming_an_unchanged_test_fails(self) -> None:
        self.assertFalse(check.decide(TABLE, body("spec/vault/keys_spec.go"), [F("pkg/vault/keys.go", "modified")]).ok)

    def test_fixture_under_the_test_tree_is_not_a_test_file(self) -> None:
        files = [F("pkg/seal/x.go", "modified"), F("spec/fixtures/x.json", "modified")]
        self.assertFalse(check.decide(TABLE, body("spec/fixtures/x.json"), files).ok)

    def test_deleted_test_does_not_count_as_evidence(self) -> None:
        files = [F("pkg/login/session.go", "modified"), F("spec/login_spec.go", "removed")]
        self.assertFalse(check.decide(TABLE, body("spec/login_spec.go"), files).ok)

    def test_deleted_trust_file_alone_is_excluded(self) -> None:
        self.assertTrue(check.decide(TABLE, "", [F("pkg/login/session.go", "removed")]).ok)

    def test_rename_out_of_a_trust_path_still_counts(self) -> None:
        result = check.decide(TABLE, "", [F("pkg/old_session.go", "renamed", previous_filename="pkg/login/session.go")])
        self.assertFalse(result.ok)
        self.assertIn("auth", " ".join(result.messages))

    def test_rename_into_a_trust_path_counts(self) -> None:
        self.assertFalse(check.decide(TABLE, "", [F("pkg/outbound/client.go", "renamed", previous_filename="pkg/tmp.go")]).ok)

    def test_renamed_test_file_is_valid_evidence_under_its_new_name(self) -> None:
        files = [F("pkg/core.go", "modified"), F("spec/core_new_spec.go", "renamed", previous_filename="spec/core_spec.go")]
        self.assertTrue(check.decide(TABLE, body("spec/core_new_spec.go"), files).ok)

    def test_the_table_file_is_always_a_trust_boundary_path(self) -> None:
        empty = check.parse_table(json.dumps({**SAMPLE, "trust_boundary": {c: [] for c in check.CATEGORIES}}))
        result = check.decide(empty, "", [F(check.TABLE_PATH, "modified")])
        self.assertFalse(result.ok)
        self.assertIn(check.TABLE_CATEGORY, " ".join(result.messages))
        files = [F(check.TABLE_PATH, "modified"), F("spec/table_spec.go", "added")]
        self.assertTrue(check.decide(empty, body("spec/table_spec.go"), files).ok)

    def test_template_placeholder_is_not_evidence(self) -> None:
        template = TEMPLATE.read_text()
        self.assertFalse(check.decide(TABLE, template, [F("pkg/core.go", "modified")]).ok)
        self.assertEqual(check.evidence_paths(template), [])

    def test_malformed_bodies_fail_closed(self) -> None:
        files = [F("pkg/core.go", "modified"), F("spec/core_spec.go", "added")]
        for bad in (None, "", 42, "Evidence test added or updated:", "Evidence test added or updated:   \n",
                    "evidence test added or updated: spec/core_spec.go"):
            with self.subTest(body=bad):
                self.assertFalse(check.decide(TABLE, bad, files).ok)

    def test_backticks_dot_slash_crlf_and_list_markers_are_tolerated(self) -> None:
        files = [F("pkg/core.go", "modified"), F("spec/core_spec.go", "modified")]
        text = "Intro\r\n- Evidence test added or updated: `./spec/core_spec.go`::rejects oversize bodies\r\n"
        self.assertTrue(check.decide(TABLE, text, files).ok)

    def test_any_one_valid_line_is_enough(self) -> None:
        files = [F("pkg/vault/keys.go", "modified"), F("spec/vault_spec.go", "modified")]
        text = body("docs/readme.md") + "Evidence test added or updated: spec/vault_spec.go\n"
        self.assertTrue(check.decide(TABLE, text, files).ok)

    def test_one_file_reports_every_matching_category(self) -> None:
        text = " ".join(check.decide(TABLE, "", [F("pkg/core.go", "modified")]).messages)
        for category in ("signing/verify", "relay inbound gate", "auth"):
            self.assertIn(category, text)

    def test_exempt_table_needs_the_line_but_not_a_test_file(self) -> None:
        files = [F("pkg/login/session.go", "modified")]
        self.assertFalse(check.decide(EXEMPT, "", files).ok)
        result = check.decide(EXEMPT, body("manual check in a preview"), files)
        self.assertTrue(result.ok)
        self.assertIn("human reviewer must confirm", " ".join(result.messages))


class FetchAndMainTests(unittest.TestCase):
    def _fake_get(self, pull: dict, pages: list[list[dict]], table: str | None):
        def fake(url: str, token: str):
            self.assertEqual(token, "synthetic-token")
            if "/contents/" in url:
                self.assertTrue(url.endswith(f"?ref={pull['base']['sha']}"), "table must be read at the base sha")
                self.assertNotIn(pull["head"]["sha"], url)
                if table is None:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
                return {"type": "file", "encoding": "base64", "content": base64.b64encode(table.encode()).decode()}
            if url.endswith(f"/pulls/{pull['number']}"):
                return pull
            page = int(re.search(r"page=(\d+)$", url).group(1))
            return pages[page - 1] if page <= len(pages) else []
        return fake

    def _pull(self, body_text: str = "", changed: int = 1) -> dict:
        return {"number": 7, "body": body_text, "changed_files": changed, "base": {"sha": BASE}, "head": {"sha": "h" * 40}}

    def _run(self, pull: dict, pages: list[list[dict]], table: str | None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp) / "event.json"
            # The event payload's base differs on purpose: the script must use the API's current base.
            event.write_text(json.dumps({"pull_request": {"number": 7, "base": {"sha": "e" * 40}}}))
            summary = Path(tmp) / "summary.md"
            env = {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_PATH": str(event),
                   "GH_TOKEN": "synthetic-token", "GITHUB_STEP_SUMMARY": str(summary)}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(check, "_get", self._fake_get(pull, pages, table)), \
                 mock.patch("builtins.print") as printed:
                code = check.main()
            output = " ".join(str(c.args[0]) for c in printed.call_args_list) + summary.read_text()
        return code, output

    def test_files_are_paginated(self) -> None:
        page1 = [{"filename": f"docs/{i}.md", "status": "modified"} for i in range(100)]
        page2 = [{"filename": "pkg/core.go", "status": "modified"}]
        with mock.patch.object(check, "_get", self._fake_get(self._pull(changed=101), [page1, page2], None)):
            _, files = check.fetch("https://api.invalid", REPO, 7, "synthetic-token")
        self.assertEqual(len(files), 101)
        self.assertEqual(files[-1].filename, "pkg/core.go")

    def test_short_file_list_fails_closed(self) -> None:
        with mock.patch.object(check, "_get", self._fake_get(self._pull(changed=5), [[{"filename": "a", "status": "added"}]], None)):
            with self.assertRaises(RuntimeError):
                check.fetch("https://api.invalid", REPO, 7, "synthetic-token")

    def test_main_reads_the_table_at_the_base_and_passes_a_clean_pr(self) -> None:
        code, output = self._run(self._pull(), [[{"filename": "docs/readme.md", "status": "modified"}]], json.dumps(SAMPLE))
        self.assertEqual(code, 0, output)

    def test_main_fails_closed_when_the_table_is_missing_on_the_base(self) -> None:
        code, output = self._run(self._pull(), [[{"filename": "docs/readme.md", "status": "modified"}]], None)
        self.assertEqual(code, 1)
        self.assertIn("missing on the base branch", output)

    def test_main_fails_closed_on_an_unparsable_table(self) -> None:
        code, output = self._run(self._pull(), [[{"filename": "docs/readme.md", "status": "modified"}]], "{oops")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", output)

    def test_main_fails_closed_on_a_non_sha_base(self) -> None:
        pull = {**self._pull(), "base": {"sha": "main"}}
        code, output = self._run(pull, [[{"filename": "docs/readme.md", "status": "modified"}]], json.dumps(SAMPLE))
        self.assertEqual(code, 1)
        self.assertIn("failing closed", output)

    def test_main_fails_closed_on_any_error_and_never_prints_the_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp) / "event.json"
            event.write_text(json.dumps({"pull_request": {"number": 7}}))
            summary = Path(tmp) / "summary.md"
            env = {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_PATH": str(event),
                   "GH_TOKEN": "synthetic-token", "GITHUB_STEP_SUMMARY": str(summary)}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(check, "_get", side_effect=OSError("network down")), \
                 mock.patch("builtins.print") as printed:
                self.assertEqual(check.main(), 1)
            output = " ".join(str(c.args[0]) for c in printed.call_args_list) + summary.read_text()
            self.assertIn("failing closed", output)
            self.assertNotIn("synthetic-token", output)

    def test_main_fails_a_trust_change_without_evidence(self) -> None:
        code, output = self._run(self._pull(), [[{"filename": "pkg/vault/keys.go", "status": "modified"}]], json.dumps(SAMPLE))
        self.assertEqual(code, 1)
        self.assertIn("key custody", output)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text()

    def test_pull_request_only_never_pull_request_target(self) -> None:
        self.assertIn("pull_request:\n    types: [opened, synchronize, reopened, edited, labeled, unlabeled]", self.text)
        self.assertNotIn("pull_request_target", self.text)
        self.assertNotIn("workflow_dispatch", self.text)

    def test_read_only_permissions_and_no_secrets(self) -> None:
        self.assertEqual(self.text.count("permissions:"), 1)
        self.assertIn("permissions:\n  contents: read\n  pull-requests: read\n", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s+[A-Za-z-]+:\s*write\s*$")
        self.assertNotIn("${{ secrets.", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_checkout_is_the_central_repository_never_pr_code(self) -> None:
        self.assertEqual(self.text.count("uses: actions/checkout@"), 1)
        self.assertIn("repository: Elusor8/.github", self.text)
        self.assertIn("ref: ${{ github.workflow_sha }}", self.text)
        self.assertIn("sparse-checkout: evidence-test", self.text)
        self.assertNotIn("github.event.pull_request.head", self.text)
        self.assertNotIn("github.head_ref", self.text)

    def test_copy_guard_runs_before_checkout(self) -> None:
        guard = self.text.index("Elusor8/.github/.github/workflows/evidence-test.yml@*")
        self.assertLess(guard, self.text.index("uses: actions/checkout@"))

    def test_every_action_is_immutably_pinned(self) -> None:
        uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)\s*$", self.text, flags=re.MULTILINE)
        self.assertEqual(self.text.count("uses:"), len(uses))
        for action in uses:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_no_conditional_skip_and_bounded(self) -> None:
        self.assertNotIn("\n        if:", self.text)
        self.assertNotIn("continue-on-error", self.text)
        self.assertIn("timeout-minutes:", self.text)
        self.assertIn("cancel-in-progress:", self.text)

    def test_untrusted_pr_text_never_reaches_the_shell(self) -> None:
        self.assertNotIn("github.event.pull_request.body", self.text)
        self.assertNotIn("github.event.pull_request.title", self.text)


class TemplateTests(unittest.TestCase):
    def test_template_carries_the_approved_lines(self) -> None:
        text = TEMPLATE.read_text()
        for line in ("Trust-boundary category touched:", "Evidence test added or updated:", "What it proves:", "If no test changes:"):
            self.assertIn(line, text)
        self.assertIn("human reviewer must confirm", text)
        self.assertIn(check.TABLE_PATH, text)
        self.assertNotIn("—", text)


if __name__ == "__main__":
    unittest.main()
