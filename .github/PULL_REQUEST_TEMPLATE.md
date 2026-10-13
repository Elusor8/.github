## Summary

<!-- What changes and why. Link the Linear ticket. -->

## Evidence test (required when this PR touches a trust-boundary path)

<!-- Trust-boundary categories: signing/verify, key custody, relay inbound gate,
MCP tool allowlist, auth, egress/SSRF. The evidence-test check fails when a trust-boundary path changes
and the line below does not name a test file that this PR adds or updates. Labels do not change this.
Each repository lists its trust-boundary paths and test globs in .github/evidence-test.json. -->

Trust-boundary category touched: <signing/verify | key custody | relay inbound gate | MCP tool allowlist | auth | egress/SSRF | none>
Evidence test added or updated: <path/to/test file>::<test name>
What it proves: <one line: the failure it would catch>
If no test changes: <reason>; the check stays red and a human reviewer must confirm the reason before merge.
