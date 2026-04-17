# br-closeout-audit v2

## 1. Purpose

`br-closeout-audit` v2 is a Rust enforcement tool that makes beads closeable
only when a machine-readable work contract and matching completion evidence are
both present.

Primary outcome:

1. Prevent incomplete beads from being closed.

Secondary outcomes:

1. Reduce "done" decisions that depend on agent interpretation.
2. Keep post-hoc audit coverage for bypass detection and trend reporting.
3. Replace the current Python implementation with a more reliable Rust binary.

This tool is not just an auditor. Its primary job is to enforce a deterministic
close contract before `br close` succeeds.

## 2. Product Principles

1. Deterministic before heuristic.
2. Fail closed on uncertainty.
3. Fixed invariants are more important than repo-local configurability.
4. `safe-close` is the supported close path; `audit` is the backstop.
5. Temporary compatibility shims are acceptable, indefinite dual-maintenance is
   not.

## 3. Language and Runtime Choice

## Decision: Rust

Rust is the best fit for v2.

1. Native fit with surrounding tooling (`br`, `bv`) and expected operator
   workflows.
2. Single compiled binary for local hooks, CI, and remote runners.
3. Strong typing for policy, contract, and JSON schema evolution.
4. Deterministic command execution and parser behavior.
5. Better long-term reliability than growing the current Python script into a
   large enforcement tool.

## 4. Current State (v1)

v1 (Python) currently provides post-hoc auditing over:

1. `br show --json`
2. `bv --robot-history`
3. `git log` / `git show` fallback evidence

v1 is useful, but it has two structural limits:

1. It primarily acts after closure, so it detects bad closeouts instead of
   preventing them.
2. It relies heavily on free-form Markdown interpretation, which leaves too
   much room for ambiguity.

v1 should be treated as a transitional compatibility baseline only. Once v2 is
shipped and adopted, v1 code should be removed rather than preserved as a
permanent fallback.

## 5. v2 Goals

1. Enforce a machine-readable bead contract for new work.
2. Block close attempts that do not satisfy hard completion constraints.
3. Require evidence that maps to declared acceptance criteria.
4. Minimize agent discretion and parser guesswork.
5. Preserve post-hoc auditing for historical review and bypass detection.
6. Provide a reliable Rust implementation with explicit packaging and install
   behavior.
7. Remove v1 Python production code after cutover.

## 6. Non-Goals

1. Replacing `br` issue storage or dependency semantics.
2. Running arbitrary build or test commands automatically by default.
3. Making every rule repo-configurable.
4. Keeping v1 and v2 in production indefinitely.

## 7. Enforcement Model

v2 should be designed around stage-specific enforcement rather than one generic
"audit" concept.

1. `lint-bead --stage create|update`
   Verifies that a bead has a valid machine-readable contract.
2. `preclose`
   Evaluates whether the bead is actually ready to close.
3. `safe-close`
   Runs `preclose`, then calls `br close` only if the gate passes.
4. `audit`
   Reviews already-closed beads, detects bypasses, and reports suspicious
   history.

Design rule:

1. `safe-close` is the supported close path.
2. If upstream `br` hook support exists, integrate with it.
3. If upstream hook support does not exist yet, team workflows must still use
   `safe-close`, and `audit` must flag direct `br close` bypasses as policy
   violations until hook support is available.

## 8. Machine-Readable Bead Contract

The core improvement in v2 is to stop depending on loosely structured issue
text as the source of truth.

For v2, every new bead should contain a machine-readable contract. Until `br`
supports first-class structured bead fields, the contract should live in the
issue description as a fenced YAML block.

Example:

```yaml
closeout_contract:
  schema_version: 1
  mode: migration
  scope_paths:
    - db/schema.sql
    - db/migrations/**
  acceptance_criteria:
    - id: AC-1
      text: Schema change is represented in tracked files.
    - id: AC-2
      text: Verification command succeeds.
  evidence_plan:
    - id: EV-1
      kind: files
      paths:
        - db/schema.sql
        - db/migrations/**
    - id: EV-2
      kind: command
      command_hint: bun test db
```

Required contract fields:

1. `schema_version`
2. `mode`
3. `scope_paths`
4. `acceptance_criteria`
5. `evidence_plan`

Required acceptance criteria shape:

1. Each criterion must have a stable ID such as `AC-1`.
2. IDs must be unique within the bead.
3. Criteria must be concrete enough to map to evidence.

## 9. Hard Invariants

