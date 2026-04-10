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
