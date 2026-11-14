# GitHub ruleset operator plan

**Prepared for separate owner authorization.** No GitHub settings were changed by G01. The proposed rule targets the exact ref `refs/heads/main` of the public personal repository `moaltamimi-unbiasedtalent/PrufAI`. GitHub's public branch-ruleset feature is available on Free. Billing tier was unverified. Enterprise evaluation and organization teams are outside this proposal. [Ruleset features and enforcement modes](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)

## Configuration to review

| Setting | Proposed value |
| --- | --- |
| Ruleset name and target | `main-independent-review`, branch type, only `refs/heads/main` |
| Bypass list | Empty, including admins, integrations, deploy keys and automation |
| Pull request and approvals | Required PR, one approving review, stale approval dismissal, approval of the most recent reviewable push by a different account, resolved review conversations |
| Required checks | All five contexts below, GitHub Actions as expected source and branch up to date with the base |
| Ref protection | Restrict deletions and block force pushes. The required-PR rule blocks ordinary direct updates without a qualifying PR. Leave blanket **Restrict updates** off, since it only permits bypass actors and would defeat normal merges with an empty list. |
| Code owners / security review | Leave CODEOWNERS approval off until real maintainers are designated. Apply the additional qualified security-review requirement through the documented human process. |
| Activation | Owner reviews a disabled configuration first if saving a preview is authorized. Apply Active only after separate authorization and reviewer readiness. |

Require merges through GitHub's PR UI or API as an operator rule. GitHub documents an edge case in which a manually pushed merge that exactly matches its own qualifying PR merge may pass required-review protection. The owner also retains the ability to edit settings. Capture configuration changes as governance events. An empty runtime bypass list does not remove the owner's settings authority. [Branch rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) · [Ruleset fields](https://docs.github.com/en/rest/repos/rules)

## Required check contexts

These are the actual Checks API names, without a workflow prefix. GitHub reported **GitHub Actions** (`github-actions`, app/integration ID **15368**) for each check. No same-name commit status was present in the exact-main check snapshot. Both workflows trigger for pull requests and pushes to `main`. The workflow definitions use these distinct names.

| Workflow | Exact required context | Execution evidence |
| --- | --- | --- |
| CI | `python` | Required PostgreSQL, guarded MinIO and Redis service suites, Alembic verification, dedicated required-suite JUnit skip checks, lint, types and API/AI tests |
| CI | `web` | Production npm audit, formatting, lint, types, tests and production build |
| CI | `browser` | Real PostgreSQL/Redis/guarded MinIO, authenticated journeys and the public credential failure-log check |
| Supply-chain evidence | `Language SBOMs and audits (informational findings)` | Language inventory, audits, provenance and SBOM archive |
| Supply-chain evidence | `Container and MinIO binary scans (informational findings)` | Scans, binary and image provenance, guarded storage and operations restore, E06A release rehearsal and artifact production |

The JUnit assertions fail on unexpected required skips in their covered suites. The general test pass can contain service-dependent skips, and some dedicated service invocations use separate flags or log evidence beyond the 20 JUnit reports. The workflows ran real services and have no job-level skip condition on the five required contexts. GitHub can treat a neutral or skipped job as satisfying a required-check rule, and a ruleset cannot inspect the pytest counts. Preserve the existing fail-closed test invocations and independently inspect job execution when any workflow or gate changes. Supply-chain findings are informational under existing policy. An archive passing establishes scan execution and integrity. It does not resolve the advisories. [Exact-main checks](https://api.github.com/repos/moaltamimi-unbiasedtalent/PrufAI/commits/dfbbfcecf76dd9c0e16cc2c2e043a73d76d0acb2/check-runs) · [GitHub status-check semantics](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)

## Apply and verify after authorization

1. Name the accountable repository owner and confirm a separate write-authorized reviewer is available. Review the risks of granting write access on a personal repository. Keep administrator, bypass and unrelated access out of the grant. Decide how to record review dismissal separately. The audited branch had no extra dismissal restrictions. GitHub's ruleset guide describes dismissal by write-authorized users, while its protected-branch REST reference lists administrators or authorized allowlist members. Confirm effective dismissal permissions after setup and keep dismissal subject to the owner procedure. [Personal repository access](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository) · [Ruleset review behavior](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-a-pull-request-before-merging) · [Dismiss review API](https://docs.github.com/en/rest/pulls/reviews#dismiss-a-review-for-a-pull-request)
2. Snapshot effective branch rules, classic protection, ruleset list/details, direct collaborators, bypass and contextual check sources in the operator record. In **Settings → Rules → Rulesets**, configure the proposed branch ruleset after the owner authorizes that action. Select the named GitHub Actions check source for each context. Keep the proposal inactive until the owner has approved activation. Validate that all five latest PR checks are visible with the expected source. [Creating rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
3. After activation, read the specific ruleset and effective `main` rules again. Verify exact target, active state, empty bypass list, approval count, freshness options, source-bound contexts, strict checks, deletion and non-fast-forward rules. Capture the rule ID, actor, date, before/after evidence and any rule-suite results.
4. Verify behavior on an authorized isolated repository if available. Scenarios below were evaluated as policy logic only during G01. On the live repository use read-only mergeability observations for authorized PRs. No force-push, deletion, prohibited direct write, bypass attempt or demo merge is needed on live `main`. Verify the reviewer record, exact SHA and normal mergeability separately before declaring enforcement verified. [Rules and rule suites API](https://docs.github.com/en/rest/repos/rule-suites)
5. If misconfigured, pause merges. Identify the faulty rule and preserve its settings and observations. The owner may correct that setting under separate authorization. If necessary, disable only the new ruleset, retain any prior layer, and keep the temporary merge freeze until the corrected rule is verified. Confirm original settings from the snapshot before ending configuration recovery. Keep E03A and release decisions separate.

## Policy scenarios assessed on paper

| Scenario | Expected with the proposed configuration |
| --- | --- |
| PR with zero eligible approvals | Block |
| PR author tries self-approval | Cannot supply required approval. [GitHub approval rules](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews) |
| Previously approved PR gets a new reviewable push | Block pending fresh approval by someone other than the last pusher |
| Python, Browser, or either Supply-chain job fails | Block |
| A required check is pending, missing, or submitted by another app | Block |
| A required job becomes skipped or neutral | GitHub may satisfy the named check. A human must check actual execution and keep the merge on hold. |
| Ordinary direct push with no qualifying PR, force push, or delete | Block |
| Actor without an applicable bypass tries to bypass | Block if the ruleset is active. Settings-editing authority is audited separately. |
| Eligible independent approval and all checks successful on the current, up-to-date PR with review discussions resolved | Eligible for a separately authorized manual merge |