These are the fixed rules that define accountability. Repo policy may tune
details, but it must not be able to disable these invariants.

## 9.1 Non-Configurable Invariants

1. A valid bead contract must be present for new beads.
2. `mode` must resolve to a canonical supported mode.
3. All blocker dependencies must already be closed.
4. Non-meta implementation evidence must exist.
5. All declared acceptance criteria must be covered by close evidence.
6. Mode-specific evidence requirements must be satisfied.
7. Structured close evidence must parse successfully.
8. Operational uncertainty blocks closure.

Examples of operational uncertainty:

1. `br` output cannot be parsed.
2. `bv --robot-history` returns an unsupported shape.
3. git queries fail.
4. policy loading or schema validation fails.
5. override journaling fails.

## 9.2 Repo-Tunable Inputs

Repo policy may tune inputs like:

1. Meta path globs
2. Mode aliases for legacy migration
3. Repo-specific required path sets
4. Repo-specific command or evidence token variants
5. Output verbosity

Repo policy must not be able to turn off the hard invariants in `9.1`.

## 10. Mode Classification

Mode classification must be explicit and deterministic.

1. `closeout_contract.mode` is authoritative for new beads.
2. Repo policy may define aliases that map to canonical modes.
3. Legacy fallback inference from labels/title/description is allowed only for
   existing beads without a contract.
4. Unknown or ambiguous modes are blockers, not warnings.

Canonical modes for v2:

1. `code`
2. `verification`
3. `migration`
4. `test`
5. `docs`

## 11. Compatibility Strategy

v2 should support a temporary migration path, but the migration rules must be
explicit.

1. New beads:
   Require the machine-readable contract.
2. Existing beads without a contract:
   Use a legacy compatibility adapter that derives a provisional contract from
   labels, headings, and backticked paths.
3. Compatibility adapter output:
   Must be clearly marked as legacy-derived in text and JSON output.
4. Compatibility window:
   Exists only to migrate existing work; it is not a permanent design state.

Compatibility requirements:

1. Bare `br-closeout-audit` invocation must continue to default to `audit`.
2. Existing v1 flags like `--issue`, `--since-rev`, and `--format json` should
   continue to work for the audit path.
3. Compatibility belongs in Rust, not in a long-lived Python wrapper.

## 12. CLI Surface

Binary name remains `br-closeout-audit`.

## 12.1 Subcommands

1. `audit`
2. `lint-bead`
3. `preclose`
4. `safe-close`
5. `policy-check`

## 12.2 Default Invocation

For compatibility with v1:

1. `br-closeout-audit` with no subcommand runs `audit`.
2. Current audit flags remain accepted on the default path.

## 12.3 Command Contracts

### `audit`

Post-hoc analysis over already-closed beads.

Examples:

```bash
br-closeout-audit
br-closeout-audit --issue content_engine-x9g.1
br-closeout-audit audit --since-rev HEAD~30 --format json
```

Exit codes:

1. `0`: no blocker findings
2. `1`: blocker findings present
3. `2`: operational/tool error

### `lint-bead`

Validates the bead contract at create/update time.

Examples:

```bash
br-closeout-audit lint-bead --issue content_engine-x9g.1 --stage create
br-closeout-audit lint-bead --issue content_engine-x9g.1 --stage update
```

Exit codes:

1. `0`: bead contract valid
2. `1`: contract invalid or missing required fields
3. `2`: operational/tool error

### `preclose`

Evaluates close readiness before `br close`.

Examples:

```bash
br-closeout-audit preclose --issue content_engine-x9g.1 --reason "Completed
AC: AC-1,AC-2
Command: bun test packages/foo --runInBand
Result: pass"

br-closeout-audit preclose --issue content_engine-x9g.1 --staged-only
```

Exit codes:

1. `0`: close allowed
2. `1`: close blocked by policy
3. `2`: operational/tool error

### `safe-close`

Runs `preclose`, then calls `br close` only if the gate passes.

Examples:

```bash
br-closeout-audit safe-close --issue content_engine-x9g.1 --reason "Completed
AC: AC-1,AC-2
Command: bun test packages/foo --runInBand
Result: pass"
```

Behavior:

1. Re-resolve policy and bead state immediately before close.
2. Run all preclose blockers.
3. If passed, call `br close`.
4. If `br close` fails, return operational error and do not report synthetic
   success.
5. Unsupported passthrough flags must error explicitly; they must not be
   silently dropped.

### `policy-check`

Validates merged policy and prints normalized effective policy.

Examples:

```bash
br-closeout-audit policy-check
br-closeout-audit policy-check --format json
```

## 13. Stage Contracts and Evidence Sources

Each stage should have a bounded evidence model.

| Stage | Reads | Must Not Rely On |
|---|---|---|
| `lint-bead` | bead contract, issue metadata | git history or inferred completion |
| `preclose` | bead contract, issue metadata, dependency state, staged diff, committed history, close reason | post-close milestones |
| `safe-close` | everything from `preclose` plus actual `br close` result | stale precomputed state |
| `audit` | closed issue metadata, history, git evidence | mutable working tree assumptions |

Stage rules:

1. `lint-bead` validates contract quality, not completion.
2. `preclose` validates readiness and evidence before closure.
3. `safe-close` is authoritative for local closure.
4. `audit` detects bypasses, regressions, and suspicious history after the
   fact.

## 14. Structured Close Evidence

Token presence alone is too weak for hard gating. v2 should parse named fields,
not just search for loose substrings.

Minimum close evidence for modes `verification`, `test`, and `migration`:

1. `AC:` line listing the covered acceptance criteria IDs
2. `Command:` line
3. `Result:` line
4. `Run-Id:` line when available

Example:

```text
Completed
AC: AC-1,AC-2
Command: bun test packages/foo --runInBand
Result: pass
Run-Id: local-2026-04-12T07:10:00Z
```

Rules:

1. Every declared acceptance criterion must be listed in `AC:` coverage unless
   policy explicitly allows partial close for that mode.
2. The parser must validate field names and values, not just token existence.
3. Empty, malformed, or duplicate fields are blockers.

## 15. Policy Model (v2)

Policy should become narrower and more explicit.

## 15.1 Precedence

Policy resolution order:

1. tool default config
2. repo-local override files
3. optional runtime `--policy` path

Merge rules:

1. maps deep-merge
2. arrays replace unless explicitly documented otherwise
3. hard invariants cannot be overridden

## 15.2 Schema Versioning

Required top-level fields:

1. `schema_version`
2. `policy_name`

Rules:

1. Unknown major schema versions are rejected.
2. Missing schema version may be adapted from v1 config only during the
   compatibility window.
3. Compatibility adaptation must emit a warning so repos migrate explicitly.

## 15.3 Proposed Effective Policy Shape

```json
{
  "schema_version": 2,
  "policy_name": "default-v2",
  "hard_invariants": {
    "require_contract": true,
    "require_closed_blockers": true,
    "require_non_meta_evidence": true,
    "require_acceptance_coverage": true,
    "fail_closed_on_operational_error": true
  },
  "legacy_compat": {
    "allow_derived_contract_for_existing_beads": true,
    "derived_contract_warn_only": false
  },
  "repo_tuning": {
    "default_since_rev": "HEAD~20",
    "default_limit": 10,
    "meta_globs": [".beads/**", ".ntm/**", "**/*.png"],
    "mode_aliases": {
      "verification": ["verification", "verify"],
      "migration": ["database", "db", "migration", "schema"],
      "test": ["testing", "test", "tests"],
      "docs": ["docs", "documentation"]
    },
    "mode_rules": {
      "migration": {
        "require_path_sets": [
          {"label": "schema", "globs": ["**/schema.prisma", "**/schema.sql"]},
          {"label": "migration", "globs": ["**/migrations/**"]}
        ]
      },
      "verification": {
        "require_structured_close_evidence": true
      },
      "test": {
        "require_structured_close_evidence": true,
        "require_any_globs": ["**/tests/**", "**/*.test.ts", "**/*.spec.ts"]
      },
      "docs": {}
    }
  },
  "outputs": {
    "include_remediation_hints": true,
    "max_details_per_finding": 8
  }
}
```

## 16. Output Contracts

## 16.1 Text Mode

Text output should always include:

1. short summary
2. per-issue status
3. blocker or warning lines
4. remediation steps
5. explicit marker when a result came from legacy-derived contract data

## 16.2 JSON Mode

Top-level JSON fields:

1. `output_version`
2. `repo`
3. `summary`
4. `results`
5. `policy`
6. `command`
7. `timestamp`

Each finding should include:

1. `severity`
2. `code`
3. `message`
4. `details`
5. `remediation`
6. `source_stage`
7. `legacy_derived`

## 17. Override Model

Overrides must be explicit, logged, and difficult enough to discourage casual
use.

## 17.1 Mechanics

