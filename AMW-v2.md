# AMW v2 Patch Checklist

Watcher code to patch in this repo:
- `tools/agent-mail-watcher/agent-mail-watcher`
- `tools/agent-mail-watcher/config/config.json`
- `tools/agent-mail-watcher/config/README.md`

Repo sync note:
- This checkout already contains `tools/agent-mail-watcher`.
- `HEAD` matches `origin/main` for the watcher subtree at the time this checklist was written, so patching can happen directly here.

## Goal

Add a beads-aware wake gate to `agent-mail-watcher` with this policy:

- If `open_count == 0`, only `urgent` messages may wake an agent.
- If `open_count > 0` and `ready_count == 0`, only `high` and `urgent` messages may wake an agent.
- Otherwise, keep the current wake behavior.

Non-goals:
- Do not add text heuristics.
- Do not require `ack_required` or `thread_id`.
- Do not move this logic into `ntm`.

## Constraints

- Resolve the target pane first, then inspect the repo state for that pane.
- Read work state from the pane's checkout when possible, not only the canonical project path.
- Use `br` as the source of truth for counts.
- Suppressed-by-policy signals must be treated as processed and must not retry.
- Existing busy suppression, ownership checks, and provider-identity checks must remain intact.
- Text-based prompt-state checks are only for live terminal consumption state, not project work counts. Past-tense recap markers such as `churned for`, `brewed for`, `worked for`, and similar completed-action phrases count as idle history, not active work; treating them as active suppresses legitimate wakes.

## Known Plan Risks And Required Fixes

These are implementation hazards already identified in this patch plan. Do not
start code changes without accounting for them.

### Risk 1: Read-only watcher accidentally mutates `.beads`

Problem:
- Plain `br stats --json` can trigger `br` auto-import or auto-flush behavior.
- The watcher is a read-only observer and must not dirty worktrees or contend
  with agent writes during scans.

Required fix:
- Call `br` in read-only observer mode:
  - `br --json --no-auto-flush --no-auto-import --lock-timeout 250 stats`
- If `br` reports a stale or locked DB in this mode:
  - treat work state as unavailable
  - fail open
  - do not suppress based on uncertain counts

### Risk 2: Re-running `br` for every signal and every binding will be noisy and slow

Problem:
- `scan_once()` can process multiple signals in one pass.
- `command_status()` can inspect many bindings in one run.
- Without caching, the watcher may spawn many duplicate `br` subprocesses for
  the same repo root and increase lock pressure.

Required fix:
- Add a per-run memoization cache keyed by repo root.
- Create the cache once in:
  - `scan_once()`
  - `command_status()`
- Pass it through to work-state helpers and `process_signal()`.
- Reuse a cached unavailable result too, not just successful results.

### Risk 3: Nearest `.beads` lookup can select the wrong repo

Problem:
- Walking upward from `pane.current_path` until a `.beads` directory is found
  can choose the wrong workspace if the pane is inside a nested repo, a sibling
  checkout, or a directory that happens to contain a separate `.beads`.

Required fix:
- Resolve the pane's Git worktree root first with `git rev-parse --show-toplevel`.
- Verify it belongs to the same Git workspace as the canonical `project_key`
  using `git rev-parse --git-common-dir`.
- Only then inspect `.beads` at that selected worktree root.
- If the pane checkout cannot be proven to match the project workspace, fall
  back to the canonical `project_key`.
- Do not use an unbounded nearest-ancestor `.beads` search as the primary selector.

### Risk 4: Self-test state seeding is underspecified and can be flaky

Problem:
- The current self-test checklist says to "seed them using br" but does not
  define how to create:
  - zero-open state
  - open-but-zero-ready state
  - ready state
- If that setup is ambiguous, the policy tests can pass for the wrong reasons.

Required fix:
- Add explicit self-test helpers that create each state deterministically.
- After seeding each repo, assert the expected `br stats --json` counts before
  sending any signal.
- Use explicit `br` operations such as:
  - create + close for zero-open state
  - create + defer or block for open-but-zero-ready state
  - create and leave unblocked for ready state

## Phase 1: Repo And Baseline

- [ ] Confirm the watcher code to patch is still under `tools/agent-mail-watcher`.
- [ ] Confirm `git fetch origin` succeeds and capture the baseline commit SHA.
- [ ] Record current watcher behavior by running:
  - `tools/agent-mail-watcher/agent-mail-watcher self-test`
  - `tools/agent-mail-watcher/agent-mail-watcher status`
- [ ] Confirm `br stats --json` works in a beads repo and returns:
  - `summary.open_issues`
  - `summary.ready_issues`
  - `summary.in_progress_issues`

## Phase 2: Config Surface

File:
- `tools/agent-mail-watcher/agent-mail-watcher`
- `tools/agent-mail-watcher/config/config.json`

Checklist:
- [ ] Extend `Config` with a single new flag:
  - `beads_gate_enabled: bool`
- [ ] Load it in `Config.load()`.
- [ ] Emit it in `Config.to_json()`.
- [ ] Add it to `config/config.json` with the intended default.
- [ ] Decide rollout default:
  - `false` for staged rollout
  - `true` if ready to enable immediately

