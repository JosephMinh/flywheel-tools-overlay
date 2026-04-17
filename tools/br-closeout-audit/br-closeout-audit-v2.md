# br-closeout-audit v2

## 1. Purpose

`br-closeout-audit` v2 is a full Rust rebuild of the close-control system for
beads.

The product goal is simple:

1. a bead is closeable only when its declared work contract and observed
   completion evidence agree
2. agents should not be left to improvise what "done" means for high-risk work
3. low-risk work should still have lightweight, explicit close expectations

## 2. Rebuild Declaration

This document is for a rebuild, not an upgrade.

Rules for the rebuild:

1. do not preserve the existing Python tool's architecture, config shape,
   parser shape, or heuristic model just because they already exist
2. do not treat the current tool as the conceptual baseline
3. the existing tool may be mined for useful ideas, examples, or migration
   cases, but it must not constrain the new design
4. the Rust implementation should be designed from the close-control problem
   outward
5. the rebuild must end with the existing tool removed from the repo

This should be framed as a new close-control system under the same
operator-facing command name.

## 3. Product Thesis

The core idea is:

1. every bead resolves to one canonical internal contract
2. every contract compiles into explicit obligations
3. the evaluator decides close readiness by checking those obligations
4. the close path records explicit outcomes, receipts, and attestations

This is more robust than a design centered on parsing free-form text and
sprinkling rule-specific heuristics across the codebase.

## 4. Design Principles

1. canonical model first, storage format second
2. deterministic obligations before heuristic interpretation
3. simple evaluation pipeline over stateful workflow machinery
4. fail closed on uncertainty
5. low-risk work should stay lightweight
6. high-risk work should require stronger proof
7. explanations and remediation should be first-class outputs
8. packaging and install behavior must be explicit, not inferred later
9. the rebuild should remove v1 code rather than carry it indefinitely

## 5. Non-Goals

1. replacing `br` as the issue tracker
2. running arbitrary build or test commands automatically by default
3. making every enforcement rule repo-configurable
4. building a complicated persistent workflow engine around close decisions
5. keeping the Python implementation as a runtime fallback
6. preserving the existing CLI, config, or heuristics unless they are
   deliberately re-adopted in the rebuild spec

## 6. Architecture Overview

The evaluator should be built as a small pipeline:

1. load raw issue, policy, and repo state through adapters
2. normalize raw inputs into canonical internal models
3. resolve policy defaults and mode packs
4. compile the resolved contract into explicit obligations
5. collect only the evidence needed to evaluate those obligations
6. produce a decision with blockers, attestations, advisories, and remediation
7. optionally call `br close` if the decision allows it

Conceptually:

```text
Raw Inputs
  -> Authored Contract
  -> Resolved Contract
  -> Compiled Plan
  -> Observed Evidence
  -> Decision
```

This should be the mental model for the entire rebuild.

## 7. Canonical Internal Model

The rebuild should define one internal representation for the system, regardless
of where the data came from.

Suggested core models:

```rust
struct AuthoredContract {
    issue_id: String,
    title: String,
    source: ContractSource,
    schema_version: u32,
    mode: Option<Mode>,
    assurance_level: Option<AssuranceLevel>,
    scope_paths: Vec<PathSpec>,
    acceptance_criteria: Vec<AcceptanceCriterion>,
    evidence_plan: Vec<AuthoredRequirement>,
    advisory_checks: Vec<String>,
}

struct ResolvedContract {
    issue_id: String,
    title: String,
    source: ContractSource,
    effective_mode: Mode,
    effective_assurance_level: AssuranceLevel,
    scope_paths: Vec<PathSpec>,
    acceptance_criteria: Vec<AcceptanceCriterion>,
    evidence_plan: Vec<ResolvedRequirement>,
    advisory_checks: Vec<String>,
    policy_name: String,
}

struct CompiledPlan {
    blocker_obligations: Vec<CompiledObligation>,
    attestation_obligations: Vec<CompiledObligation>,
    advisory_checks: Vec<AdvisoryCheck>,
}

struct AcceptanceCriterion {
    id: String,
    text: String,
}

enum ContractSource {
    DescriptionYaml,
    LegacyDerived,
    FutureStructuredFields,
}

enum DecisionOutcome {
    Allowed,
    Blocked,
    AttestationRequired,
    Error,
}

struct ObservedEvidence {
    touched_paths: Vec<String>,
    dependency_states: Vec<DependencyState>,
    receipts: Vec<EvidenceReceipt>,
    close_reason_fields: CloseReasonFields,
    attestations: Vec<AttestationRecord>,
}

struct Decision {
    outcome: DecisionOutcome,
    blockers: Vec<Finding>,
    required_attestations: Vec<Finding>,
    advisories: Vec<Finding>,
    obligation_results: Vec<ObligationResult>,
    remediation: Vec<String>,
    operational_error: Option<OperationalError>,
}
```