1. Only `safe-close` supports `--force-closeout`.
2. `--force-reason` is required and must meet a minimum length.
3. Override attempts are rejected for operational/tool errors.
4. Override attempts are rejected when contract parsing fails.
5. Override attempts must print a visible warning banner in text and JSON
   output.

## 17.2 Override Journal

Do not write override records into `.beads/.br_history`.

Instead, write to a tool-owned path such as:

1. `.ntm/closeout-audit/override-log.jsonl`

Required fields:

1. timestamp
2. actor
3. issue_id
4. blocked_codes
5. force_reason
6. git_head_sha
7. policy_name

If override journaling fails, `safe-close` must fail closed.

## 18. Data Sources and Failure Modes

## 18.1 Upstream Commands

1. `br show --json <id>`
2. `bv --robot-history`
3. `git log` / `git show`
4. `git diff --cached --name-only`

## 18.2 Adapter Layer

Use Rust adapter traits to isolate command execution from policy logic.

1. `BrClient`
2. `BvClient`
3. `GitClient`

This enables deterministic unit tests and explicit failure handling.

## 18.3 Failure Rules

1. `preclose` and `safe-close` fail closed on parse, command, or schema errors.
2. `audit` returns operational error for command failures and never reports a
   false pass.
3. `safe-close` must re-fetch issue state before calling `br close`.
4. If `br close` reports failure after preclose passes, the wrapper returns
   exit `2` and does not hide the failure.

## 19. Packaging and Install

The Rust rewrite needs an explicit install story because this repo currently
installs tools by symlinking top-level executables.

Required packaging design:

1. Keep a top-level executable named `tools/br-closeout-audit/br-closeout-audit`
   so `install.sh` keeps working.
2. Replace the current Python implementation with either:
   - the Rust binary itself at that path, or
   - a thin non-Python launcher that execs a tool-owned compiled binary
3. Do not require the old Python runtime after cutover.
4. Document the local build command and output path clearly.

Migration note:

1. The plan must not assume that `install.sh` builds Rust artifacts.
2. Build and packaging steps must be explicit and testable.

## 20. Rollout Plan

The rollout should optimize for hard constraints first, not for a mechanical
"parity port" alone.

## Phase 0: Contract and invariant freeze

1. Freeze the bead contract schema.
2. Freeze the list of non-configurable invariants.
3. Freeze the JSON output contract and compatibility expectations.

## Phase 1: Rust core and compatibility baseline

1. Scaffold the Rust crate and packaging layout.
2. Implement policy loading, schema validation, and adapter traits.
3. Implement `audit` with v1-compatible invocation behavior.
4. Support temporary legacy contract derivation for old beads.

## Phase 2: Contract enforcement at create/update time

1. Ship `lint-bead`.
2. Require machine-readable contract for new beads.
3. Keep legacy derivation only for existing beads.

## Phase 3: Deterministic pre-close enforcement

1. Ship `preclose`.
2. Ship `safe-close`.
3. Enforce structured close evidence.
4. Enforce acceptance-criteria coverage.
5. Add strict override logging and fail-closed behavior.

## Phase 4: Adoption and hard enforcement

1. Make `safe-close` the default documented close path.
2. Integrate with upstream hooks if available.
3. Use `audit` to flag direct `br close` bypasses until hook support exists.
4. Tune repo-local policy only where genuinely needed.

## Phase 5: Remove v1 code

1. Delete the Python v1 implementation.
2. Delete v1-only docs and behavior notes.
3. Remove Python-specific runtime assumptions from the tool docs.
4. Keep backward-compatible audit invocation in Rust where still needed.

## 21. Rollout Exit Criteria

Soft-launch and hard-enforcement transitions should be measurable.

Before hard enforcement is the default:

1. false-positive rate is acceptably low in pilot repos
2. median `preclose` runtime is under the target threshold
3. override rate is low enough that policy is not being routinely bypassed
4. at least one real repo uses `safe-close` successfully as the standard path

Initial target:

1. median single-issue `preclose` runtime under 1 second on warm local state

## 22. Test Strategy

## 22.1 Unit Tests

1. contract parsing and validation
2. policy merge and schema validation
3. mode resolution
4. structured close evidence parsing
5. acceptance-criteria coverage checks
6. override validation
7. failure-on-uncertainty behavior

## 22.2 Integration Tests

Use fixture repos with scripted histories for:

1. good closeout path
2. meta-only closeout
3. missing dependency closeout
4. migration missing schema or migration path set
5. verification missing structured evidence
6. malformed contract
7. direct `br close` bypass later detected by `audit`
8. override journaling failure
9. `br close` failure after successful `preclose`

