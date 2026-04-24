# Stack Sync Plan

## Purpose

This repo needs a durable, explicit process for owning customized tool forks
while still ingesting upstream improvements when they are worth taking.

The end state is:

1. upstream code remains ingestible
2. local custom functionality is documented and preserved deliberately
3. syncs are reproducible, reviewable, testable, repairable, and fast enough to use routinely
4. a selected NTM-managed agent performs reconciliation work under human-owned policy
5. the overlay repo remains the control plane for the stack
6. direct merges are safe enough to use because the system records enough state to recover non-destructively

## Core Thesis

`flywheel-sync` is a synthesis tool, not a chooser tool.

If the goal were merely to pick upstream or local versions of files, a normal
git merge or manual conflict resolution workflow would already be sufficient.

This tool exists for the harder case:

1. upstream improved something that should be taken
2. the local fork added something that must be preserved
3. the best final implementation may be neither version as written
4. the correct result may require rewriting, splitting, moving, or deleting files
5. the correct result may also require updating tests, docs, and integration code

The mechanism of this tool must therefore be explicit:

1. show the agent both the upstream delta and the local fork delta
2. require the agent to understand the functionality and intent of both
3. have the agent produce the best combined implementation of the desired behavior
4. verify the combined behavior with invariant-driven checks before acceptance

## Non-Goal

This tool is not intended to automate any of these weak strategies:

1. choose `ours` or `theirs` for each conflicted file
2. prefer the smaller textual diff
3. preserve file shape at the expense of preserving behavior
4. treat file boundaries as sacred when a better combined design crosses files
5. mark a sync successful because conflicts are gone even if the resulting behavior is wrong

Any implementation that behaves like a file chooser is the wrong implementation.

## What The Tool Actually Does

The unit of reconciliation is behavior or subsystem, not file text.

For every changed area, the tool should drive the agent to answer:

1. what new or improved behavior exists upstream?
2. what local behavior or policy exists in the fork?
3. which parts of each are still desirable?
4. where do they conflict?
5. what combined design best preserves the desired capabilities from both sides?
6. what code, tests, and docs need to change to realize that combined design?

The final implementation may:

1. keep the upstream version of a file
2. keep the local fork version of a file
3. write a synthesized third version of a file
4. split one file into several files
5. merge multiple files into one
6. move logic between files or modules
7. delete code made obsolete by the synthesized design
8. add or update tests and docs so the new combined behavior is explicit

That freedom is the point of the tool.

## Explicit Reconciliation Mechanism

This section defines the mechanism in procedural terms so there is no ambiguity.

### Inputs

Every reconciliation run must gather all of the following:

1. upstream diff from the last accepted upstream frontier to the candidate upstream tip
2. local fork diff from the same frontier to the current fork tip
3. the current source tree of the fork
4. the current source tree of the upstream tip where needed for context
5. `merge.md`, which gives the worker direct instructions on how this fork expects synthesis to be performed
6. human docs describing local differences, invariants, and prior sync guidance
7. machine-readable customization data and invariant mappings
8. relevant tests, golden outputs, and smoke checks

Git-derived diffs and logs are mandatory raw evidence.

Higher-level planning artifacts such as a per-tool bead graph may be included as
supporting context or used to generate summaries, but they must not replace the
git-derived evidence bundle.

The reason is simple:

1. a graph records intended work or declared structure
2. a diff records what actually changed in the fork and upstream
3. drift detection depends on comparing the declared model against the live delta

### Analysis Model

The tool should not prompt the agent with "choose a side per file."

Instead it should prompt the agent to:

1. identify changed subsystems or behavior areas
2. summarize desired upstream gains in each area
3. summarize required local capabilities in each area
4. identify conflicts, overlaps, and obsolete assumptions
5. design the target combined behavior for that area
6. implement that target design across whatever files are appropriate

### Required Agent Behavior

For each affected area, the agent must:

1. read the upstream changes
2. read the local fork changes
3. understand the relevant docs and invariants
4. decide what the best combined behavior should be
5. implement that combined behavior even when it requires a new file shape
6. update tests and docs when the synthesized design changes behavior or structure
7. stop and explain blockers when the intended combined behavior is ambiguous

