"""evidence-test: a PR that touches a trust-boundary path must name a changed evidence test.

Keyed on paths, not labels, and fails closed: any error, a missing or invalid table, or a file
list that may be truncated is a failure. The check proves a test was named and changed, not that
it is adequate; human review stays the backstop.

Each target repository keeps its own table at TABLE_PATH. It is read through the API at the
pull request's BASE commit, never the head, so a PR cannot change the table it is judged against.
The table file is itself always a trust-boundary path.

Renames, removals and test files:
1. A renamed or copied file without its previous name fails closed. With the previous name, both
   the source and the destination path are judged against the trust globs.
2. Removing the table, or removing or renaming away a workflow, is a trust change. Other removed
   files are not judged.
3. A path that matches a test glob is not a trust match, except the table path and workflow paths,
   which are never subtracted.

Run by .github/workflows/evidence-test.yml with only the Python standard library. It never checks
out pull-request code.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

TABLE_PATH = ".github/evidence-test.json"
TABLE_CATEGORY = "evidence-test table"
# Workflow paths are judged by the table like any other path, but removing one is always a trust change.
WORKFLOW_GLOBS = [".github/workflows/**"]
WORKFLOW_CATEGORY = "workflow removed"
# GitHub's file statuses that carry a source path in previous_filename.
MOVED = ("renamed", "copied")
CATEGORIES = (
    "signing/verify",
    "key custody",
    "relay inbound gate",
    "MCP tool allowlist",
    "auth",
    "egress/SSRF",
)
EVIDENCE_LINE = re.compile(r"^[ \t>*-]*Evidence test added or updated:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
# GitHub's pull-request files endpoint returns at most 3000 files; at that count the list may be cut short.
MAX_FILES = 3000
PER_PAGE = 100


class TableError(ValueError):
    """The repository's evidence-test table is missing or invalid."""


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """`**/` spans zero or more directories, `**` anything, `*` and `?` stay inside one segment, `{a,b}` alternates."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "{":
            end = pattern.find("}", i)
            if end < 0:
                raise TableError(f"unclosed brace in glob {pattern!r}")
            options = pattern[i + 1:end].split(",")
            out.append("(?:" + "|".join(re.escape(o) for o in options) + ")")
            i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, globs: list[str]) -> bool:
    return any(glob_to_regex(g).match(path) for g in globs)


def _globs(value, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(g, str) and g and not g.startswith("/") for g in value):
        raise TableError(f"{where} must be a list of relative glob strings")
    for g in value:
        glob_to_regex(g)
    return value


def parse_table(text: str | None) -> dict:
    """Validate a repository table. Every category must be present (an empty list means not applicable)."""
    if text is None:
        raise TableError(f"{TABLE_PATH} is missing on the base branch")
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as error:
        raise TableError(f"{TABLE_PATH} is not valid JSON ({error})") from None
    if not isinstance(data, dict) or data.get("version") != 1:
        raise TableError(f"{TABLE_PATH} must be an object with \"version\": 1")
    boundary = data.get("trust_boundary")
    if not isinstance(boundary, dict) or set(boundary) != set(CATEGORIES):
        raise TableError(f"{TABLE_PATH} trust_boundary must list exactly these categories: {', '.join(CATEGORIES)}")
    table = {"trust_boundary": {c: _globs(boundary[c], f"trust_boundary[{c!r}]") for c in CATEGORIES},
             "test_globs": _globs(data.get("test_globs"), "test_globs")}
    exempt = data.get("test_leg_exempt")
    if exempt is not None and (not isinstance(exempt, str) or not exempt.strip()):
        raise TableError("test_leg_exempt, when present, must be a non-empty reason")
    if not table["test_globs"] and not exempt:
        raise TableError("test_globs is empty and no test_leg_exempt reason is given")
    table["test_leg_exempt"] = exempt
    return table


@dataclass
class ChangedFile:
    filename: str
    status: str
    previous_filename: str | None = None


@dataclass
class Result:
    ok: bool
    messages: list[str] = field(default_factory=list)


def evidence_paths(body: str | None) -> list[str]:
    """Paths named on `Evidence test added or updated:` lines. A `::test name` suffix and backticks are dropped."""
    if not isinstance(body, str):
        return []
    paths = []
    for value in EVIDENCE_LINE.findall(body.replace("\r\n", "\n")):
        value = value.split("::", 1)[0].strip().strip("`").strip()
        if value.startswith("./"):
            value = value[2:]
        if value and not value.startswith("<"):
            paths.append(value)
    return paths


def is_protected(path: str) -> bool:
    """The table path and workflow paths: never subtracted as tests, and their removal is a trust change."""
    return path == TABLE_PATH or matches(path, WORKFLOW_GLOBS)


def decide(table: dict, body: str | None, files: list[ChangedFile]) -> Result:
    unsourced = [f.filename for f in files if f.status in MOVED and not f.previous_filename]
    if unsourced:
        return Result(False, [f"evidence-test: renamed or copied file without a previous name ({', '.join(sorted(unsourced))}); "
                              "the source path cannot be judged, failing closed."])

    present = [f for f in files if f.status != "removed"]
    rules = {TABLE_CATEGORY: [TABLE_PATH], **table["trust_boundary"]}
    touched: dict[str, list[str]] = {}
    for f in files:
        if f.status == "removed":
            # Other removals are not judged; removing the table or a workflow is.
            candidates = [f.filename] if is_protected(f.filename) else []
            gone = f.filename
        else:
            candidates = [f.filename] + ([f.previous_filename] if f.status in MOVED else [])
            gone = f.previous_filename if f.status == "renamed" else None
        # A test-glob match is not a trust match, unless the path is protected.
        candidates = [p for p in candidates if is_protected(p) or not matches(p, table["test_globs"])]
        for category, globs in rules.items():
            if any(matches(p, globs) for p in candidates):
                touched.setdefault(category, []).append(f.filename)
        if gone and matches(gone, WORKFLOW_GLOBS):
            touched.setdefault(WORKFLOW_CATEGORY, []).append(gone)

    if not touched:
        return Result(True, ["evidence-test: no trust-boundary path changed."])

    summary = "; ".join(f"{c}: {', '.join(sorted(set(p)))}" for c, p in touched.items())
    named = evidence_paths(body)

    if table.get("test_leg_exempt"):
        if named:
            return Result(True, [f"evidence-test: trust-boundary change ({summary}). Evidence line present: {', '.join(named)}.",
                                 f"A human reviewer must confirm the evidence before merge ({table['test_leg_exempt']})."])
        return Result(False, [f"evidence-test: PR touches {summary}, but has no `Evidence test added or updated:` line."])

    changed = {f.filename for f in present}
    valid = [p for p in named if p in changed and matches(p, table["test_globs"])]
    if valid:
        return Result(True, [f"evidence-test: trust-boundary change ({summary}). Named evidence test changed in this PR: {', '.join(valid)}."])

    reason = ("no `Evidence test added or updated:` line" if not named
              else f"the named path ({', '.join(named)}) is not a test file changed in this PR")
    return Result(False, [f"evidence-test: PR touches {summary}, but names no changed evidence test: {reason}.",
                          f"Test files must match: {', '.join(table['test_globs'])}."])


def _get(url: str, token: str):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_table(api: str, repository: str, base_sha: str, token: str) -> str | None:
    """The table's text at the base commit, or None when the file does not exist there."""
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha or ""):
        raise TableError("pull request base is not a full commit SHA")
    url = f"{api}/repos/{repository}/contents/{urllib.parse.quote(TABLE_PATH)}?ref={base_sha}"
    try:
        item = _get(url, token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if not isinstance(item, dict) or item.get("type") != "file" or item.get("encoding") != "base64":
        raise TableError(f"{TABLE_PATH} on the base branch is not a regular file")
    return base64.b64decode(item["content"]).decode("utf-8")


def fetch(api: str, repository: str, number: int, token: str) -> tuple[dict, list[ChangedFile]]:
    pull = _get(f"{api}/repos/{repository}/pulls/{number}", token)
    files: list[ChangedFile] = []
    page = 1
    while True:
        batch = _get(f"{api}/repos/{repository}/pulls/{number}/files?per_page={PER_PAGE}&page={page}", token)
        files += [ChangedFile(f["filename"], f["status"], f.get("previous_filename")) for f in batch]
        if len(batch) < PER_PAGE:
            break
        page += 1
    if len(files) >= MAX_FILES or len(files) < pull.get("changed_files", 0):
        raise RuntimeError(f"changed-file list may be incomplete ({len(files)} of {pull.get('changed_files')}); failing closed")
    return pull, files


def main() -> int:
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        number = int(event["pull_request"]["number"])
        api, token = os.environ.get("GITHUB_API_URL", "https://api.github.com"), os.environ["GH_TOKEN"]
        pull, files = fetch(api, repository, number, token)
        table = parse_table(fetch_table(api, repository, pull["base"]["sha"], token))
        result = decide(table, pull.get("body"), files)
    except TableError as error:
        result = Result(False, [f"evidence-test: {error}; failing closed."])
    except Exception as error:  # fail closed on anything unexpected
        result = Result(False, [f"evidence-test: could not complete the check ({type(error).__name__}: {error}); failing closed."])

    for line in result.messages:
        print(line if result.ok else f"::error::{line}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(result.messages) + "\n")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