The important part is not the exact syntax. The important part is that the rule
engine never operates on raw Markdown, raw labels, or arbitrary YAML strings.

## 8. Adapters and Storage

The canonical model should be independent from storage.

Adapter types:

1. `DescriptionYamlAdapter`
   Reads a fenced YAML block from the issue description.
2. `LegacyDerivationAdapter`
   Derives a provisional authored contract for old beads that do not yet have
   one.
3. `FutureStructuredFieldsAdapter`
   Reserved for a future where `br` supports first-class structured issue
   fields.

Adapter responsibilities:

1. parse raw input
2. validate adapter-specific syntax
3. normalize into `AuthoredContract`
4. mark whether the contract was authored or derived

The enforcement engine must consume only canonical models, not raw adapter
outputs.

## 9. Bead Contract

Until upstream tooling provides first-class fields, the contract can live in
the issue description as a fenced YAML block. That is a storage adapter, not
the internal source of truth.

Example:

```yaml
closeout_contract:
  schema_version: 1
  mode: migration
  assurance_level: strict
  scope_paths:
    - db/schema.sql
    - db/migrations/**
  acceptance_criteria:
    - id: AC-1
      text: Schema and migration files are updated together.
    - id: AC-2
      text: Verification command passes.
  evidence_plan:
    - kind: path_set
      label: schema
      paths:
        - db/schema.sql
      covers: [AC-1]
    - kind: path_set
      label: migration
      paths:
        - db/migrations/**
      covers: [AC-1]
    - kind: command_receipt
      command_hint: bun test db
      covers: [AC-2]
  advisory_checks:
    - Confirm rollback notes are documented if operator care is required.
```

Required contract fields:

1. `schema_version`
2. `mode`
3. `assurance_level`
4. `scope_paths`
5. `acceptance_criteria`
6. `evidence_plan`

Rules:

1. `assurance_level` must be one of `minimal`, `standard`, or `strict`
2. `acceptance_criteria` IDs must be unique
3. `evidence_plan` must be typed and valid against schema
4. `advisory_checks` is optional and never blocks close by itself

Example: lightweight docs bead

```yaml
closeout_contract:
  schema_version: 1
  mode: docs
  assurance_level: minimal
  scope_paths:
    - README.md
  acceptance_criteria: []
  evidence_plan:
    - kind: touch_any
      paths: [README.md]
  advisory_checks:
    - Confirm the README matches the current operator workflow.
```

Example: broad cleanup bead

```yaml
closeout_contract:
  schema_version: 1
  mode: cleanup
  assurance_level: minimal
  scope_paths: []
  acceptance_criteria: []
  evidence_plan:
    - kind: manual_attestation
      id: cleanup-confirmed
      text: Cleanup completed without leaving obvious stale references.
      covers: []
  advisory_checks:
    - Confirm the repo still feels coherent after cleanup.
```

## 10. Evidence Plan Is Executable

`evidence_plan` should not be descriptive metadata. It should be the machine
readable set of obligations that the evaluator executes.

### 10.1 Primitive Obligation Kinds

