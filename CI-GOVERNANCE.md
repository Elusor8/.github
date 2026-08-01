# ZipViz GitHub-native delivery gate

## Purpose

Stop broken or unreviewed changes from reaching a deployable default branch without building a second CI product.

## Delivered design

```text
pull request
  -> GitHub Enterprise runs the centrally required workflow
  -> npm install, 226+ tests, and build must pass
  -> Einstein/Opus challenges the change in Buzz
  -> Devin supplies a second review when the change is risky or Einstein is uncertain
  -> the Enterprise ruleset permits or blocks merge
```

The pilot targets only `Elusor8/zipviz-mcp`.

## Source of truth

- Required workflow: `.github/workflows/zipviz-mcp-required.yml`
- Workflow source repository: `Elusor8/.github`
- Ruleset owner: `Elusor8` organization
- Target: the default branch of `zipviz-mcp`
- Secrets inherited by the workflow: none
- Workflow token permission: `contents: read`
- Checkout credentials removed before dependency scripts run: yes
- Third-party actions: pinned to immutable commit SHAs
- Auto-merge: off

No caller workflow is required in `zipviz-mcp`. GitHub Enterprise injects the required workflow from the central repository.

## Review separation

- A builder must not review its own change.
- Einstein is the persistent read-only Opus reviewer on Venger, with a separate signed Buzz identity.
- If Devin built a change, Einstein reviews it.
- If Einstein is unavailable or uncertain, Devin performs a fresh independent review.
- Copilot review is optional and is not part of the required gate.

The review must bind its verdict to the exact pull-request head SHA. A generic approval of a PR number is stale after a new push.

## Safe rollout

1. Commit and independently review the central workflow.
2. Create the organization ruleset in **Evaluate** mode for `zipviz-mcp` only.
3. Prove one passing PR and one deliberately failing PR.
4. Confirm the required workflow is sourced from the central immutable SHA.
5. Switch the pilot ruleset to **Active** only after the independent-review identity is accepted by GitHub.
6. Observe real work before adding another repository.

## Rollback

Set the single pilot ruleset's enforcement state to **Disabled**. Do not delete it: disabling preserves the configuration and audit history and can be reversed.

No repository files, branch history, deployments, domains, billing, or secrets are changed by that rollback.

## Evidence status

This document is updated only from live proof. Proposed or incomplete checks remain labelled as such.
