# Agent Mail Watcher

This watcher is the user-level wakeup bridge for Agent Mail. It must work without local `ntm` source changes.

## What It Must Do

- Poll Agent Mail signals and deliver wake prompts only when an agent is idle or waiting for input.
- Suppress wake prompts when the pane is already working.
- Treat killed panes as dead: prune stale bindings and stop wake delivery to them.
- Scrub transient Codex session variables out of the tmux server environment so new panes do not inherit a stale shared session identity.
- Auto-discover live `ntm` worker panes from `~/.config/ntm/sessions` and `~/.ntm/sessions`.
- Auto-register newly discovered `ntm` worker panes in Agent Mail if they are not already registered.
- Auto-bind newly discovered `ntm` worker panes so future mail can wake them immediately.
- Reuse existing registry mappings when a pane already has an assigned Agent Mail identity.
- Refresh live pane identity aliases and tmux binding metadata so stale ownership files do not drift.
- Auto-respawn idle conflicted `ntm` Codex worker panes when multiple live panes share the same Codex session thread.
- Auto-restore the shared user-level `agent-mail.service` if `127.0.0.1:8765` goes down unexpectedly.

## Current NTM Reconciliation Model

- Read `agent.json` and `agent_registry.json` from each live `ntm` session.
- Match live tmux panes to `ntm` worker pane titles such as `Session__cod_2`.
- Map worker type codes to Agent Mail programs:
  - `cc` -> `claude-code`
  - `cod` -> `codex-cli`
  - `gmi` -> `gemini-cli`
  - `cursor` -> `cursor`
  - `windsurf` -> `windsurf`
  - `aider` -> `aider`
- Create Agent Mail identities only when no registry entry exists yet.
- Update `agent_registry.json` and `bindings.json` only when the watcher actually learned something new.

## Root Cause Guardrails

- The real failure mode was tmux inheriting `CODEX_THREAD_ID` from a Codex-driven shell.
- A separate recurring VPS failure mode was the shared Agent Mail service disappearing on port `8765` and leaving every client dead until somebody restarted it manually.
- If tmux keeps that global variable, newly spawned `ntm` worker panes can come up attached to the same underlying Codex session even when their pane titles and Agent Mail names differ.
- The watcher now removes these transient tmux globals on every scan:
  - `CODEX_THREAD_ID`
  - `CODEX_CI`
  - `CODEX_MANAGED_BY_BUN`
- The user-level `compaction-recovery` wrapper also strips `CODEX_THREAD_ID` before launching Codex as defense in depth.
- Wake prompts are intentionally simple now. They tell the agent there is new mail and leave routing/ownership enforcement to the watcher.
- Why: asking the receiving agent to re-prove ownership inside the prompt created false failures and operator confusion even when wake routing was already correct.
- The watcher now treats the localhost Agent Mail service as shared infrastructure and attempts a user-level `systemctl --user start agent-mail.service` when `127.0.0.1:8765` is unexpectedly unreachable.

## Wake Rules

- Wake when `prompt_state` is `idle_prompt`.
- Wake when `prompt_state` is `awaiting_input`.
- Do not wake when `prompt_state` is `working`.
- Do not wake when no owned live pane exists.
- Do not wake a Codex pane if the watcher still detects a shared-provider-thread conflict.
- Do not wake a Codex pane if the watcher detects any provider-identity issue, including:
  - a shared-provider-thread conflict across multiple live panes
  - a leftover legacy `CODEX_THREAD_ID` on a single pane after the global scrub fix was installed
- If that issue is found while the pane is idle, the watcher should repair it by respawning the pane with its existing launcher command before later wake delivery.

## Beads Gate

The watcher can also suppress non-urgent wakes when the target project has no actionable bead work. This is feature-gated behind a single config flag and only applies on top of the rules above; busy suppression, ownership checks, and provider-identity checks all run first.

### `beads_gate_enabled`

Boolean in `config.json`. Defaults to `false` for staged rollout.

- `false`: legacy behavior. Every wake follows the existing pre-v2 path; bead state is not consulted.
- `true`: the watcher reads bead counts from `br stats --json` (read-only observer mode) for each target pane's project and applies the policy below before delivering a wake.

### Wake policy matrix

| Project state                              | low  | normal | high | urgent |
| ------------------------------------------ | ---- | ------ | ---- | ------ |
| ready (open ≥ 1, ready ≥ 1)                | wake | wake   | wake | wake   |
| open but zero ready (open ≥ 1, ready = 0)  | drop | drop   | wake | wake   |
| zero open (open = 0)                       | drop | drop   | drop | wake   |
| unavailable (br read failed, locked, etc.) | wake | wake   | wake | wake   |
| disabled (`beads_gate_enabled=false`)      | wake | wake   | wake | wake   |