Supported primitive requirement kinds for the initial rebuild:

1. `touch_any`
   At least one declared path or glob must be touched.
2. `touch_all`
   Every declared path or glob must be touched.
3. `path_set`
   A labeled set of paths must have evidence.
4. `command_receipt`
   A structured command proof must be present.
5. `dependency_closed`
   Named dependencies must be closed.
6. `artifact_exists`
   A declared file or artifact must exist.
7. `manual_attestation`
   The closer must explicitly attest to a condition.

### 10.2 Obligation Composition

The obligation language should also support simple composition so the engine can
stay generic:

1. `all_of`
   Every child requirement must satisfy.
2. `any_of`
   At least one child requirement must satisfy.
3. `n_of`
   At least `n` child requirements must satisfy.

Example:

```yaml
- kind: any_of
  requirements:
    - kind: touch_any
      paths: [README.md]
    - kind: artifact_exists
      paths: [docs/generated/readme-preview.md]
```

### 10.3 Obligation Result Semantics

Each compiled obligation should evaluate to one of:

1. `satisfied`
2. `unsatisfied`
3. `unknown`
4. `not_applicable`

Rules:

1. blocker obligations with `unknown` produce `Error`
2. blocker obligations with `unsatisfied` produce `Blocked`
3. attestation obligations that are unresolved produce
   `AttestationRequired`
4. `not_applicable` is only allowed when the compiled plan explicitly marks the
   obligation conditional and the condition is not active

The evaluator should operate over typed obligations and result states, not over
mode names alone.

## 11. Check Classes

The system should distinguish three classes of checks:

1. blocker
   Must pass for close to proceed.
2. attestation
   Requires explicit human confirmation in the close path.
3. advisory
   Never blocks close, but is shown prominently.

Mapping:

1. `evidence_plan` may contain blocker obligations and attestation obligations
2. `advisory_checks` always remain advisory
3. assurance level controls how many blocker obligations are required by
   default

This is a better fit for vague cleanup work than forcing everything into either
"hard gate" or "no gate."

## 12. Assurance Model

Assurance level defines the baseline rigor of the bead.

### `minimal`

Use for low-risk work like README edits, broad cleanup, or housekeeping.

Baseline behavior:

1. keep the universal invariants
2. allow lightweight `evidence_plan`
3. allow `manual_attestation` where hard machine proof is not appropriate
4. do not require command receipts by default

### `standard`

Use for normal code, docs, and test work that should provide clear proof of
completion.

Baseline behavior:

1. require meaningful `scope_paths` or equivalent path obligations
2. require explicit acceptance criteria when the work is scoped enough to define
   them
3. require evidence obligations that map to the work

### `strict`

Use for security, migration, verification, and other high-risk work.

Baseline behavior:

1. apply all standard requirements
2. require stronger typed obligations
3. require structured receipts for command-style verification obligations
4. use tighter override policy

## 13. Mode Packs

Modes should not exist as a pile of special cases in core code. They should be
resolved as rule packs that define defaults and minimums.

Canonical mode packs for the initial rebuild:

1. `cleanup`
2. `docs`
3. `code`
4. `test`
5. `verification`
6. `migration`
7. `security`

Each mode pack may define:

1. default assurance level
2. minimum assurance level
3. default obligation templates
4. mode-specific receipt requirements
5. mode-specific remediation hints

Examples:

1. `docs`
   Defaults to `minimal`
2. `cleanup`
   Defaults to `minimal`
3. `code`
   Defaults to `standard`
4. `migration`
   Minimum `strict`
5. `security`
   Minimum `strict`

## 14. Compilation Model

Close evaluation should be driven by a compiled plan.

Compilation order:

1. validate the authored contract
2. resolve the mode pack
3. resolve the assurance profile
4. materialize a `ResolvedContract`
5. add universal invariants
6. add assurance-level defaults
7. add mode-pack defaults
8. add explicit contract obligations from `evidence_plan`
9. classify obligations as blocker or attestation
10. append advisory checks

