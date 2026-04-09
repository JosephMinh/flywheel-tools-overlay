# flywheel-tools-overlay

Personal overlay repository for home-level tools that should not be overwritten by weekly Agentic Coding Flywheel updates.

## Scope

This repo snapshots the current `~/tools` tree into `tools/` and keeps those tools outside upstream repos such as `ntm`.

Current focus:
- `tools/agent-mail-watcher`
- `tools/br-closeout-audit`
- `tools/browser`

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
- sync `tools/` into `~/tools`
- recreate `~/.local/bin/<tool>` symlinks when a tool has a matching executable
- recreate `~/.config/<tool>` and `~/.local/state/<tool>` symlinks when those directories exist
- recreate `~/.config/systemd/user/<tool>.service` symlinks for tools that ship user units
- run `systemctl --user daemon-reload` if available

The installer is intentionally non-destructive:
- it does not delete extra files already present under `~/tools`
- it does not stop or restart services automatically

## Update Workflow

1. make changes in the live `~/tools/...` tree
2. mirror them back into this repo's `tools/` directory
3. commit and push this repo
4. on another machine or after a VPS rebuild, clone this repo and run `./install.sh`

## Notes

- This repo currently includes the full `tools/` snapshot as requested, including live state and logs.
- If you later want a cleaner split between durable source and runtime state, the next step is to move volatile state files behind `.gitignore` and preserve them only on the live machine.
