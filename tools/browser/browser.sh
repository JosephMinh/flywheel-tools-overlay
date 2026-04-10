#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AI Agent Browser Tool (Global)
#
# Universal CLI for any AI agent to view and interact with UI via Playwright.
# Run from any project directory — uses CWD for screenshots, PID, and allowlist.
#
# Security: navigation restricted to hostnames in allowlist.
#   Allowlist resolution: CWD/.browser-allowlist.json → global default
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PROJECT_DIR="$(pwd)"
PID_FILE="$PROJECT_DIR/.browser-server.pid"
SERVER_URL="http://127.0.0.1:${BROWSER_PORT:-6780}"
LOG_FILE="$PROJECT_DIR/.browser-server.log"

# Detect best TS runner
if command -v bun &>/dev/null; then
  RUNNER="bun run"
else
  RUNNER="npx tsx"
fi

# ── Helpers ────────────────────────────────────────────────────────────────────

is_running() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

require_running() {
  if ! is_running; then
    echo "Error: Browser server not running. Start it first:"
    echo "  browser.sh start"
    exit 1
  fi
}

send() {
  local cmd="$1"
  local args_json="$2"
  require_running
  curl -s -X POST "$SERVER_URL/command" \
    -H "Content-Type: application/json" \
    -d "{\"command\": \"$cmd\", \"args\": $args_json}"
}

# ── Server lifecycle ──────────────────────────────────────────────────────────

cmd_start() {
  if is_running; then
    echo "Browser server already running (PID $(cat "$PID_FILE"))"
    curl -s "$SERVER_URL/health" 2>/dev/null || true
    return 0
  fi

  echo "Starting browser server for: $PROJECT_DIR"
  cd "$PROJECT_DIR"
  $RUNNER "$TOOL_DIR/browser-server.ts" > "$LOG_FILE" 2>&1 &

  # Wait for server to be ready
  local tries=0
  while [ $tries -lt 30 ]; do
    if [ -f "$PID_FILE" ] && curl -s "$SERVER_URL/health" &>/dev/null; then
      echo "Browser server started (PID $(cat "$PID_FILE"))"
      curl -s "$SERVER_URL/health"
      return 0
    fi
    sleep 0.5
    tries=$((tries + 1))
  done

  echo "Failed to start browser server. Check logs:"
  echo "  cat $LOG_FILE"
  return 1
}

cmd_stop() {
  if is_running; then
    local pid
    pid=$(cat "$PID_FILE")
    kill "$pid" 2>/dev/null || true
    local tries=0
    while [ $tries -lt 10 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 0.3
      tries=$((tries + 1))
    done
    rm -f "$PID_FILE"
    echo "Browser server stopped"
  else
    echo "Browser server not running"
    rm -f "$PID_FILE" 2>/dev/null || true
  fi
}

cmd_status() {
  if is_running; then
    echo "Browser server running (PID $(cat "$PID_FILE"))"
    curl -s "$SERVER_URL/health"
  else
    echo "Browser server not running"
  fi
}

cmd_restart() {
  cmd_stop
  cmd_start
}

# ── Command dispatch ──────────────────────────────────────────────────────────

case "${1:-help}" in
  # Server
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  restart) cmd_restart ;;

  # Navigation
  navigate)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh navigate <url>"; exit 1; }
    send navigate "{\"url\": \"$2\"}"
    ;;
  back)    send back "{}" ;;
  forward) send forward "{}" ;;
  reload)  send reload "{}" ;;
  url)     send url "{}" ;;
  title)   send title "{}" ;;

  # Interaction
  click)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh click <selector>"; exit 1; }
    send click "{\"selector\": \"$2\"}"
    ;;
  type)
    [ -z "${2:-}" ] || [ -z "${3:-}" ] && { echo "Usage: browser.sh type <selector> <text>"; exit 1; }
    send type "{\"selector\": \"$2\", \"text\": \"$3\"}"
    ;;
  press)
    [ -z "${2:-}" ] || [ -z "${3:-}" ] && { echo "Usage: browser.sh press <selector> <key>"; exit 1; }
    send press "{\"selector\": \"$2\", \"key\": \"$3\"}"
    ;;
  hover)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh hover <selector>"; exit 1; }
    send hover "{\"selector\": \"$2\"}"
    ;;
  select)
    [ -z "${2:-}" ] || [ -z "${3:-}" ] && { echo "Usage: browser.sh select <selector> <value>"; exit 1; }
    send select "{\"selector\": \"$2\", \"value\": \"$3\"}"
    ;;
  scroll)
    send scroll "{\"direction\": \"${2:-down}\", \"amount\": ${3:-500}}"
    ;;
  wait)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh wait <selector> [timeout_ms]"; exit 1; }
    send wait "{\"selector\": \"$2\", \"timeout\": ${3:-5000}}"
    ;;

  # Inspection
  screenshot)
    send screenshot "{\"name\": \"${2:-}\"}"
    ;;
  screenshot-full)
    send screenshot "{\"name\": \"${2:-}\", \"fullPage\": true}"
    ;;
  snapshot)
    send snapshot "{}"
    ;;
  text)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh text <selector>"; exit 1; }
    send text "{\"selector\": \"$2\"}"
    ;;
  html)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh html <selector>"; exit 1; }
    send html "{\"selector\": \"$2\"}"
    ;;
  visible)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh visible <selector>"; exit 1; }
    send visible "{\"selector\": \"$2\"}"
    ;;
  count)
    [ -z "${2:-}" ] && { echo "Usage: browser.sh count <selector>"; exit 1; }
    send count "{\"selector\": \"$2\"}"
    ;;

  # Help
  help|--help|-h|*)
    cat <<'USAGE'
AI Agent Browser Tool — Secure UI interaction for AI agents (global)

Run from any project directory. Uses CWD for screenshots and config.

USAGE: browser.sh <command> [args...]

SERVER
  start                       Start browser server (background)
  stop                        Stop browser server
  status                      Check if server is running
  restart                     Restart browser server

NAVIGATION
  navigate <url>              Go to URL (allowlist enforced)
  back / forward / reload     Standard navigation
  url / title                 Print current URL or page title

INTERACTION
  click <selector>            Click an element
  type <selector> <text>      Type into an input
  press <selector> <key>      Press a key (e.g. Enter, Tab)
  hover <selector>            Hover over element
  select <selector> <value>   Select dropdown option
  scroll [up|down] [pixels]   Scroll page (default: down 500)
  wait <selector> [timeout]   Wait for element to appear

INSPECTION
  screenshot [name]           Viewport screenshot → screenshots/
  screenshot-full [name]      Full-page screenshot → screenshots/
  snapshot                    Accessibility tree (text output)
  text <selector>             Get text content
  html <selector>             Get inner HTML
  visible <selector>          Check if element is visible
  count <selector>            Count matching elements

SECURITY
  Domain allowlist resolution (first found wins):
    1. CWD/.browser-allowlist.json     (project override)
    2. browser tool directory/browser-allowlist.default.json  (global default)

  All non-allowlisted network requests are blocked at the Playwright level.
  The evaluate/exec commands are disabled.
  Server binds to 127.0.0.1 only.

EXAMPLES
  cd ~/ntm_Dev/HR_dashboard/hr-dashboard
  browser.sh start
  browser.sh navigate http://localhost:3000
  browser.sh click "button[type=submit]"
  browser.sh screenshot login-page
  browser.sh snapshot
  browser.sh stop
USAGE
    ;;
esac
