# br-closeout-audit v2

## 1. Purpose

`br-closeout-audit` v2 upgrades the current post-hoc closeout detector into a full lifecycle quality gate:

1. Prevent bad beads from being created.
2. Prevent hollow closeouts before `br close` runs.
3. Keep post-hoc auditing for detection and trend reporting.

v2 is designed to be a hard control system, not just a reporting tool.

## 2. Language and Runtime Choice

## Decision: Rust

Rust is the best fit for v2.

1. Native fit with surrounding tooling (`br`, `bv`) and expected operator workflows.
2. Single static binary for local hooks, CI, and remote runners.
3. Strong typing for policy schema evolution and deterministic gating logic.
4. Fast execution for frequent preclose checks.
5. Easier long-term maintenance than a growing shell/Python wrapper stack.

## 3. Current State (v1)

v1 (Python) currently provides:

1. Post-hoc audit across:
   - `br show --json`
   - `bv --robot-history`
   - `git log`/`git show` fallback evidence
2. Findings like:
   - `open_blocker_dependencies`
   - `meta_only_commits`
   - `expected_paths_not_touched`
   - `meta_close_commit`
   - mode-specific checks (verification/migration)
3. Layered policy merge (global defaults + repo overrides).

Main gap: v1 runs after closure. It can detect hollow closeouts, but cannot reliably block them before they happen.

## 4. v2 Goals

1. Add preventative controls at create/update and close stages.
2. Keep backward-compatible post-hoc audit output for reporting.
3. Minimize false positives with explicit, configurable policy.
4. Provide clear remediation output so users can fix quickly.
5. Support gradual rollout: warn-only first, then hard block.

## 5. Non-Goals

1. Replacing `br` issue storage or dependency engine.
2. Running arbitrary build/test commands by default.
3. Enforcing one global policy for all repositories.

## 6. CLI Surface

Binary name remains `br-closeout-audit`.

## 6.1 Subcommands

1. `audit`
2. `preclose`
3. `safe-close`
4. `lint-bead`
5. `policy-check`

## 6.2 Command Contracts

### `audit` (detective)

Post-hoc analysis over already-closed beads.

Examples:

```bash
br-closeout-audit audit
br-closeout-audit audit --issue content_engine-x9g.1
br-closeout-audit audit --since-rev HEAD~30 --format json
```

Exit codes:

- `0`: no blockers
- `1`: blocker findings present
- `2`: operational/tool error

### `preclose` (preventative gate)

Evaluates close readiness before calling `br close`.

Examples:

```bash
br-closeout-audit preclose --issue content_engine-x9g.1 --reason "Completed evidence: ..."
br-closeout-audit preclose --issue content_engine-x9g.1 --staged-only
```

Exit codes:

- `0`: close allowed
- `1`: close blocked (policy blocker)
- `2`: operational/tool error

### `safe-close` (workflow wrapper)

Runs `preclose`, then calls `br close` only if gating passes.

Examples:

```bash
br-closeout-audit safe-close --issue content_engine-x9g.1 --reason "Completed evidence: ..."
br-closeout-audit safe-close --issue content_engine-x9g.1 --reason "..." --suggest-next
```

Behavior:

1. Resolve policy and issue mode.
2. Run all preclose blockers.
3. If passed: run `br close` with provided reason/session flags.
4. If blocked: print actionable remediation and do not call `br close`.

### `lint-bead` (create/update gate)

Validates bead quality contract before close time.

Examples:

```bash
br-closeout-audit lint-bead --issue content_engine-x9g.1 --stage create
br-closeout-audit lint-bead --issue content_engine-x9g.1 --stage update
```

Intended use:

- run after `br create`
- run during `br update` workflows
- run in CI for policy conformance

### `policy-check`

Validates merged policy file and prints normalized effective policy.

Examples:

```bash
br-closeout-audit policy-check
br-closeout-audit policy-check --format json
```

## 7. Policy Model (v2)

Policy remains layered:

1. global default config (tool directory)
2. repo-local override files
3. optional runtime `--policy` path

## 7.1 Schema Versioning

Add required top-level fields:

- `schema_version` (integer)
- `policy_name` (string)

Tool will reject unknown major schema versions.

## 7.2 Proposed Effective Policy Shape

