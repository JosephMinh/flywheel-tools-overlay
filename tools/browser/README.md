# AI Agent Browser Tool

Gives AI agents (Claude Code, Codex, Gemini CLI, etc.) the ability to **view
and interact with any project's UI** via Playwright.

## How it works

```
~/flywheel-tools-overlay/tools/browser/
  browser-server.ts                  ← Playwright HTTP server
  browser.sh                         ← Universal CLI (symlinked into ~/.local/bin and ~/bin)
  browser-allowlist.default.json     ← Global default allowlist

~/.local/bin/browser.sh → symlink    ← On PATH for all agents after install

<any project>/
  .browser-allowlist.json            ← Optional per-project override
  screenshots/                       ← Output (auto-created, gitignored)
  .browser-server.pid                ← Ephemeral (gitignored)
```

The server uses **CWD at startup** for project context, so screenshots
and config are always project-local.

## Security

All navigation is restricted to hostnames in the allowlist:

```json
{ "allowedOrigins": ["localhost", "127.0.0.1", "0.0.0.0"] }
```

- **Network-level blocking**: `page.route("**/*")` aborts all non-allowed requests
- **No eval**: arbitrary JS execution disabled
- **Localhost-only binding**: server on 127.0.0.1, never 0.0.0.0
- **Per-project override**: drop `.browser-allowlist.json` in project root

## Setup by agent

### Claude Code (MCP — pre-configured)

`@playwright/mcp` is in user-level `~/.claude.json` with `--allowed-origins`.
Native tools: `browser_navigate`, `browser_click`, `browser_screenshot`, etc.

### Gemini CLI (MCP)

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--headless",
        "--allowed-origins",
        "http://localhost:3000,http://127.0.0.1:3000"
      ]
    }
  }
}
```

### Codex / Pi / Any CLI agent

```bash
cd ~/ntm_Dev/my-project
browser.sh start
browser.sh navigate http://localhost:3000
browser.sh click "button[type=submit]"
browser.sh screenshot my-page
browser.sh snapshot    # accessibility tree (text, for non-visual agents)
browser.sh stop
```

## Quick reference

| Command | Description |
|---------|-------------|
| `start` / `stop` / `status` | Server lifecycle |
| `navigate <url>` | Go to URL (allowlist enforced) |
| `click <selector>` | Click element |
| `type <selector> <text>` | Fill input |
| `press <selector> <key>` | Keyboard key |
| `screenshot [name]` | Viewport screenshot |
| `screenshot-full [name]` | Full-page screenshot |
| `snapshot` | Accessibility tree as text |
| `hover` / `select` / `scroll` | Other interactions |
| `text` / `html` / `visible` / `count` | Element inspection |

Run `browser.sh help` for full details.

## Per-project allowlist

To allow additional origins for a specific project, create
`.browser-allowlist.json` in the project root:

```json
{
  "allowedOrigins": ["localhost", "127.0.0.1", "staging.internal"]
}
```

## Troubleshooting

- **Server won't start**: `cat .browser-server.log`
- **Browsers not installed**: `npx playwright install chromium`
- **Port conflict**: `BROWSER_PORT=7000 browser.sh start`
- **Playwright not found**: ensure project has `playwright` in devDependencies