This produces one compiled plan that the evaluator checks.

That is simpler and more robust than scattering logic across many rule-specific
branches.

## 15. Universal Invariants

These apply to every close attempt:

1. a valid contract exists or a permitted legacy contract can be derived
2. canonical mode resolves successfully
3. canonical assurance level resolves successfully
4. blocker dependencies are closed
5. some non-meta repo evidence exists unless policy explicitly allows an
   attestation-only minimal bead
6. operational uncertainty blocks close

Examples of operational uncertainty:

1. `br` output cannot be parsed
2. `bv --robot-history` returns an unsupported shape
3. git queries fail
4. policy loading or schema validation fails
5. receipt or override journaling fails

## 16. Evaluation Pipeline

The close path should stay simple.

### `preclose`

1. load current issue, policy, dependency, and repo state
2. resolve the authored contract into a resolved contract
3. compile a plan
4. collect only the evidence needed for that plan
5. evaluate obligations
6. return a decision

### `safe-close`

1. rerun the full `preclose` evaluation immediately before close
2. if the outcome is `Blocked`, do not call `br close`
3. if the outcome is `AttestationRequired`, require explicit attestation inputs
   and rerun evaluation
4. if the outcome is `Allowed`, call `br close`
5. if `br close` fails, return `Error`

Explicit anti-goal:

1. do not build a reusable snapshot-token or deferred-approval workflow into
   the first rebuild

The system should prefer fresh evaluation at close time over a more complicated
state machine.

## 17. Decision Outcomes and Exit Codes

The decision model must be explicit.

### `Allowed`

Meaning:

1. evaluation completed successfully
2. no blocker obligations are unsatisfied
3. no required attestation obligations remain unresolved

CLI behavior:

1. exit code `0`
2. `safe-close` may call `br close`

### `Blocked`

Meaning:

1. evaluation completed successfully
2. one or more blocker obligations are unsatisfied

CLI behavior:

1. exit code `1`
2. `safe-close` must not call `br close`

### `Error`

Meaning:

1. the tool could not make a trustworthy decision
2. this is an operational or integrity failure, not a normal unmet-work failure

Examples:

1. contract parse failure
2. policy parse failure
3. upstream `br` or `bv` payload unreadable
4. git query failure
5. receipt or attestation journaling failure

CLI behavior:

1. exit code `2`
2. `safe-close` must not call `br close`
3. `Error` is non-overridable

### `AttestationRequired`

Meaning:

1. evaluation completed successfully
2. no blocker obligations are unsatisfied
3. one or more required attestation obligations remain unresolved

CLI behavior:

1. exit code `3`
2. `safe-close` must not call `br close` until the required attestations are
   supplied explicitly
3. supplied attestations must be recorded before the final close attempt

## 18. Structured Close Evidence and Receipts

Strict command-style evidence should be stronger than free text.

When a `command_receipt` obligation is present, the tool should require
structured evidence with fields such as:

1. covered AC IDs
2. command
3. result
4. actor
5. timestamp
6. git HEAD

For the rebuild, this should be represented internally as a typed
`EvidenceReceipt`.

Receipts may be stored under a tool-owned path such as:

1. `.ntm/closeout-audit/receipts/`

This gives `strict` mode a reliable proof surface without requiring a larger
workflow engine.

## 19. Manual Attestations

Some work is real but not naturally machine-provable.

For those cases, the rebuild should support `manual_attestation` obligations in
the contract.

Examples:

1. broad cleanup work
2. repository organization work
3. human review confirmation

Requirements:

1. attestations must be explicit
2. attestations must be recorded in the close path
3. attestations should appear in output and logs clearly
4. attestation obligations should be allowed mainly for `minimal` work unless a
   mode pack explicitly permits them at a higher level

This gives low-risk vague work a structured prompt path without pretending it
can always be proven mechanically.

## 20. Policy Model

Policy should tune the engine, not define the entire engine.

Top-level areas:

1. hard invariants
2. assurance profiles
3. mode packs
4. adapter settings
5. output settings

Rules:

1. hard invariants cannot be disabled
2. arrays replace unless explicitly documented otherwise
3. mode packs may raise assurance floors
4. repo policy may tune defaults, not remove the core decision model

Example effective policy shape:

```json
{
  "schema_version": 2,
  "policy_name": "default-v2",
  "hard_invariants": {
    "require_contract": true,
    "require_canonical_mode": true,
    "require_canonical_assurance_level": true,
    "require_closed_blockers": true,
    "require_non_meta_evidence": true,
    "allow_attestation_only_minimal_beads": false,
    "fail_closed_on_operational_error": true
  },
  "assurance_profiles": {
    "minimal": {
      "require_command_receipts": false
    },
    "standard": {
      "require_command_receipts": false
    },
    "strict": {
      "require_command_receipts": true,
      "override_policy": "elevated"
    }
  },
  "mode_packs": {
    "docs": {
      "default_assurance_level": "minimal",
      "minimum_assurance_level": "minimal"
    },
    "cleanup": {
      "default_assurance_level": "minimal",
      "minimum_assurance_level": "minimal"
    },
    "code": {
      "default_assurance_level": "standard",
      "minimum_assurance_level": "standard"
    },
    "migration": {
      "default_assurance_level": "strict",
      "minimum_assurance_level": "strict"
    },
    "security": {
      "default_assurance_level": "strict",
      "minimum_assurance_level": "strict"
    }
  }
}
```

## 21. Commands

Recommended command surface for the rebuild:

1. `contract init`
   Create a starter contract for a bead.
2. `contract derive`
   Derive a provisional contract for a legacy bead.
3. `contract normalize`
   Normalize and validate contract formatting.
4. `lint-bead`
   Validate contract quality and required structure.
5. `preclose`
   Evaluate close readiness using current state.
6. `safe-close`
   Run evaluation and call `br close` only if allowed.
7. `audit`
   Review already closed beads and detect bypasses.
8. `simulate`
   Replay a policy or contract against selected beads without changing state.
9. `policy-check`
   Validate and print the effective policy.

This command set makes the rebuild useful for authoring, migration, evaluation,
and rollout, not just final gating.

## 22. Packaging, Installation, and CLI Contract

This section is required because the repo installer only symlinks top-level
executable files under each tool directory.

Required packaging contract:

1. the supported command name remains `br-closeout-audit`
2. the repo must continue to expose a top-level executable at
   `tools/br-closeout-audit/br-closeout-audit`
3. that top-level executable may be either:
   1. the Rust binary itself
   2. a minimal non-Python launcher that execs a co-located Rust binary
4. the Rust implementation must not require the Python v1 runtime to launch
5. the install path must remain compatible with `./install.sh`

Recommended source and artifact layout:

```text
tools/br-closeout-audit/
├── br-closeout-audit           # stable installed entrypoint
├── README.md
├── rust/                       # Cargo crate for the rebuild
└── bin/
    └── br-closeout-audit       # built Rust binary or release artifact
```

Build and install requirements:

1. local development must document an explicit Cargo build command
2. if the stable entrypoint is a launcher, it must fail with a clear error when
   the Rust binary is missing
3. release packaging must define how the built binary lands under the tool
   directory before `./install.sh` runs
4. automation-facing commands must support stable exit codes and `--json`
   outputs

CLI contract:

1. `preclose`, `safe-close`, `audit`, `simulate`, and `policy-check` must all
   support `--json`
2. evaluation commands must use the outcome exit codes defined in this spec
3. `safe-close` must expose explicit attestation flags for
   `AttestationRequired` outcomes
4. CLI parse and usage errors should be distinct from evaluation outcomes

This keeps the rebuild operationally shippable instead of leaving packaging
decisions to implementation drift.

## 23. Output Model

Outputs should expose the decision trace clearly.

Text mode should always show:

1. issue and contract summary
2. effective mode and assurance level
3. compiled obligations
4. obligation results
5. blockers
6. required attestations
7. advisories
8. remediation

JSON mode should include:

1. `output_version`
2. `issue_id`
3. `outcome`
4. `effective_mode`
5. `effective_assurance_level`
6. `contract_source`
7. `compiled_plan`
8. `obligation_results`
9. `blockers`
10. `required_attestations`
11. `advisories`
12. `receipts_used`
13. `policy_name`
14. `operational_error`

This makes the system explainable and tunable.

## 24. Override Model

Overrides should be explicit and classed.

Suggested classes:

1. non-overridable
   Operational errors, invalid contract state, and core parse failures
2. justified override
   Lower-risk gaps where operator rationale can allow close
3. elevated override
   Strict-mode gaps that require stronger operator intent and logging

Rules:

1. override attempts must never bypass operational uncertainty
2. strict-mode overrides should require stronger ceremony
3. all overrides must be journaled to a tool-owned path

Suggested log path:

1. `.ntm/closeout-audit/override-log.jsonl`

## 25. Bypass Detection

Until close hooks are universal, direct `br close` remains a bypass path.

`audit` should explicitly detect and report:

1. beads closed without going through the supported close path
2. closeouts with missing required obligations
3. closeouts where evidence exists but does not satisfy the contract
4. closeouts that should have had attestations or receipts but do not

This keeps the rebuild useful even before all close paths are enforced.

## 26. Performance Design

Performance should be designed, not wished for.

Guidelines:

1. compile obligations before collecting evidence
2. collect only the evidence needed by those obligations
3. cache tracked files and glob expansion within a single process
4. do not run history searches for obligations that do not need them
5. keep `minimal` beads on a fast path
6. prefer one upstream query per source per issue where possible

Initial target:

1. median single-issue `preclose` under 1 second on warm local state

## 27. Simulation and Replay

The rebuild should include a simulation tool from the start.

`simulate` should support:

1. running the current policy against selected beads
2. replaying historical beads against a proposed policy
3. comparing decision deltas between two policy versions
4. replaying the conformance corpus as a deterministic acceptance check

This makes rollout safer and gives policy tuning a real feedback loop.

## 28. Testing Strategy

### 28.1 Unit Tests

1. adapter parsing and normalization
2. authored contract validation
3. resolved contract construction
4. compiled plan generation
5. obligation composition semantics
6. obligation result classification
7. assurance profile resolution
8. mode-pack resolution
9. receipt parsing
10. manual attestation handling
11. override classification
12. fail-closed behavior

### 28.2 Integration Tests

1. docs bead requiring `README.md` touch
2. cleanup bead using manual attestation
3. standard code bead with path obligations
4. migration bead requiring schema and migration evidence
5. security bead requiring strict evidence
6. direct close bypass detected by `audit`
7. override journaling failure
8. `br close` failure after successful evaluation

### 28.3 Parser and Drift Tests

1. malformed YAML contracts
2. legacy-derived contracts
3. multiple `br` payload shapes
4. multiple `bv` payload shapes
5. property tests for policy merge behavior

### 28.4 Golden Output Tests

1. text decision traces
2. JSON decision traces
3. simulation output
4. override output

## 29. Conformance Corpus

The rebuild should ship with a versioned conformance corpus. This corpus is the
behavior contract for the system, not just a convenience test fixture.

Recommended layout:

```text
tools/br-closeout-audit/tests/corpus/
  <scenario-id>/
    manifest.yaml
    issue.json
    policy.json
    dependencies.json
    close_input.json
    receipts/
    repo/
    expected/
      authored_contract.json
      resolved_contract.json
      compiled_plan.json
      decision.json
      text_output.txt
```

Fixture rules:

1. `manifest.yaml` declares the scenario ID, command under test, expected
   outcome, and expected exit code
2. `issue.json` stores the raw issue payload seen by adapters
3. `policy.json` stores the effective policy for that case
4. `repo/` stores the minimal repo fixture needed for evaluation
5. `close_input.json` stores close-reason fields and attestation inputs where
   relevant