### Success Criteria

A reconciliation run succeeds only if all of these are true:

1. desired local capabilities are preserved
2. desired upstream improvements are incorporated
3. invariants remain true
4. verification proves the combined behavior sufficiently
5. the landed code reflects the best combined implementation, not merely the easiest textual merge

## Core Decision

The stack is split into two layers:

1. per-tool source forks
   These hold the actual modified source code for major tools like `br`,
   `mcp_agent_mail`, and `ntm`.
2. overlay control plane
   This repo owns manifests, policies, wrappers, installers, sync tooling,
   update orchestration, state schemas, and operator documentation.

That split preserves upstream mergeability without forcing all source into one
huge custom monorepo.

## What Lives Where

### In Each Custom Tool Fork

Each fork should own:

1. source code
2. tests
3. short `README.md` note about local differences
4. `merge.md`
5. `docs/LOCAL_CHANGES.md`
6. `docs/UPSTREAM_SYNC.md`
7. `docs/SYNC_INVARIANTS.md`
8. `sync/customizations.json`
9. `sync/upstream-base.txt`
10. `sync/upstream-decisions.json`

### In This Overlay Repo

This repo should own:

1. `stack-sync.manifest.json`
2. `flywheel-sync`
3. manifest schema and migration tooling
4. release and install policy for the local stack
5. the agent-assisted reconciliation workflow
6. bootstrap templates and validation rules for adopting new customized forks
7. local, uncommitted runtime state under the sync state root

## Runtime State Model

The system should distinguish three kinds of information clearly.

### Declarative Policy

The manifest is the durable policy contract.

It should describe:

1. what tool is managed
2. where its fork lives
3. how sync, install, publish, retention, and verification should behave
4. which reconciliation workers are preferred and what capabilities are required

It should not be rewritten for every run.

### Runtime Execution State

Operational state should live under the local `state/` root used by
`flywheel-sync`.

That state should be:

1. machine-readable
2. uncommitted
3. crash-safe
4. resumable
5. content-addressed where practical
6. rich enough to reconstruct what happened during a run and help an operator repair a bad landing

External planning systems may be integrated, but they are not the canonical
runtime ledger for sync execution.

If a tool uses a bead graph or similar planning graph, that graph should inform
work decomposition, operator review, or ledger authoring rather than serve as
the only execution-state record for the sync lifecycle.

### Human-Facing Current-State Docs

`README.md` should describe the accepted current state of the customized fork.

That means:

1. transient run progress does not belong in `README.md`
2. accepted behavior or workflow changes do belong in `README.md`
3. updating `README.md` is part of accepting a reconciliation when user-visible behavior changes
4. the overlay `README.md` should distinguish current implementation from planned evolution so operators are not misled

### Runtime State Layout

Suggested layout:

1. `state/locks/` for per-tool lifecycle locks
2. `state/runs/<tool>/<run-id>/` for run metadata and lightweight reports
3. `state/artifacts/` for content-addressed evidence bundles and accepted artifacts
4. `state/cache/` for reusable diff, build, and verification caches
5. `state/index/` for fast status and drift summaries
6. `state/gc/` for retention and garbage-collection bookkeeping

## Command Surface Plan

The current subcommands are low-level building blocks.

The future user-facing command surface should add:

1. `flywheel-sync bootstrap <tool>` to adopt an existing fork or start a new managed customization
2. `flywheel-sync sync <tool>` as the high-level orchestration command for a normal reconciliation run
3. `flywheel-sync status [tool]` to summarize the current state of managed tools
4. `flywheel-sync drift [tool]` to show upstream drift, undocumented fork drift, and verification drift
5. `flywheel-sync review <tool>` to render the evidence bundle, synthesis summary, and verification report
6. `flywheel-sync accept <tool>` to record the operator decision and update the real fork intentionally
7. `flywheel-sync repair <tool>` to prepare a human-guided repair workflow from accepted runtime artifacts
8. `flywheel-sync rollback <tool>` to create a non-destructive rollback or revert path
9. `flywheel-sync publish <tool>` when the tool is distributed as a fork release rather than merged source
10. `flywheel-sync doctor <tool>` to explain missing prerequisites, stale docs, capability mismatches, or verification blockers
11. `flywheel-sync resume <tool>` to continue the latest interrupted run from a safe checkpoint
12. `flywheel-sync abort <tool>` to end an in-progress run deliberately and release its lock
13. `flywheel-sync migrate-manifest` to move older manifest versions to the current schema safely
14. `flywheel-sync test-worker <agent>` to verify that a candidate worker can actually perform this workflow safely

## Reconciliation Worker Selection

The generalized workflow should use any agent types available through `ntm`,
not just Codex.

Discovery should work like this:

1. inspect the agent types configured or registered with `ntm`
2. treat the built-in default Flywheel agents as:
   1. Codex (`codex`, `cod`)
   2. Claude Code (`claude`, `cc`)
   3. Gemini CLI (`gemini`, `gmi`)
3. include any additional `ntm` plugin agent types after the built-ins

The default worker preference order should be:

1. Codex
2. Claude Code
3. Gemini CLI
4. any other registered `ntm` agent types

### Capability Model

Selection should not be based on availability alone.

Each worker type should be evaluated against required capabilities such as:

1. non-interactive launch support
2. prompt-file or stdin prompt support
3. stable exit semantics for automation
4. sufficient context handling for the generated evidence bundle
5. ability to edit files in place safely
6. ability to consume Agent Mail or other required MCP tooling when needed
7. timeout and resume characteristics
8. sandbox and network assumptions
9. cost or scarcity class for fallback decisions

The manifest should be able to declare minimum required capabilities for a tool.

### Certification Harness

A registered worker should not be trusted only because it exists in `ntm`.

`flywheel-sync test-worker <agent>` should run a canned reconciliation fixture
that proves the worker can:

1. read both sides of a change
2. synthesize a third implementation
3. update tests when needed
4. stop and explain uncertainty instead of guessing blindly

### Selection Policy

The selection policy should be:

1. if the user explicitly passes `--merge-agent` or `--merge-agents`, honor that ordered preference first
2. otherwise, consider the manifest preference list
3. otherwise, consider the default preference order
4. in all cases, filter the candidate list by required capabilities before choosing a worker
5. only treat a worker as eligible for the normal path if it has passed the certification harness or an equivalent approved policy
6. if none are eligible, fail with a clear diagnostic explaining what `ntm` currently knows about, which capabilities are missing, and how to configure at least one valid worker

## Evidence Bundle Design

The agent should not receive only raw git diffs and a giant generic prompt.

The evidence bundle should contain both raw evidence and structured synthesis
guidance.

Each bundle should include:

1. upstream diff and logs
2. local diff and logs
3. manifest snapshot
4. repo status snapshot
5. `merge.md`
6. docs and machine-readable customization data
7. generated reconciliation packets grouped by subsystem or behavior area
8. a worker-specific prompt that explains the synthesis task

### Prompt Contract

The generated worker prompt must not merely mention that `merge.md` exists.

It must explicitly instruct the worker to:

1. read `merge.md` before making any reconciliation decisions
2. treat `merge.md` as binding fork-specific direction for how synthesis should be performed
3. use `merge.md` together with invariants, customization data, and tests when deciding the target combined behavior
4. stop and report a blocker if `merge.md` is missing, contradictory, or too incomplete to guide a safe synthesis
5. avoid starting implementation until it has incorporated the `merge.md` guidance into its plan for the affected packets

The prompt should also require the worker to produce a short synthesis summary
that cites how `merge.md` influenced the chosen design.

### Reconciliation Packets

Each packet should describe one affected area and include:

1. packet ID
2. changed files in that area
3. summary of desired upstream gains
4. summary of required local capabilities
5. linked invariants
6. linked tests and golden outputs
7. risk notes and likely conflict zones
8. open questions or ambiguity markers if the tool can detect them