- `wake` runs the existing wake path. `drop` logs `suppressed-no-open-beads` or `suppressed-no-ready-beads` and treats the signal as delivered (no retry, no auto-create).
- `high` does NOT break through when there are zero open beads — only `urgent` does. An empty backlog means there is nothing for the agent to act on, even with a high-importance ping.
- Unavailable bead state always falls back to wake. Suppression on uncertainty is never correct: operators must not lose wakes because `br` was briefly busy or the repo was misclassified.

### Worked examples

- A project with `open=8, ready=3` receives a `normal` wake → delivered.
- A project with `open=8, ready=0` receives a `normal` wake → suppressed (`suppressed-no-ready-beads`).
- A project with `open=8, ready=0` receives a `high` wake → delivered.
- A project with `open=0, ready=0` receives a `high` wake → suppressed (`suppressed-no-open-beads`).
- A project with `open=0, ready=0` receives an `urgent` wake → delivered.
- The watcher cannot find a beads repo for the resolved project → wake delivered (fail-open).

### Why the default is `false`

The gate is rolled out behind a flag so the live watcher can be deployed first, then the policy can be enabled per-machine after the operator confirms the new fields in `agent-mail-watcher status` output match expectations for that machine's projects:

- `work_state_repo_root`, `work_state_source`, `work_state_available`
- `work_state_open_count`, `work_state_ready_count`, `work_state_in_progress_count`
- `normal_wake_allowed`, `high_wake_allowed`, `urgent_wake_allowed`
- `beads_gate_explanation` (concise human-readable summary of the active state)

Once those look correct on a machine, set `beads_gate_enabled: true` in that machine's `config.json` and restart the user service.

### State sourcing

The beads-gate policy needs each project's open and ready bead counts. Two rules govern how the watcher reads them.

**Source of truth: `br stats --json` in observer mode**

The watcher invokes:

```
br --json --no-auto-flush --no-auto-import --lock-timeout 250 stats
```

- `--no-auto-flush` and `--no-auto-import` keep the watcher read-only; it must not mutate the bead graph or contend with agent writes during a scan.
- `--lock-timeout 250` keeps scans cheap; a busy DB returns a timeout that the watcher treats as unavailable.
- The watcher never parses `.beads/issues.jsonl` directly. Counts come only from the `summary` block of `br stats`, so the gate respects whatever the daemon currently believes.

**Pane-checkout preference, canonical fallback**

When a wake target is resolved, the watcher prefers the pane's own working directory as the work-state source — but only when it can prove the pane is in the same Git workspace as the project's canonical path (verified via `git rev-parse --show-toplevel` and `git rev-parse --git-common-dir`). This matters for divergent worktrees: a pane on a feature branch worktree may have different open beads than the canonical `main` checkout.

Only when the pane checkout cannot be proven to belong to the same workspace does the watcher fall back to the canonical `project_key`. If neither resolves to a beads repo, the work state is reported as unavailable.

The status output exposes the resolved choice on each binding:

- `work_state_source: "pane-worktree"` — pane checkout used.
- `work_state_source: "canonical-project"` — fell back to canonical path.

**Per-run cache**

One scan or status run can inspect many bindings. The watcher memoizes work-state lookups keyed by repo root within the run, caching both successful and unavailable reads, so duplicate bindings in the same project never spawn a second `br` invocation in the same pass.

### Suppression semantics

When the gate suppresses a wake, the suppression is **terminal**. It is logged once, the signal is marked delivered, and the watcher does not retry. There is no exponential backoff for policy decisions; the signal moves on.

Suppression actions that appear in `events.jsonl`:

- `suppressed-no-open-beads` — project has zero open beads; only `urgent` would have woken.
- `suppressed-no-ready-beads` — project has open beads but zero ready (everything is blocked); only `high` and `urgent` would have woken.

Skip actions used when no policy was applied at all:

- `skip-policy-disabled` — `beads_gate_enabled` was `false` at scan time; the policy did not run.
- `skip-policy-unavailable` — `br` could not return counts; the policy chose to fail open and the existing wake path runs unchanged.

**Fail-open on uncertainty**

Whenever the watcher cannot trust the counts, the gate must not suppress. The conditions that fail open:

- `br` is missing or returned a non-zero exit code.
- `br stats` returned without a `summary` block.
- The DB was locked and the 250 ms lock-timeout expired.
- The pane checkout could not be proven against the canonical project and there was no canonical beads repo either.
- The bead graph response was not parseable JSON.

In every fail-open case, `work_state_available` is `false`, `work_state_error` carries the underlying reason, `beads_gate_explanation` reads `work state unavailable (<error>) — failing open, all wakes proceed`, and the existing wake path runs as if the gate were disabled. The watcher must never lose a wake because of a transient `br` problem.

