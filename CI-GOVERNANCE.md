# Elusor CI governance

## Phase 1: prove the gate

Pilot repository: `zipviz-mcp`.

1. Every pull request runs tests and a build.
2. CI receives read-only repository access and no inherited secrets.
3. Auto-merge stays off.
4. No GitHub protection is activated until a deliberately failing pilot PR proves the check works.
5. After the pilot, add repositories one at a time from `governance/repositories.json`.

## Merge rules

- No direct changes to a protected default branch.
- A failing required check never merges normally.
- The builder does not approve its own work.
- Stop after two failed build/review loops and fix the spec, implementation, or test deliberately.
- Authentication, permissions, secrets, production deployment, database migration, customer-data, billing, legal, patent, domain, or irreversible changes follow their existing authority gate. CI does not grant authority.
- Emergency bypass, if later enabled, must be explicit, logged, reversible, and followed by a corrective pull request. There is no invisible bypass.

## Auto-merge

Deferred. It is considered only after the pilot has blocked a broken pull request and the team has observed enough successful changes to define a narrow low-risk class.

Test changes and dependency upgrades are not automatically low risk: either can make a green check misleading.

## Rollout evidence required per repository

- normal pull request passes;
- deliberately broken pull request fails;
- direct default-branch update is refused after protection is activated;
- no production secret is available to CI;
- rollback or ruleset-disable procedure is recorded.