6. `expected/` stores canonical intermediate and final outputs

Required scenario classes:

1. authored contract allowed
2. authored contract blocked
3. attestation required
4. operational error
5. legacy-derived contract
6. strict receipt missing
7. strict receipt present
8. mode-floor enforcement
9. override allowed
10. override denied
11. bypass detected by audit
12. parser drift for `br`
13. parser drift for `bv`

The corpus should be runnable both as test fixtures and via `simulate`.

## 30. Rollout Plan

## Phase 0: Freeze the Core Model

1. freeze the authored, resolved, and compiled models
2. freeze the obligation language and result semantics
3. freeze the assurance model
4. freeze the mode-pack model
5. freeze the CLI outcome contract

## Phase 1: Build the Engine

1. implement adapters
2. implement canonical models
3. implement obligation compiler
4. implement evidence evaluator
5. implement decision output

## Phase 2: Build Authoring and Migration Tools

1. ship `contract init`
2. ship `contract derive`
3. ship `contract normalize`
4. migrate legacy open beads

## Phase 3: Build Close Controls

1. ship `lint-bead`
2. ship `preclose`
3. ship `safe-close`
4. ship receipt and attestation handling

## Phase 4: Build Replay and Policy Tooling

1. ship `simulate`
2. ship the conformance corpus runner
3. tune mode packs and assurance defaults
4. validate real-repo adoption

## Phase 5: Cut Over

1. make the rebuilt Rust tool the supported path
2. remove the existing tool implementation
3. remove v1-only docs and configs

## 31. Cutover and Decommissioning

This rebuild should end with a clean cutover and deletion of the old tool.

Two acceptable decommission paths:

1. immediate removal path
   If operators do not need the current tool during the rebuild, remove the
   existing implementation early and build the Rust tool as the only supported
   code path.
2. parallel build path
   If operators still need the current tool during development, freeze it,
   build the Rust tool in parallel, replace the stable entrypoint at cutover,
   then delete the existing implementation immediately after cutover.

Required end state regardless of path:

1. the existing Python implementation is removed from the repo
2. the installed `br-closeout-audit` entrypoint resolves to the Rust rebuild
3. v1-only parsing, docs, and config assumptions are removed
4. only deliberate migration helpers remain, if still needed for in-flight
   beads

Explicit anti-goal:

1. do not preserve the Python runtime as a permanent fallback

## 32. Definition of Done

The rebuild is complete when all are true:

1. the Rust implementation uses authored, resolved, and compiled canonical
   models
2. the evaluator runs on compiled obligations, not raw Markdown heuristics
3. `evidence_plan` is executable
4. the obligation language supports composition and explicit result semantics
5. low-risk beads can use lightweight path obligations and attestations
6. strict beads can require stronger receipts
7. `lint-bead`, `preclose`, `safe-close`, `audit`, `simulate`, and
   `policy-check` all work
8. packaging and install behavior are documented and implemented
9. decision outputs are explainable in text and JSON
10. the conformance corpus passes
11. performance targets are met
12. at least one real repo uses the rebuilt close path
13. the existing Python implementation has been removed

## 33. Immediate Next Steps

1. freeze the authored, resolved, and compiled type model
2. freeze the primitive and composite obligation kinds
3. freeze obligation result semantics and exit codes
4. freeze the assurance profiles and mode packs
5. write one canonical contract example for each of `docs`, `cleanup`, `code`,
   `migration`, and `security`
6. define the stable tool layout and launcher contract under
   `tools/br-closeout-audit/`
7. scaffold the Rust crate around adapters, compiler, evaluator, and output
   modules
8. implement `contract init`, `lint-bead`, and `preclose` first
9. implement receipts and manual attestation support
10. implement the conformance corpus runner
11. implement `safe-close`
12. implement `simulate`
13. choose the immediate-removal or parallel-build decommission path and remove
    the existing tool accordingly