```json
{
  "schema_version": 2,
  "policy_name": "default-v2",
  "defaults": {
    "default_since_rev": "HEAD~20",
    "default_limit": 10,
    "meta_globs": [".beads/**", ".ntm/**", "**/*.png"],
    "close_commit_meta_prefixes": ["chore: close ", "chore: sync beads"]
  },
  "modes": {
    "code": {
      "required_sections": ["Background", "Scope", "Where", "Acceptance", "Evidence Plan"],
      "require_expected_paths": true,
      "require_acceptance_criteria": true
    },
    "verification": {
      "required_sections": ["Background", "Scope", "Where", "Acceptance", "Evidence Plan"],
      "require_close_reason_tokens": ["evidence:", "command=", "result="],
      "require_acceptance_criteria": true
    },
    "migration": {
      "required_sections": ["Background", "Scope", "Where", "Acceptance", "Evidence Plan"],
      "require_path_sets": [
        {"label": "schema", "globs": ["**/schema.prisma", "**/schema.sql"]},
        {"label": "migration", "globs": ["**/migrations/**"]}
      ],
      "require_acceptance_criteria": true
    },
    "test": {
      "required_sections": ["Background", "Scope", "Where", "Acceptance", "Evidence Plan"],
      "require_any_globs": ["**/tests/**", "**/*.test.ts", "**/*.spec.ts"],
      "require_acceptance_criteria": true
    },
    "docs": {
      "required_sections": ["Background", "Scope", "Acceptance"],
      "require_acceptance_criteria": true
    }
  },
  "prevention": {
    "enforce_on_create": true,
    "enforce_on_update": true,
    "block_meta_only_close": true,
    "block_open_blocker_dependencies": true,
    "allow_force_override": true,
    "force_override_requires_reason": true,
    "force_override_reason_min_length": 30
  },
  "outputs": {
    "include_remediation_hints": true,
    "max_details_per_finding": 8
  }
}
```

## 8. Constraint System Per Bead

This is the main preventative layer.

## 8.1 Required Sections

By mode/type, require markdown sections in issue description:

1. `## Background`
2. `## Scope`
3. `## Where` (or `## Files touched`)
4. `## Acceptance`
5. `## Evidence Plan`

## 8.2 Expected Paths

For non-docs modes, bead must include file references or globs in sectioned text.

Validation:

1. parse backticked paths/globs
2. resolve against tracked files where possible
3. fail lint if no usable path hints found

## 8.3 Acceptance Criteria Required

For non-docs and docs alike, require explicit acceptance criteria.

Source fields checked in order:

1. dedicated `acceptance_criteria`
2. `## Acceptance` section in description

## 8.4 Structured Close Reason

For verification/test/migration modes, close reason must include evidence tokens and concrete data.

Example template:

```text
Completed
Evidence: command=bun test packages/foo --runInBand
Result: pass
Run_ID: local-2026-04-12T07:10:00Z
```

## 9. Finding Taxonomy (v2)

Retain v1 codes and add prevention-specific codes.

## 9.1 New Lint Codes

1. `missing_required_section`
2. `missing_acceptance_criteria`
3. `missing_expected_paths`
4. `invalid_expected_path_pattern`
5. `insufficient_close_reason_template`

## 9.2 New Preclose Codes

1. `staged_meta_only_close` (staged diff only touches meta paths)
2. `close_reason_missing_structured_evidence`
3. `force_override_without_reason`

## 10. Override Model

Overrides must be explicit, auditable, and painful enough to discourage casual bypass.

## 10.1 Mechanics

1. `safe-close` supports `--force-closeout`.
2. Requires `--force-reason` meeting minimum length.
3. Emits `WARN` banner in terminal and JSON output.
4. Writes override record to `.beads/.br_history/closeout_overrides.jsonl`.

## 10.2 Override Record Fields

1. timestamp
2. actor
3. issue_id
4. blocked_codes
5. force_reason
6. git_head_sha

## 11. Data Flow and Adapters

## 11.1 Upstream Commands

1. `br show --json <id>`
2. `bv --robot-history`
3. `git log` / `git show`
4. `git diff --cached --name-only` (preclose staged gate)

## 11.2 Adapter Layer

Use Rust adapter traits to isolate command execution from policy logic.

1. `BrClient`
2. `BvClient`
3. `GitClient`

This enables deterministic unit tests with mock clients.

## 12. Output Contracts

## 12.1 Text Mode

1. short summary
2. per-issue status
3. blocker/warn lines
4. remediation steps for each blocker

## 12.2 JSON Mode

Top-level fields:

1. `repo`
2. `summary`
3. `results`
4. `policy`
5. `command`
6. `timestamp`

Each finding includes:

1. `severity`
2. `code`
3. `message`
4. `details`
5. `remediation`
6. `source_stage` (`lint`, `preclose`, `audit`)

## 13. Rollout Plan

## Phase 1: Rust parity port (v1-equivalent)

1. Implement `audit` with current v1 checks.
2. Maintain compatible exit codes and JSON shape where possible.
3. Validate against existing repos.

## Phase 2: Preventative close gate

1. Add `preclose` and `safe-close`.
2. Add staged meta-only blocker.
3. Add structured close reason gate by mode.