### Verification stack

Use the verification commands in ascending cost.

| Scope | Command | Typical cost | Use when |
| --- | --- | --- | --- |
| Syntax sanity | `python3 -m py_compile tools/agent-mail-watcher/agent-mail-watcher` | under 1 second | Confirm the watcher script still parses after a local edit. |
| Fast unit and contract sweep | `python3 -m unittest discover -s tools/agent-mail-watcher/tests` | seconds | Main local regression pass. Covers config-flag contracts, policy matrix tests, work-state selection, artifact helpers, e2e smoke checks, and non-goal / guardrail regressions without requiring live tmux. |
| Targeted config and policy contracts | `python3 tools/agent-mail-watcher/tests/test_beads_gate_v2.py` | seconds | Focused pass for `beads_gate_enabled`, disabled-mode compatibility, and status/event field contracts. |
| Targeted guardrail regression checks | `python3 tools/agent-mail-watcher/tests/test_non_goals_and_guardrails.py` | seconds | Prove `ack_required` and `thread_id` stay out of the gate and that busy suppression, ownership checks, and provider-identity helpers remain authoritative. |
| Targeted work-state selection checks | `python3 tools/agent-mail-watcher/tests/test_work_state_selection.py` | seconds | Validate pane-worktree vs canonical fallback behavior without running the live self-test. |
| Scripted e2e matrix | `python3 tools/agent-mail-watcher/tests/run_e2e_scenarios.py` | seconds | Run the tmux-free end-to-end policy matrix for zero-open, open-zero-ready, ready, disabled-mode, unavailable, fail-open, and shell-only-target cases. |
| E2E smoke wrapper | `python3 tools/agent-mail-watcher/tests/test_e2e_runner_smoke.py` | under 2 minutes | Re-run the scripted e2e suite as a subprocess and pin the `summary.json`, `LATEST_RUN`, and read-only observer guarantees. |
| Live tmux self-test | `tools/agent-mail-watcher/agent-mail-watcher self-test` | about 30 seconds or more | Highest-fidelity watcher check. Exercises real prompt delivery, busy suppression, prompt consumption, cross-project routing, shared-session scrubbing, divergent-worktree fallback, pane-worktree preference, and auto-create behavior. |
| Gated self-test unittest | `AMW_RUN_SELF_TEST=1 python3 tools/agent-mail-watcher/tests/test_self_test_invariants.py` | about 30 seconds or more | Run the heavy `self-test` path from `unittest` when you want the tmux-backed validation plus an assertion that `ok=true`, `artifact_scenario_count` is healthy, and the manifest exists. |

Fast local checks are the first five rows. The last three rows are broader rollout gates and should be run before enabling `beads_gate_enabled` on a real machine.

### Artifact locations

- `agent-mail-watcher self-test` writes to `/home/ubuntu/.local/state/agent-mail-watcher-selftest/<run-id>/`.
- The self-test bundle entry point is `/home/ubuntu/.local/state/agent-mail-watcher-selftest/<run-id>/artifacts/manifest.json`.
- Scripted e2e runs write to `tools/agent-mail-watcher/tests/_e2e_scenarios/<run-id>/`.
- `tools/agent-mail-watcher/tests/_e2e_scenarios/LATEST_RUN` is the stable pointer to the most recent e2e run.
- The e2e bundle entry point is `tools/agent-mail-watcher/tests/_e2e_scenarios/<run-id>/summary.json`.

Recommended inspection order after a failure:

1. Open the run-level manifest first.
   Self-test: `artifacts/manifest.json`
   Scripted e2e: `summary.json`
2. Identify the failing scenario id and open that scenario manifest next.
   Self-test: `artifacts/scenarios/<scenario-id>/manifest.json`
   Scripted e2e: `scenarios/<scenario-id>/manifest.json`
3. Inspect the scenario-local watcher outputs.
   Self-test: `watcher-state.json`, `watcher-events-tail.json`, `pane-captures.json`, `command-outcomes.json`
   Scripted e2e: the `first_look_paths` entries in `summary.json`, especially `events`, `watcher_state`, and `scenario_manifest`
4. Use the stable pointer files when you are sharing evidence in notes or mail.
   Self-test: paste the absolute `artifact_manifest` path from the command output
   Scripted e2e: paste `tools/agent-mail-watcher/tests/_e2e_scenarios/LATEST_RUN` plus the referenced `summary.json`

### Rollback and troubleshooting

**Fast rollback**

1. Edit `~/.config/agent-mail-watcher/config.json` and set `"beads_gate_enabled": false`.
2. Restart the watcher service:

```bash
systemctl --user restart agent-mail-watcher.service
```

3. Confirm rollback with `agent-mail-watcher status`.
   The binding should now report `beads_gate_explanation: "policy disabled — pre-v2 wake behavior preserved"` and new wakes should log `skip-policy-disabled` instead of policy suppressions.

