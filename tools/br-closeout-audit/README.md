# br-closeout-audit

Generic Beads closeout auditor with global defaults plus optional per-project overrides.

It audits whether a closed bead has enough implementation and verification
evidence behind it, using:

- `br show --json` for issue metadata and close reasons
- `bv --robot-history` for bead-to-commit correlation
- `git log` / `git show` for fallback commit evidence and file touches

## Install shape

- Tool code: `/home/ubuntu/flywheel-tools-overlay/tools/br-closeout-audit/`
- Launcher: `/home/ubuntu/.local/bin/br-closeout-audit`

## Usage

Audit specific beads:

```bash
br-closeout-audit --issue hr-j04f.1 --issue hr-j04f.9
```

Audit recently closed beads in the current repo:

```bash
cd /home/ubuntu/ntm_Dev/HR_dashboard
br-closeout-audit
```

Audit beads whose close evidence falls in a git range:

```bash
br-closeout-audit --since-rev HEAD~30
```

JSON output:

```bash
br-closeout-audit --issue hr-j04f.1 --format json
```

## Project policy

Global defaults live alongside the tool in:

- `/home/ubuntu/flywheel-tools-overlay/tools/br-closeout-audit/config.json`

Optional repo-local overrides are loaded automatically when present:

- `.ntm/closeout-audit.json`
- `.beads/closeout-audit.json`
- `.br-closeout-audit.json`

Repo-local policy files can override any default keys, including labels, path globs, and mode rules.

## What it checks

- closed bead has implementation evidence
- linked commits are not only tracker/meta changes
- expected file paths from the bead description are actually touched
- migration beads show both schema and migration evidence
- verification beads include explicit evidence in the close reason
- blocker dependencies are already closed
- close events done in tracker-only commits are surfaced

## Notes

- The current default policy is intentionally strict. It is meant to catch suspicious closeouts before they become "done" by habit.
- Projects with unusual schema, migration, or test layouts should add a repo-local override file instead of weakening the global defaults.