The packet is the right unit of work because the tool is reconciling behavior,
not blindly replaying file edits.

## Run State Machine

Every run should be represented by a machine-readable record and one explicit
lifecycle state.

Core states:

1. `bootstrap`
2. `prepared`
3. `worker_running`
4. `worker_blocked`
5. `verify_pending`
6. `verification_failed`
7. `verified`
8. `accept_pending`
9. `accepted`
10. `published`
11. `rolled_back`
12. `aborted`

State transitions should be:

1. explicit
2. idempotent
3. written before and after risky operations
4. resumable after crashes or interrupted terminals
5. easy to inspect through `status` and `doctor`

### Locking Rules

The runtime state should enforce one active lifecycle lock per managed tool.

That means:

1. `bootstrap`, `sync`, `prepare`, `accept`, `repair`, and `rollback` acquire a lock
2. `resume` renews the existing lock if the same run is continued
3. stale locks are recoverable through explicit `resume` or `abort`
4. read-only commands like `status`, `drift`, and `review` do not need exclusive locks

### Storage Discipline

The runtime state should have one transactional source of truth.

The recommended design is:

1. a small transactional database or append-only event log as the canonical run ledger
2. derived JSON or markdown views for human inspection where helpful
3. content-addressed artifact storage referenced from the canonical ledger

Per-tool planning graphs may be referenced or mirrored into derived views, but
they should not replace the canonical run ledger.

This avoids partial-state corruption across crashes, cleanup, and schema changes.

## Sync Model

Every customized tool should follow one lifecycle from adoption through
recurring upstream reconciliation.

### Phase 0: Bootstrap / Adopt

`flywheel-sync bootstrap <tool>` should:

1. register or create the tool entry in the manifest
2. record the fork repo path, upstream remote, lifecycle mode, install mode, retention policy, and worker policy
3. detect the initial upstream base or ask the operator to confirm it
4. scaffold missing docs:
   1. `README.md` local-differences section
   2. `merge.md`
   3. `docs/LOCAL_CHANGES.md`
   4. `docs/UPSTREAM_SYNC.md`
   5. `docs/SYNC_INVARIANTS.md`
   6. `sync/customizations.json`
   7. `sync/upstream-base.txt`
   8. `sync/upstream-decisions.json`
5. infer an initial customization ledger from the current fork diff, changed path clusters, and git history where possible
6. suggest default tests and smoke tests from the tool's existing build metadata
7. identify likely risk zones and high-conflict files from historical divergence
8. discover which worker types are available through `ntm`
9. write a bootstrap report listing what was inferred, what still needs operator input, which workers are available, which capabilities are missing, and what is currently unsafe to automate

### Phase 1: Prepare

`flywheel-sync prepare <tool>` should remain the expert entry point.

The future `flywheel-sync sync <tool>` command should call it as the first stage
of the normal lifecycle.

Prepare should:

1. validate the manifest entry against the current schema
2. acquire the tool lifecycle lock
3. fetch `origin` and `upstream`
4. resolve the accepted upstream frontier from `sync/upstream-base.txt` and `sync/upstream-decisions.json`
5. compute:
   1. upstream delta: frontier -> upstream tip
   2. local fork delta: frontier -> origin tip
6. compute stable input hashes for diffs, docs, verification profile, and worker profile
7. reuse cached heavy artifacts when those hashes match a prior run
8. create a disposable worktree from the current origin branch
9. snapshot the declared markdown docs and customization data
10. generate reconciliation packets grouped by subsystem or behavior area
11. resolve the worker to use from:
    1. explicit CLI override
    2. manifest preference list
    3. default priority order
12. generate a worker-specific synthesis prompt that explicitly directs the worker to read `merge.md` first and treat it as binding fork-specific merge guidance
13. write the run record, transition the run to `prepared`, and emit an operator summary

### Phase 2: Synthesis-First Reconciliation

This is the heart of the system.

The selected worker is used as a reconciliation worker, not as a policy source.

The worker must treat every reconciliation packet as a synthesis task.