Notes:
- Keep the config surface minimal.
- Do not add a general policy engine or multiple independent gate toggles in v2.

## Phase 3: Work-State Helpers

File:
- `tools/agent-mail-watcher/agent-mail-watcher`

Add data structures:
- [ ] Add a `ProjectWorkState` dataclass with fields:
  - `repo_root`
  - `source`
  - `available`
  - `open_count`
  - `ready_count`
  - `in_progress_count`
  - `error`

Update existing helpers:
- [ ] Extend `run_command()` to support:
  - `cwd`
  - optional timeout

Add new helpers:
- [ ] `normalize_importance(message: dict[str, Any]) -> str`
- [ ] `git_repo_root(path_value: str) -> str | None`
- [ ] `git_common_dir(path_value: str) -> str | None`
- [ ] `same_git_workspace(path_a: str, path_b: str) -> bool`
- [ ] `find_beads_repo_root(path_value: str) -> str | None`
- [ ] `project_work_state_for_repo_root(repo_root: str, cache: dict[str, ProjectWorkState] | None = None) -> ProjectWorkState`
- [ ] `project_work_state_for_target(pane: PaneInfo | None, project_key: str, cache: dict[str, ProjectWorkState] | None = None) -> ProjectWorkState`

Implementation details:
- [ ] Prefer the resolved pane's checkout root when:
  - the pane has a valid current path
  - the pane path is in the same Git workspace as `project_key`
  - a beads repo exists there
- [ ] Fall back to canonical `project_key` when pane checkout detection fails.
- [ ] Resolve Git worktree root before selecting a beads repo root.
- [ ] Only accept `.beads` at the selected worktree root or canonical project root.
- [ ] Do not use a free nearest-ancestor `.beads` search across unrelated nested repos.
- [ ] Do not parse `.beads/issues.jsonl` for counts.
- [ ] Run `br` in read-only observer mode in the selected repo root:
  - `br --json --no-auto-flush --no-auto-import --lock-timeout 250 stats`
- [ ] Parse counts from `.summary`.
- [ ] Memoize results per repo root for the duration of a single `scan_once()` or `status` run.
- [ ] If `br` fails, the repo is not beads-enabled, or the DB is stale or locked:
  - set `available = false`
  - preserve current watcher behavior later

## Phase 4: Beads Gate Decision

File:
- `tools/agent-mail-watcher/agent-mail-watcher`

Add decision helper:
- [ ] `beads_gate_decision(importance: str, work_state: ProjectWorkState) -> tuple[str, str | None]`

Expected return values:
- [ ] `wake`
- [ ] `suppress-no-open-beads`
- [ ] `suppress-no-ready-beads`
- [ ] `skip-policy-unavailable`

Policy rules:
- [ ] If `work_state.available` is `false`, return `wake`.
- [ ] If `open_count == 0`:
  - return `wake` only for `urgent`
  - suppress `low`, `normal`, and `high`
- [ ] If `open_count > 0` and `ready_count == 0`:
  - return `wake` for `high` and `urgent`
  - suppress `low` and `normal`
- [ ] Otherwise return `wake`

Notes:
- `high` should not break through when there are zero open beads.
- Unknown importance should normalize to `normal`.

## Phase 5: Integrate Into Signal Processing

File:
- `tools/agent-mail-watcher/agent-mail-watcher`

Touch function:
- `process_signal(...)`

Checklist:
- [ ] Keep existing project resolution and pane selection first.
- [ ] After pane resolution, compute:
  - normalized importance
  - project work state
  - beads gate decision
- [ ] When `pane is None`, compute work state from canonical `project_key` only.
- [ ] Apply the beads gate before:
  - `build_prompt()`
  - `deliver_prompt_to_live_pane()`
  - `build_launch_command()`
  - `create_tmux_pane()`
- [ ] If the decision is suppression:
  - emit action `suppressed-no-open-beads` or `suppressed-no-ready-beads`
  - set `delivered = True`
  - do not wake
  - do not launch
  - do not auto-create a pane
- [ ] If the decision is `wake`, continue through existing logic unchanged.

Event payload additions:
- [ ] Add `message_importance`
- [ ] Add `beads_gate_enabled`
- [ ] Add `beads_gate_decision`
- [ ] Add `beads_gate_reason`
- [ ] Add `work_state_repo_root`
- [ ] Add `work_state_source`
- [ ] Add `work_state_available`
- [ ] Add `work_state_open_count`
- [ ] Add `work_state_ready_count`
- [ ] Add `work_state_in_progress_count`
- [ ] Add `work_state_error`

Retry semantics:
- [ ] Confirm `scan_once()` treats suppressed-by-policy events as terminal because `delivered = True`.
- [ ] Do not introduce retry loops for policy suppression.
- [ ] Create one `work_state_cache` in `scan_once()` and pass it into each `process_signal()` call.

## Phase 6: Status Output

File:
- `tools/agent-mail-watcher/agent-mail-watcher`

Touch function:
- `command_status(...)`