## Phase 3: Bead quality linting

1. Add `lint-bead` for create/update stages.
2. Add required sections and expected path checks.
3. Add acceptance criteria enforcement.

## Phase 4: Soft launch

1. Start in warn-only mode for lint/preclose in selected repos.
2. Collect false-positive cases.
3. Tune policy defaults.

## Phase 5: Hard enforcement

1. Enable blocker mode by default.
2. Require override reason for bypass.
3. Add CI and pre-push docs/workflows.

## 14. Test Strategy

## 14.1 Unit Tests

1. policy merge and schema validation
2. mode classification
3. expected path extraction/resolution
4. finding generation for each rule
5. override validation

## 14.2 Integration Tests

Use fixture repos with scripted histories:

1. good closeout path
2. meta-only closeout
3. missing dependency closeout
4. migration missing schema/migration set
5. verification missing evidence tokens

## 14.3 Golden Output Tests

1. text output snapshot
2. json output snapshot
3. remediation hint snapshot

## 15. Acceptance Test Matrix

| ID | Stage | Scenario | Input Setup | Expected Result |
|---|---|---|---|---|
| AT-01 | lint/create | Minimal bead with title only | no description, no acceptance | BLOCKER `missing_required_section`, `missing_acceptance_criteria` |
| AT-02 | lint/create | Code bead missing `Where` | description lacks path section | BLOCKER `missing_required_section` |
| AT-03 | lint/update | Code bead has sections but no paths | no file/glob references | BLOCKER `missing_expected_paths` |
| AT-04 | lint/update | Docs bead with required docs sections | background/scope/acceptance present | PASS |
| AT-05 | preclose | Open blocker dependencies exist | one `blocks` dep not closed | BLOCKER `open_blocker_dependencies` |
| AT-06 | preclose | Staged diff touches only `.beads/**` | staged files all meta | BLOCKER `staged_meta_only_close` |
| AT-07 | preclose | Verification close reason lacks tokens | reason=`Completed` | BLOCKER `close_reason_missing_structured_evidence` |
| AT-08 | preclose | Migration bead missing migration files | schema touched only | BLOCKER `missing_required_path_set_migration` |
| AT-09 | safe-close | Preclose blockers present | any blocker | no `br close` call executed |
| AT-10 | safe-close | No blockers | compliant bead + evidence | `br close` executed successfully |
| AT-11 | safe-close override | `--force-closeout` without reason | override flag only | BLOCKER `force_override_without_reason` |
| AT-12 | safe-close override | `--force-closeout` with valid reason | long rationale provided | close proceeds + override record persisted |
| AT-13 | audit | Existing closed bead with linked code commits | non-meta evidence present | PASS or WARN only |
| AT-14 | audit | Closed bead with meta-only evidence | linked commits only `.beads/**` | BLOCKER `meta_only_commits` |
| AT-15 | audit | Expected paths never touched | bead names paths not touched | BLOCKER `expected_paths_not_touched` |
| AT-16 | audit | Close commit subject is meta prefix but code evidence exists | close commit `chore: close ...` and real code elsewhere | WARN `meta_close_commit` |
| AT-17 | policy-check | Invalid schema version | `schema_version=999` | exit 2 with schema error |
| AT-18 | policy-check | Valid merged policy | defaults + repo override | PASS with normalized output |
| AT-19 | json contract | Findings include remediation | failing scenario in json mode | remediation array present |
| AT-20 | performance | Single-issue preclose latency | warm cache local repo | <1s median runtime |

## 16. Project Integration (recommended)

1. Alias `br close` workflows to `br-closeout-audit safe-close` in team docs.
2. Add optional pre-push command:

```bash
br-closeout-audit audit --since-rev "$(git merge-base HEAD origin/main)"
```

3. Add optional create/update workflow examples:

```bash
br create --title "..." --description "..."
br-closeout-audit lint-bead --issue <id> --stage create
```

## 17. Definition of Done (v2)

v2 is complete when all are true:

1. Rust binary shipped with `audit`, `preclose`, `safe-close`, `lint-bead`, `policy-check`.
2. Policy v2 schema implemented and validated.
3. Acceptance matrix AT-01 to AT-20 passes in CI.
4. Documentation updated with migration guidance from v1.
5. At least one real repository uses `safe-close` as default close path.

## 18. Immediate Next Build Steps

1. Scaffold Rust crate in `tools/br-closeout-audit/`.
2. Implement policy parser + schema validation.
3. Port existing v1 checks into shared evaluation engine.
4. Add `preclose` and `safe-close` command wiring.
5. Add first fixture-driven integration tests for AT-05, AT-06, AT-07, AT-10.

