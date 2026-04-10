# flywheel-tools-overlay

Personal overlay repository for home-level tools that should not be overwritten by weekly Agentic Coding Flywheel updates.

## Scope

This repo is the canonical home for personal flywheel tools. Tools live directly in this checkout under `tools/`, outside upstream repos such as `ntm`.

Current focus:
- `tools/agent-mail-watcher`
- `tools/br-closeout-audit`
- `tools/browser`
- `tools/ntm-bootstrap`

Intentional non-scope for now:
- pending `ntm` source changes are not included here yet

## Layout

```text
flywheel-tools-overlay/
├── tools/
│   └── ...
├── install.sh
└── README.md
```

## Install

From the repo root:

```bash
./install.sh
```

That will:
- recreate `~/.local/bin/<tool>` and `~/bin/<tool>` symlinks for top-level executable files
- recreate `~/.config/<tool>` and `~/.local/state/<tool>` symlinks when those directories exist
- recreate `~/.config/systemd/user/<tool>.service` symlinks for tools that ship user units
- run `systemctl --user daemon-reload` if available

The installer is intentionally non-destructive:
- it does not stop or restart services automatically
- it does not copy tool trees anywhere else, because this repo is the source of truth

## Update Workflow

1. make changes directly in this repo's `tools/` directory
2. commit and push this repo
3. on another machine or after a VPS rebuild, clone this repo and run `./install.sh`

## Notes

- This repo currently includes the full tool trees as requested, including live state and logs.
- Runtime-only watcher artifacts such as bindings, locks, event logs, and backups are intentionally gitignored so the repo stays clean while the tools run.
- If you later want a cleaner split between durable source and runtime state, the next step is to move volatile state files behind `.gitignore` and preserve them only on the live machine.
