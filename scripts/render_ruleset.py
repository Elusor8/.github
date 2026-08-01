#!/usr/bin/env python3
"""Render additive GitHub organization ruleset plans without calling GitHub."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "governance" / "repositories.json"


def known_repositories() -> set[str]:
    data = json.loads(MANIFEST.read_text())
    return {repo["name"] for repo in data["repositories"]}


def build_plan(repo: str, check_context: str, enforcement: str) -> dict[str, Any]:
    if repo not in known_repositories():
        raise ValueError(f"unknown repository: {repo}")
    if not check_context.strip():
        raise ValueError("check context must not be empty")

    payload: dict[str, Any] = {
        "name": f"ZipViz CI - {repo}",
        "target": "branch",
        "enforcement": enforcement,
        "bypass_actors": [],
        "conditions": {
            "repository_name": {
                "include": [repo],
                "exclude": [],
                "protected": False,
            },
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
            {
                "type": "pull_request",
                "parameters": {
                    "allowed_merge_methods": ["squash"],
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": True,
                    "require_last_push_approval": True,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": True,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": True,
                    "required_status_checks": [{"context": check_context}],
                    "strict_required_status_checks_policy": True,
                },
            },
        ],
    }
    return {
        "operation": "create_org_ruleset",
        "endpoint": "/orgs/Elusor8/rulesets",
        "method": "POST",
        "preserves_existing_rulesets": True,
        "preserves_classic_branch_protection": True,
        "payload": payload,
    }


def build_rollback(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("operation") != "create_org_ruleset":
        raise ValueError("rollback input is not a create_org_ruleset plan")
    payload = copy.deepcopy(plan["payload"])
    payload["enforcement"] = "disabled"
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    return {
        "operation": "disable_created_ruleset",
        "method": "PUT",
        "ruleset_id": "SET_FROM_CREATE_RESPONSE",
        "source_plan_sha256": hashlib.sha256(canonical).hexdigest(),
        "payload": payload,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a GitHub ruleset plan. This tool never calls GitHub."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--check-context", required=True)
    plan.add_argument(
        "--enforcement", choices=("evaluate", "active"), default="evaluate"
    )

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--plan", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.action == "plan":
            output = build_plan(args.repo, args.check_context, args.enforcement)
        else:
            output = build_rollback(json.loads(args.plan.read_text()))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