For each packet, the worker should:

1. identify the desired upstream gains
2. identify the required local capabilities
3. identify which assumptions from either side are now obsolete
4. design the target combined behavior
5. decide what code structure best implements that target behavior
6. implement that code structure across whatever files are appropriate
7. update tests and docs when the structure or behavior changes
8. stop and explain blockers when the desired combined behavior is ambiguous

The worker is explicitly allowed to:

1. keep an upstream implementation unchanged when that is the best result
2. keep a local implementation unchanged when that is the best result
3. write a synthesized third implementation that is better than either source version
4. restructure files when that is needed to realize the best combined design
5. remove code that is made obsolete by the combined design

The worker is explicitly not allowed to:

1. choose a side per file as its default strategy
2. ignore one side because the other is newer or cleaner
3. preserve textual form while losing required behavior
4. claim success because conflicts disappeared

If the worker blocks, the run should move to `worker_blocked` with:

1. the unresolved packet IDs or invariants
2. the reason the worker stopped
3. the exact prompt and capability profile used
4. the next safe operator action

### Phase 3: Verification

`flywheel-sync check <tool>` should run the tool's declared verification plan
inside the prepared worktree and write a structured verification report.

Verification should support tiers:

1. `quick` for fast local sanity checks
2. `standard` for the normal acceptance gate
3. `full` for expensive or release-grade verification

Verification should also:

1. distinguish required checks from advisory checks
2. fail closed when a managed or enforced tool has no required verification configured
3. map each invariant to proving checks or explicitly report that the invariant remains unproven
4. map each reconciliation packet to at least one proving check or explicit manual review item
5. emit machine-readable output so the report can feed ACFS and future dashboards
6. support timeouts, retry policy, and flake classification
7. allow safe parallel execution where commands are independent
8. reuse build or dependency caches when that does not compromise correctness
9. capture enough artifact references for `review`, `repair`, and `explain-landed`

No run is considered ready until:

1. required verification passes for the selected acceptance tier
2. the synthesized result is reviewed
3. the changes are merged into the real fork intentionally

### Phase 4: Review / Accept / Publish

The generalized workflow needs an explicit acceptance phase rather than leaving
that step as undocumented operator behavior.

`flywheel-sync review <tool>` should summarize:

1. upstream changes being absorbed
2. declared local customizations being preserved
3. the synthesized design choices made by the worker
4. unresolved risk areas
5. verification results by tier, packet, and invariant
6. the exact worker, capability profile, and prompt bundle used

`flywheel-sync accept <tool>` should:

1. require explicit operator approval
2. verify that the target branch tip has not moved unexpectedly since preparation and verification
3. land the accepted worktree only when the current branch state still matches the verified assumptions
4. record enough local state to reconstruct and repair the landing if the worker made a bad fold
5. update `README.md` when accepted behavior or workflow changed
6. write the final review artifact set for auditability
7. optionally publish a fork release when the tool is distributed from release artifacts instead of source

Direct merge is the default landing model for this project.

That means the repair path matters more than PR metadata:

1. save the pre-merge commit
2. save the accepted worktree or diff artifact
3. save the exact worker and prompt bundle used
4. save verification results and operator acceptance summary
5. make it easy for a human to compare `README.md`, accepted artifacts, and landed code if something looks wrong
6. never rely on destructive history rewriting as the primary recovery mechanism

### Phase 5: Post-Land Validation, Repair, And Rollback

The system should verify not just the prepared worktree but the landed or
installed outcome where relevant.

`flywheel-sync validate-landed <tool>` should:

1. confirm the landed commit or installed artifact matches the accepted run record
2. run post-land or post-install smoke checks where the distribution model requires it
3. record whether the delivered tool, not just the repo state, is healthy

`flywheel-sync repair <tool>` should:

1. recreate a repair worktree from the accepted artifacts and landed commit
2. explain the delta between the intended landing and the observed landed state
3. give the operator a safe path to patch or re-run a synthesis without losing auditability

`flywheel-sync rollback <tool>` should:

1. default to a non-destructive rollback strategy such as a revert commit or repair branch
2. preserve the accepted run record and artifact references
3. record whether the landing was fully reverted, partially repaired, or superseded by a follow-up fix

### Phase 6: Record The New Frontier And Install State

After the fork has absorbed the reconciliation successfully, run:

```bash
flywheel-sync mark-synced <tool>
```

That should:

1. update `sync/upstream-base.txt` only to the last contiguous accepted upstream frontier
2. update `sync/upstream-decisions.json` with accepted, deferred, or rejected upstream decisions when needed
3. commit the frontier marker and any accepted decision data in the fork repo alongside the landed reconciliation
4. update local runtime state indicating the last accepted run, landed commit, install mode, worker used, and rollback status

## Required Per-Fork Documentation And Contracts

### `README.md`

Must include a short section:

`Local Differences From Upstream`

It should say:

1. what changed
2. why it changed
3. whether the change is policy, workflow, or implementation
4. what an operator should expect to be different today

### `merge.md`

This file is mandatory.

It should give the worker direct instructions on how to reconcile upstream and
local changes for this fork.

It should include:

1. a direct statement that the worker must synthesize the best combined implementation rather than choose one side per file
2. local priorities when upstream and local behavior conflict
3. examples of combinations that are preferred over either source version alone
4. anti-patterns the worker must avoid for this fork
5. subsystem-specific merge heuristics that are too specific to fit cleanly in generic manifest fields

This file should be treated as a high-priority instruction input during every
reconciliation run and should be included in every evidence bundle.

### `docs/LOCAL_CHANGES.md`

Must explain:

1. each major customization
2. why it exists
3. what files or subsystems it touches
4. what tests prove it still works

### `docs/UPSTREAM_SYNC.md`

Must explain:

1. how to reconcile from upstream
2. where conflicts are likely
3. what must never be regressed
4. what verification tiers are required before accepting a run
5. which repair or rollback paths are preferred if a bad synthesis lands

### `docs/SYNC_INVARIANTS.md`

Must list the non-negotiable properties that every future run must preserve.

Each invariant should identify:

1. invariant ID
2. severity
3. scope
4. human rationale
5. proving checks or expected evidence

### `sync/customizations.json`

This is the machine-readable customization ledger.

A per-tool bead graph is a reasonable authoring surface for this ledger when the
graph nodes map cleanly to customizations, invariants, proving checks, or
deferred upstream decisions.

However, the graph should compile or export into stable fork-local artifacts
such as `sync/customizations.json` and `sync/upstream-decisions.json`.

The graph itself should not be the only durable policy representation because
`flywheel-sync` must remain tool-agnostic and must be able to validate the fork
from committed fork-local contracts plus live git state.

Each entry should capture:

1. customization ID
2. short summary
3. rationale
4. owned files or subsystems
5. linked invariants
6. expected user-facing behavior or golden outputs when relevant
7. proving tests or check IDs
8. merge risk
9. documentation coverage
10. optional drift fingerprints for changed-path clustering or generated diffs

### `sync/upstream-decisions.json`

This records non-trivial upstream intake decisions.

It should be used when the fork:

1. intentionally defers an upstream change
2. intentionally rejects an upstream change
3. accepts upstream work non-contiguously for a period

This avoids overloading `upstream-base.txt` with meaning it cannot safely carry by
itself.

### Validation States

Validation should classify a tool into one of three states:

1. `bootstrap` - scaffolds exist but operator input is still incomplete
2. `managed` - docs, invariants, and verification are present enough for normal use
3. `enforced` - missing `merge.md`, missing verification, missing invariant coverage, failed worker certification, or undocumented drift is a hard blocker

Graduation between states should be based on explicit criteria, not operator mood.

## Drift Detection

The system should compare the live fork delta against the declared
customization model.

This comparison requires git-derived live delta analysis.

A bead graph or other planning graph can help explain intended
customizations, but it cannot by itself prove that the current fork still
matches that intent.

Drift detection should report:

1. upstream drift not yet evaluated
2. local fork changes not covered by `customizations.json`
3. invariants with no proving checks
4. tests that no longer map cleanly to declared customizations
5. deferred or rejected upstream changes that remain outstanding

Undocumented drift should block `managed` and `enforced` tools from a normal
accept path until the docs or ledger are repaired.

## Manifest Contract

The overlay manifest should declare one object per customized tool.

Each tool entry should include:

1. manifest schema version
2. display name
3. repo path
4. origin remote and branch
5. upstream remote and branch
6. upstream base marker file
7. upstream decision ledger path
8. `merge.md` path
9. markdown doc paths to snapshot
10. customization ledger path
11. verification profiles and required commands
12. advisory smoke tests
13. verification policy
14. lifecycle mode
15. install mode
16. publish mode
17. worker preference list
18. required worker capabilities
19. landing branch and landing strategy
20. cleanliness policy for the target repo
21. artifact retention and cache policy
22. bootstrap template or profile
23. notes for operators

The manifest should remain declarative. It should not accumulate per-run mutable
execution facts that belong in local runtime state.

### Schema And Migration

The manifest contract is large enough that it should be versioned formally.

That means:

1. maintain a JSON Schema for validation
2. require a `manifest_version`
3. version any runtime state schema that `status`, `repair`, and ACFS rely on
4. provide `flywheel-sync migrate-manifest` for safe upgrades
5. fail clearly when the manifest is too old or too new for the current tool

## Artifact Model And Caching

Prepared runs should not regenerate every heavy artifact eagerly.

The system should:

1. key diff and log artifacts by frontier commit, upstream tip, local tip, selected profile, and docs hash
2. store heavy artifacts in a content-addressed area
3. generate expensive artifacts lazily when first needed by `review`, `check`, `repair`, or `explain-landed`
4. pin accepted-run artifacts according to retention policy
5. garbage-collect only non-pinned artifacts from terminal runs

This keeps repeated runs fast without weakening auditability.

## Status, Drift, And Diagnostics

The tool should make current risk visible without requiring operators to inspect
raw JSON by hand.

`flywheel-sync status` should answer:

1. which tools are configured
2. which validation state each tool is in
3. which tool currently has upstream drift
4. which run, if any, is active
5. what the last accepted landing was
6. whether rollback or repair information exists

`flywheel-sync doctor` should answer:

1. why a tool is blocked
2. whether the block is documentation, capability, drift, verification, landing, or state related
3. what the next safe action is

`flywheel-sync drift` should answer:

1. how far upstream has moved since the last accepted frontier
2. whether local fork changes are still covered by the declared customization model
3. whether accepted verification evidence is stale relative to current code

`flywheel-sync explain-landed` should answer:

1. what landed in the last accepted reconciliation
2. which worker and prompt profile produced it
3. what verification proved it
4. what repair path is available if it looks wrong

## ACFS Relationship

### Immediate Operating Rule

Do not let upstream `acfs-update --stack` own tools that are now customized.

Short term operational rule:

1. use `acfs-update --no-stack` for system, runtime, agent, and cloud updates
2. use `flywheel-sync` for customized stack tools

### Long-Term ACFS Plan

Once the workflow is stable, ACFS should be evolved so `--stack` becomes
manifest-driven instead of hardcoded.

Desired future modes:

1. `upstream_installer`
2. `fork_release`
3. `fork_source_sync`
4. `overlay_only`
5. `skip`

At that point, `acfs-update --stack` can remain the one-command operator entry
point, but it will consult the overlay manifest rather than forcing only
upstream installers.

ACFS should consume:

1. declarative policy from the manifest
2. operational facts from the local `flywheel-sync` runtime state

It should not need to parse `README.md` for machine decisions.

## Security And Trust Model

Generalized use means the trust model must be explicit and enforceable.

The system should define:

1. which worker command sources are trusted by default
2. whether direct worker command overrides are allowed, warned, or blocked by policy
3. how secrets and environment variables are redacted from generated bundles
4. whether a worker type is allowed network access during reconciliation or verification
5. what provenance is recorded for accepted runs
6. which data is safe to retain in local runtime state and which must be omitted or redacted