## 22.3 Compatibility Tests

1. bare `br-closeout-audit` still runs `audit`
2. `--issue`, `--since-rev`, and `--format json` still work on the audit path
3. v1 config without `schema_version` adapts during the migration window
4. legacy beads without contracts are marked as compatibility-derived

## 22.4 Golden Output Tests

1. text output snapshot
2. JSON output snapshot
3. remediation hint snapshot
4. legacy-compatibility output snapshot

## 23. Acceptance Test Matrix

| ID | Stage | Scenario | Expected Result |
|---|---|---|---|
| AT-01 | lint/create | New bead missing contract block | BLOCKER `missing_contract` |
| AT-02 | lint/create | Contract missing `mode` | BLOCKER `missing_required_field` |
| AT-03 | lint/create | Contract has duplicate AC IDs | BLOCKER `duplicate_acceptance_criterion_id` |
| AT-04 | lint/update | Valid contract for code bead | PASS |
| AT-05 | preclose | Open blocker dependencies exist | BLOCKER `open_blocker_dependencies` |
| AT-06 | preclose | Staged diff touches only meta paths | BLOCKER `staged_meta_only_close` |
| AT-07 | preclose | Verification reason lacks structured fields | BLOCKER `invalid_structured_close_evidence` |
| AT-08 | preclose | Acceptance criteria not fully covered | BLOCKER `acceptance_criteria_uncovered` |
| AT-09 | preclose | Migration bead missing migration path set | BLOCKER `missing_required_path_set_migration` |
| AT-10 | safe-close | Preclose blockers present | no `br close` call executed |
| AT-11 | safe-close | No blockers | `br close` executed successfully |
| AT-12 | safe-close | Override without valid reason | BLOCKER `force_override_without_reason` |
| AT-13 | safe-close | Override journal write fails | exit `2`, close blocked |
| AT-14 | safe-close | `br close` fails after pass | exit `2`, failure surfaced |
| AT-15 | audit | Closed bead with meta-only evidence | BLOCKER `meta_only_commits` |
| AT-16 | audit | Legacy bead without contract | result marked `legacy_derived=true` |
| AT-17 | policy-check | Invalid schema version | exit `2` with schema error |
| AT-18 | compatibility | Bare invocation runs audit | PASS |
| AT-19 | compatibility | v1 config adapts with warning | PASS with compatibility warning |
| AT-20 | performance | Single-issue preclose latency | under target threshold |

## 24. V1 Decommissioning Instructions

v1 removal should be an explicit part of the implementation plan, not a vague
"later cleanup."

Required actions after phase-4 adoption criteria are met:

1. Delete the current Python script at
   `tools/br-closeout-audit/br-closeout-audit`.
2. Replace it with the Rust binary or a thin non-Python launcher.
3. Remove v1-only parsing code that exists only to preserve the old Python
   implementation.
4. Remove v1-only documentation that treats heuristic Markdown parsing as the
   default design.
5. Keep only the compatibility behavior that still matters to operators, and
   implement that behavior in Rust.

Explicit anti-goal:

1. Do not leave the Python v1 code in-tree as a "just in case" runtime
   fallback.

## 25. Definition of Done

v2 is complete when all are true:

1. A Rust implementation ships `audit`, `lint-bead`, `preclose`, `safe-close`,
   and `policy-check`.
2. New beads require a machine-readable contract.
3. Hard invariants are enforced before close.
4. Acceptance criteria coverage is validated during close.
5. Compatibility behavior for legacy beads and bare audit invocation exists in
   Rust.
6. Packaging and install behavior are documented and tested.
7. Acceptance tests pass in CI.
8. At least one real repo uses `safe-close` as the standard close path.
9. The Python v1 production implementation has been removed.

## 26. Immediate Next Build Steps

1. Freeze the bead contract schema and one canonical example.
2. Freeze the list of hard invariants that cannot be disabled by policy.
3. Decide the Rust packaging layout so `install.sh` still exposes
   `br-closeout-audit`.
4. Scaffold the Rust crate and adapter interfaces.
5. Implement policy parsing, schema validation, and legacy config adaptation.
6. Implement `lint-bead` before building more heuristic audit logic.
7. Implement `preclose` and `safe-close` with fail-closed behavior.
8. Port audit functionality into Rust with compatible default invocation.
9. Add fixture-driven tests for contract parsing, close evidence, override
   journaling, and `br close` failure handling.
10. Remove v1 Python runtime code after rollout criteria are met.
