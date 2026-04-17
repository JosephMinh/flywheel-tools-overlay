# br-closeout-audit v2

## 1. Purpose

`br-closeout-audit` v2 is a full rebuild of the closeout auditor as a Rust
enforcement tool.

The product goal is simple:

1. A bead is closeable only when its declared work contract and observed
   completion evidence agree.

The design should make closure deterministic enough that agents are not left to
interpret "done" for themselves, while still allowing low-risk work to stay
lightweight.

This rebuild should not be framed as "the current auditor, but bigger." It
should be framed as a new close-control system under the same operator-facing
name.

## 2. Product Thesis

The core idea is:

1. Every bead resolves to one canonical internal contract.
2. Every contract compiles into explicit obligations.
3. The evaluator decides close readiness by checking whether those obligations
   are satisfied.

This is more robust than a design centered on parsing free-form text and
sprinkling rule-specific heuristics across the codebase.

## 3. Design Principles

1. Canonical model first, storage format second.
2. Deterministic obligations before heuristic interpretation.
3. Simple evaluation pipeline over stateful workflow machinery.
4. Fail closed on uncertainty.
5. Low-risk work should stay lightweight.
6. High-risk work should require stronger proof.
7. Explanations and remediation should be first-class outputs.
8. The rebuild should remove v1 code rather than carry it indefinitely.

## 4. Non-Goals

1. Replacing `br` as the issue tracker.
2. Running arbitrary build or test commands automatically by default.
3. Making every enforcement rule repo-configurable.
4. Building a complicated persistent workflow engine around close decisions.
5. Keeping the Python implementation as a runtime fallback.

## 5. Architecture Overview

The evaluator should be built as a small pipeline:

1. Load raw issue, policy, and repo state through adapters.
2. Normalize raw inputs into canonical internal models.
3. Compile the contract into explicit obligations.
4. Collect only the evidence needed to evaluate those obligations.
5. Produce a decision with blockers, attestations, advisories, and remediation.
6. Optionally call `br close` if the decision allows it.

Conceptually:

```text
Raw Inputs -> Canonical Models -> Obligations -> Observed Evidence -> Decision
```

This should be the mental model for the entire rebuild.

## 6. Canonical Internal Model

The rebuild should define one internal representation for the system, regardless
of where the data came from.

Suggested core models:

```rust
struct BeadContract {
    issue_id: String,
    title: String,
    mode: Mode,
    assurance_level: AssuranceLevel,
    scope_paths: Vec<PathSpec>,
    acceptance_criteria: Vec<AcceptanceCriterion>,
    evidence_plan: Vec<EvidenceRequirement>,
    advisory_checks: Vec<String>,
}

struct AcceptanceCriterion {
    id: String,
    text: String,
}

enum EvidenceRequirement {
    TouchAny { label: Option<String>, paths: Vec<PathSpec>, covers: Vec<String> },
    TouchAll { label: Option<String>, paths: Vec<PathSpec>, covers: Vec<String> },
    PathSet { label: String, paths: Vec<PathSpec>, covers: Vec<String> },
    CommandReceipt { label: Option<String>, covers: Vec<String>, command_hint: Option<String> },
    DependencyClosed { dependency_ids: Vec<String>, covers: Vec<String> },
    ArtifactExists { paths: Vec<PathSpec>, covers: Vec<String> },
    ManualAttestation { id: String, text: String, covers: Vec<String> },
}

struct ResolvedPolicy {
    policy_name: String,
    hard_invariants: HardInvariants,
    assurance_profiles: AssuranceProfiles,
    mode_packs: ModePacks,
    outputs: OutputPolicy,
}

struct ObservedEvidence {
    touched_paths: Vec<String>,
    dependency_states: Vec<DependencyState>,
    close_reason_fields: CloseReasonFields,
    receipts: Vec<EvidenceReceipt>,
}

struct Decision {
    allow_close: bool,
    blockers: Vec<Finding>,
    required_attestations: Vec<Finding>,
    advisories: Vec<Finding>,
    obligations_evaluated: Vec<ObligationResult>,
}
```

The important part is not the exact syntax. The important part is that the rule
engine never operates on raw Markdown, raw labels, or arbitrary YAML strings.

## 7. Adapters and Storage

The canonical model should be independent from storage.

Adapter types:

1. `DescriptionYamlAdapter`
   Reads a fenced YAML block from the issue description.
2. `LegacyDerivationAdapter`
   Derives a provisional contract for old beads that do not yet have one.
3. `FutureStructuredFieldsAdapter`
   Reserved for a future where `br` supports first-class structured issue
   fields.

Adapter responsibilities:

1. parse raw input
2. validate adapter-specific syntax
3. normalize into `BeadContract`
4. mark whether the contract was authored or derived

The enforcement engine must consume only the canonical `BeadContract`, not the
raw adapter outputs.

## 8. Bead Contract

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

1. `assurance_level` must be one of `minimal`, `standard`, or `strict`.
2. `acceptance_criteria` IDs must be unique.
3. `evidence_plan` must be typed and valid against schema.
4. `advisory_checks` is optional and never blocks close by itself.

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

## 9. Evidence Plan Is Executable

`evidence_plan` should not be descriptive metadata. It should be the machine
readable set of obligations that the evaluator executes.

Supported requirement kinds for the initial rebuild:

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

The evaluator should operate over these typed obligations, not over mode names
alone.

## 10. Check Classes

The system should distinguish three classes of checks:

1. Blocker
   Must pass for close to proceed.
2. Attestation
   Requires explicit human confirmation in the close path.
3. Advisory
   Never blocks close, but is shown prominently.

Mapping:

1. `evidence_plan` may contain blocker obligations and attestation obligations.
2. `advisory_checks` always remain advisory.
3. Assurance level controls how many blocker obligations are required by
   default.

This is a better fit for vague cleanup work than forcing everything into either
"hard gate" or "no gate."

## 11. Assurance Model

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

## 12. Mode Packs

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

## 13. Obligation Compilation

Close evaluation should be driven by a compiled obligation set.

Compilation order:

1. validate the canonical contract
2. resolve the mode pack
3. resolve the assurance profile
4. add universal invariants
5. add assurance-level defaults
6. add mode-pack defaults
7. add explicit contract obligations from `evidence_plan`
8. append advisory checks

This produces one flat set of obligations that the evaluator checks.

That is simpler and more robust than scattering logic across many rule-specific
branches.

## 14. Universal Invariants

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

## 15. Commands

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

## 16. Evaluation Pipeline

The close path should stay simple.

### `preclose`

1. load current issue, policy, dependency, and repo state
2. resolve the canonical contract
3. compile obligations
4. collect only the evidence needed for those obligations
5. return a decision

### `safe-close`

1. rerun the full `preclose` evaluation immediately before close
2. if blockers remain, do not call `br close`
3. if allowed, call `br close`
4. if `br close` fails, return operational error

Explicit anti-goal:

1. Do not build a reusable snapshot-token or deferred-approval workflow into
   the first rebuild.

The system should prefer fresh evaluation at close time over a more complicated
state machine.

## 17. Structured Close Evidence and Receipts

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

## 18. Manual Attestations

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

This gives low-risk vague work a structured prompt path without pretending it
can always be proven mechanically.

## 19. Policy Model

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

## 20. Output Model

Outputs should expose the decision trace clearly.

Text mode should always show:

1. issue and contract summary
2. effective mode and assurance level
3. compiled obligations
4. blockers
5. required attestations
6. advisories
7. remediation

JSON mode should include:

1. `output_version`
2. `issue_id`
3. `effective_mode`
4. `effective_assurance_level`
5. `contract_source`
6. `obligations`
7. `obligation_results`
8. `blockers`
9. `required_attestations`
10. `advisories`
11. `receipts_used`
12. `policy_name`

This makes the system explainable and tunable.

## 21. Override Model

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

## 22. Bypass Detection

Until close hooks are universal, direct `br close` remains a bypass path.

`audit` should explicitly detect and report:

1. beads closed without going through the supported close path
2. closeouts with missing required obligations
3. closeouts where evidence exists but does not satisfy the contract

This keeps the rebuild useful even before all close paths are enforced.

## 23. Performance Design

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

## 24. Simulation and Replay

The rebuild should include a simulation tool from the start.

`simulate` should support:

1. running the current policy against selected beads
2. replaying historical beads against a proposed policy
3. comparing decision deltas between two policy versions

This makes rollout safer and gives policy tuning a real feedback loop.

## 25. Testing Strategy

## 25.1 Unit Tests

1. adapter parsing and normalization
2. canonical model validation
3. obligation compilation
4. assurance profile resolution
5. mode-pack resolution
6. evidence requirement evaluation
7. receipt parsing
8. manual attestation handling
9. override classification
10. fail-closed behavior

## 25.2 Integration Tests

1. docs bead requiring `README.md` touch
2. cleanup bead using manual attestation
3. standard code bead with path obligations
4. migration bead requiring schema and migration evidence
5. security bead requiring strict evidence
6. direct close bypass detected by `audit`
7. override journaling failure
8. `br close` failure after successful evaluation

## 25.3 Parser and Drift Tests

1. malformed YAML contracts
2. legacy-derived contracts
3. multiple `br` payload shapes
4. multiple `bv` payload shapes
5. property tests for policy merge behavior

## 25.4 Golden Output Tests

1. text decision traces
2. JSON decision traces
3. simulation output
4. override output

## 26. Rollout Plan

## Phase 0: Freeze the Core Model

1. freeze the canonical internal model
2. freeze the obligation types
3. freeze the assurance model
4. freeze the mode-pack model

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
2. tune mode packs and assurance defaults
3. validate real-repo adoption

## Phase 5: Cut Over

1. make the rebuilt Rust tool the supported path
2. remove v1 code
3. remove v1-only docs

## 27. Cutover and Decommissioning

This rebuild should end with a clean cutover.

Required cutover actions:

1. remove the Python implementation from production use
2. replace the top-level executable with the Rust binary or a thin non-Python
   launcher
3. remove v1-only parsing paths that are no longer needed
4. retain only the migration tools needed for old beads still in flight

Explicit anti-goal:

1. Do not preserve the Python runtime as a permanent fallback.

## 28. Definition of Done

The rebuild is complete when all are true:

1. the Rust implementation uses a canonical internal model
2. the evaluator runs on compiled obligations, not raw Markdown heuristics
3. `evidence_plan` is executable
4. low-risk beads can use lightweight path obligations and attestations
5. strict beads can require stronger receipts
6. `lint-bead`, `preclose`, `safe-close`, `audit`, `simulate`, and
   `policy-check` all work
7. decision outputs are explainable in text and JSON
8. performance targets are met
9. at least one real repo uses the rebuilt close path
10. the Python v1 implementation has been removed

## 29. Immediate Next Steps

1. freeze the canonical type model
2. freeze the initial obligation kinds
3. freeze the assurance profiles and mode packs
4. write one canonical contract example for each of `docs`, `cleanup`, `code`,
   `migration`, and `security`
5. scaffold the Rust crate around adapters, compiler, evaluator, and output
   modules
6. implement `contract init`, `lint-bead`, and `preclose` first
7. implement receipts and manual attestation support
8. implement `safe-close`
9. implement `simulate`
10. remove v1 after cutover criteria are met