These should be policy knobs the tool can enforce, not only recommendations.

## Safety Model

The workflow should be intentionally conservative.

### Non-Negotiable Rules

1. never treat the tool as a per-file chooser
2. never start a normal reconciliation run without generating a prompt that explicitly tells the worker to read `merge.md` first
3. never let the worker define the invariants it is supposed to preserve
4. never mark a run complete before required verification passes
5. never update the accepted upstream frontier before the accepted reconciliation lands
6. never let a managed or enforced tool silently proceed with missing invariant documentation
7. never let publish or install state drift from what the manifest says should be authoritative
8. never treat `README.md` as transient execution state
9. never accept a direct merge without recording enough runtime state to repair it afterward
10. never use destructive history rewriting as the primary rollback mechanism for a bad accepted run
11. never let a branch advance between verification and acceptance without rechecking whether the verified assumptions still hold

### Preferred Review Artifacts

Every run should leave behind:

1. raw diff evidence
2. reconciliation packets
3. metadata snapshot
4. generated worker prompt
5. verification report
6. final operator decision
7. repair and rollback references for accepted runs

These artifacts should live in the local runtime state area unless intentionally
promoted into committed source.

## Rollout Plan

### Phase 0: Establish The Control Plane

1. add `flywheel-sync`
2. add `stack-sync.manifest.json`
3. document the architecture in this repo

### Phase 1: Make The Mechanism Explicit In Code And Docs

1. generate synthesis-first prompts instead of generic merge prompts
2. add reconciliation packets grouped by subsystem
3. add explicit success criteria based on combined behavior rather than textual merge completion
4. ensure the doc and code never describe the tool as an ours-vs-theirs chooser

### Phase 2: Add Schema, State, And Worker Certification

1. add manifest schema validation and versioning
2. define the runtime state schema and transactional run ledger
3. add per-tool locks, resume, and abort semantics
4. add worker certification and capability checks

### Phase 3: Build Bootstrap, Drift Support, And Contracts

1. implement scaffold generation for the required per-fork docs and `sync/*` files
2. implement manifest authoring helpers so a new user can adopt a tool without manual JSON surgery
3. implement validation states (`bootstrap`, `managed`, `enforced`) with explicit graduation criteria
4. add diff-based customization inference and drift detection
5. add upstream decision tracking for deferred or rejected upstream work
6. add optional import/export paths between planning graphs and the fork-local sync ledgers without replacing diff evidence or the transactional run ledger

### Phase 4: Build Verification And Landing

1. add verification tiers and packet-to-check plus invariant-to-check mapping
2. add timeouts, retry policy, and flake classification
3. add safe parallel execution and cache reuse where appropriate
4. add explicit landing, repair, rollback, and post-land validation

### Phase 5: Normalize The Pattern

1. choose `br` first
2. bring it to `managed`, then `enforced`
3. repeat the same structure for `mcp_agent_mail`
4. repeat it for `ntm`
5. expand to additional stack tools only if they truly need forked source

### Phase 6: Fold Back Into ACFS

1. patch ACFS so stack updates can read the overlay manifest
2. make `acfs-update --stack` dispatch to custom modes safely
3. keep `flywheel-sync` as the lower-level expert tool even after ACFS integration

## What Makes This "Best Of Both Worlds"

This structure preserves both desirable properties:

1. you keep upstream reachability and can still ingest improvements
2. you keep local custom behavior without rewriting it by hand every time
3. new Flywheel users get a repeatable adoption path instead of reverse-engineering your setup
4. users without Codex can still use Flywheel-managed custom forks through other `ntm` agents
5. direct merges stay practical because the repair information lives in local state while `README.md` reflects the current accepted system
6. the workflow is hard to misunderstand because the mechanism is explicitly synthesis-first rather than file-choice-oriented

The critical reason it works is that custom behavior is elevated from tribal
knowledge to explicit policy plus documentation plus machine-readable metadata
plus repairable runtime state.

That is what makes agentic reconciliation safe enough to be useful.