**First-response troubleshooting**

| Symptom | Check first | Expected meaning | Next step |
| --- | --- | --- | --- |
| Wake was suppressed with `suppress-no-open-beads` | `events.jsonl`, then the binding in `agent-mail-watcher status` | Project had `open_count=0`; only `urgent` would land | Confirm `work_state_open_count`, `work_state_ready_count`, and `beads_gate_explanation`. If the counts are wrong, move to the artifact manifests below. |
| Wake was suppressed with `suppress-no-ready-beads` | `events.jsonl`, then `agent-mail-watcher status` | Project had open work but nothing ready; only `high` and `urgent` would land | Confirm `work_state_ready_count=0` and inspect the blocker chain in the bead graph. |
| Event shows `skip-policy-unavailable` | `events.jsonl` and `work_state_error` in `status` | The watcher failed open because `br` data was unavailable or untrusted | This is not a suppression. Inspect the error text, then open the latest e2e or self-test manifest to compare against the known fail-open cases. |
| Event shows `skip-policy-disabled` | `agent-mail-watcher status` | Policy was intentionally bypassed because the flag is off | No policy bug. Either keep the rollback in place or re-enable the flag and restart after you finish validating. |
| Signal stays deferred with `deferred-no-owned-pane` or another pre-v2 defer action | `state.json`, then `events.jsonl` | Existing retry semantics are still in control; the gate did not terminally suppress the wake | Check `attempt_count`, `queued_at`, and `retry_exhausted_at` in `state.json` to see whether the watcher is still retrying or has exhausted the signal. |
| Busy pane was not interrupted | `events.jsonl` for `suppressed-working-pane` | Busy suppression guardrail still won over wake delivery | This is expected. The wake is treated as delivered and no prompt should be injected into the busy pane. |

**Manifest-first inspection order**

1. If the problem came from scripted validation, open `tools/agent-mail-watcher/tests/_e2e_scenarios/LATEST_RUN`, then open the referenced `summary.json`.
2. If the problem came from `agent-mail-watcher self-test`, open the `artifact_manifest` path printed by the command, then inspect the failing scenario under `artifacts/scenarios/<scenario-id>/manifest.json`.
3. Use `events.jsonl` and `state.json` after that to confirm whether the watcher treated the signal as terminal suppression, fail-open bypass, or a retryable defer.

## Retry And Log Caps

- An undelivered signal gets one initial processing attempt plus at most `5` retries.
- After the fifth retry fails, the watcher marks that signal exhausted in `state.json` and stops retrying it.
- Older undelivered state entries that predate the retry counter are exhausted on startup so legacy retry loops stop immediately after restart.
- `events.jsonl` is capped at `5000` lines.
- When a new event arrives and the file is already at the cap, the oldest lines are dropped first and the new line is appended, so retention is strict FIFO.

## Files That Matter

- Watcher home: `/home/ubuntu/flywheel-tools-overlay/tools/agent-mail-watcher`
- Watcher launcher: `/home/ubuntu/.local/bin/agent-mail-watcher`
- Watcher config symlink: `/home/ubuntu/.config/agent-mail-watcher`
- Watcher bindings symlink: `/home/ubuntu/.config/agent-mail-watcher/bindings.json`
- Watcher events symlink: `/home/ubuntu/.local/state/agent-mail-watcher/events.jsonl`
- Watcher state symlink: `/home/ubuntu/.local/state/agent-mail-watcher/state.json`
- Compatibility symlink: `/home/ubuntu/.local/bin/agent-mail-watcher`
- Compatibility symlink: `/home/ubuntu/.config/agent-mail-watcher`
- NTM session roots:
  - `/home/ubuntu/.config/ntm/sessions`
  - `/home/ubuntu/.ntm/sessions`

## Fast Validation

1. Run `/home/ubuntu/.local/bin/agent-mail-watcher status` and confirm the target agent is `alive`, bound, and `wake_deliverable: true` when idle.
2. Send a real Agent Mail message to that agent.
3. Check `events.jsonl` for `prompted-live-pane` or `suppressed-working-pane`.
4. If needed, inspect the pane with `tmux capture-pane -p -t <pane-id>`.

## Modification Checklist

- Keep the behavior watcher-owned, not `ntm`-repo-owned.
- Preserve busy suppression.
- Preserve dead-pane pruning.
- Preserve automatic discovery and registration of new `ntm` worker panes.
- Preserve tmux global env scrubbing for transient Codex session vars.
- Preserve live alias/metadata refresh so pane-key and pane-id identity files stay in sync with bindings.
- Preserve idle auto-repair for shared-session Codex worker conflicts.
- Re-run a real wake probe after any change.