Checklist:
- [ ] Compute work state for each resolved binding.
- [ ] Reuse a per-run `work_state_cache` so duplicate bindings in the same repo do not re-run `br`.
- [ ] Surface the work-state block in status JSON:
  - `work_state_repo_root`
  - `work_state_available`
  - `work_state_open_count`
  - `work_state_ready_count`
  - `work_state_in_progress_count`
- [ ] Add policy-specific booleans:
  - `normal_wake_allowed`
  - `high_wake_allowed`
  - `urgent_wake_allowed`
- [ ] Update `wake_deliverable` semantics only if necessary.

Recommended interpretation:
- [ ] Keep `wake_deliverable` meaning "pane/provider is technically wakeable".
- [ ] Add separate beads-policy fields rather than overloading `wake_deliverable`.

## Phase 7: Config And Docs

Files:
- `tools/agent-mail-watcher/config/config.json`
- `tools/agent-mail-watcher/config/README.md`

Checklist:
- [ ] Document the new `beads_gate_enabled` flag.
- [ ] Document the policy in plain language.
- [ ] Document that work state comes from `br stats --json`.
- [ ] Document that the watcher uses the target pane's checkout when possible.
- [ ] Document suppression event names.
- [ ] Document that policy suppression is terminal and does not retry.

## Phase 8: Self-Test Coverage

File:
- `tools/agent-mail-watcher/agent-mail-watcher`

Touch function:
- `command_self_test(...)`

Add helper support inside self-test:
- [ ] Create temporary beads repos in self-test workspaces.
- [ ] Seed them using `br` so counts are real.
- [ ] Add a helper like `seed_self_test_beads_state(repo_root: pathlib.Path, scenario: str) -> dict[str, int]`.
- [ ] After seeding each scenario, assert `br --json --no-auto-flush --no-auto-import stats` returns the expected counts before any signal is written.

Required test cases:
- [ ] `open_count == 0` and `normal` signal -> `suppressed-no-open-beads`
- [ ] `open_count == 0` and `high` signal -> `suppressed-no-open-beads`
- [ ] `open_count == 0` and `urgent` signal -> normal wake path still works
- [ ] `open_count > 0` and `ready_count == 0` and `normal` signal -> `suppressed-no-ready-beads`
- [ ] `open_count > 0` and `ready_count == 0` and `high` signal -> normal wake path still works
- [ ] `ready_count > 0` and `normal` signal -> normal wake path still works
- [ ] unavailable beads state -> watcher fails open and preserves existing behavior
- [ ] policy suppression does not auto-create a pane for an unowned shell-only target

Verification assertions:
- [ ] Suppressed events never inject prompt text.
- [ ] Suppressed events never launch shell commands.
- [ ] Suppressed events set `delivered = True`.
- [ ] Suppressed events are logged with the new work-state fields.
- [ ] Unavailable, stale, or locked beads state fails open instead of suppressing.

## Phase 9: Manual Validation

Use real watcher commands after patching:
- [ ] `tools/agent-mail-watcher/agent-mail-watcher self-test`
- [ ] `tools/agent-mail-watcher/agent-mail-watcher scan-once`
- [ ] `tools/agent-mail-watcher/agent-mail-watcher status`

Manual scenarios:
- [ ] Bound idle pane in a repo with zero open beads:
  - send `normal`
  - send `high`
  - send `urgent`
- [ ] Bound idle pane in a repo with open but zero ready beads:
  - send `normal`
  - send `high`
  - send `urgent`
- [ ] Bound idle pane in a repo with ready beads:
  - send `normal`
- [ ] Working pane in each state above:
  - confirm existing `suppressed-working-pane` behavior remains intact

Inspect:
- [ ] `events.jsonl`
- [ ] `state.json`

Expected results:
- [ ] Policy suppressions are terminal.
- [ ] Existing technical defers still retry only when they already did before.
- [ ] Busy suppression still behaves exactly as before.

## Phase 10: Optional Hardening

Recommended if time permits:
- [ ] Add a worktree-specific validation case where the canonical project path and pane checkout differ.
- [ ] Verify the watcher chooses the pane checkout's beads state, not the canonical root's state.

This can be done either:
- [ ] in `self-test` if maintainable
- [ ] as a separate manual validation script

## Phase 11: Rollout

Checklist:
- [ ] Patch the code in this repo first.
- [ ] Run self-test and manual validation here.
- [ ] Commit and push this repo.
- [ ] After the remote repo is updated, pull the finished watcher into `~/tools/agent-mail-watcher`.
- [ ] Restart the user service after deployment:
  - `systemctl --user restart agent-mail-watcher.service`

## Acceptance Criteria

- [ ] Watcher blocks non-urgent wakes when a project has no open beads.
- [ ] Watcher blocks low and normal wakes when a project has open but no ready beads.
- [ ] Watcher still allows urgent wakes after completion.
- [ ] Watcher still allows high wakes when work exists but nothing is ready.
- [ ] Suppressed-by-policy signals do not retry.
- [ ] Existing pane ownership, provider identity, busy suppression, and bootstrap flows remain intact.
- [ ] Status and event logs expose enough state to explain every beads-gate decision.
