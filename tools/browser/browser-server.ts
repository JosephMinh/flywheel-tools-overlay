/**
 * AI Agent Browser Server (Global)
 *
 * A secure HTTP server wrapping Playwright that gives any AI agent
 * the ability to view and interact with any project's UI.
 *
 * Project context is determined by CWD at startup:
 *   - Screenshots  → CWD/screenshots/
 *   - PID file     → CWD/.browser-server.pid
 *   - Allowlist    → CWD/.browser-allowlist.json (fallback → global default)
 *
 * Security: All navigation is restricted to hostnames in the allowlist.
 * External requests are blocked at the network level via page.route().
 *
 * Usage:
 *   browser.sh start   # from any project directory
 */

import http from "node:http";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import { chromium, type Browser, type BrowserContext, type Page } from "playwright";

// ---------------------------------------------------------------------------
// Paths — project context from CWD, tool defaults from script location
// ---------------------------------------------------------------------------

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PROJECT_DIR = process.cwd();
const PORT = Number(process.env.BROWSER_PORT ?? 6780);
const HOST = "127.0.0.1";
const SCREENSHOTS_DIR = path.resolve(PROJECT_DIR, "screenshots");
const PID_FILE = path.resolve(PROJECT_DIR, ".browser-server.pid");
const LOG_FILE = path.resolve(PROJECT_DIR, ".browser-server.log");
const COMMAND_TIMEOUT = 15_000;

// Allowlist resolution: project-local → global default
const PROJECT_ALLOWLIST = path.resolve(PROJECT_DIR, ".browser-allowlist.json");
const GLOBAL_ALLOWLIST = path.resolve(__dirname, "browser-allowlist.default.json");
const ALLOWLIST_PATH = fs.existsSync(PROJECT_ALLOWLIST) ? PROJECT_ALLOWLIST : GLOBAL_ALLOWLIST;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AllowlistConfig {
  allowedOrigins: string[];
}

interface CommandRequest {
  command: string;
  args?: Record<string, unknown>;
}

interface CommandResult {
  error?: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let browser: Browser;
let context: BrowserContext;
let page: Page;
let config: AllowlistConfig;

// ---------------------------------------------------------------------------
// Security helpers
// ---------------------------------------------------------------------------

function loadAllowlist(): AllowlistConfig {
  const raw = JSON.parse(fs.readFileSync(ALLOWLIST_PATH, "utf-8"));
  console.log(`Allowlist loaded from: ${ALLOWLIST_PATH}`);
  return raw;
}

function isAllowedUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return config.allowedOrigins.includes(parsed.hostname);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Screenshot helpers
// ---------------------------------------------------------------------------

function screenshotPath(name?: string): string {
  const filename = `${name || `shot-${Date.now()}`}.png`;
  return path.join(SCREENSHOTS_DIR, filename);
}

async function takeScreenshot(name?: string): Promise<string> {
  const p = screenshotPath(name);
  await page.screenshot({ path: p });
  return p;
}

async function takeFullScreenshot(name?: string): Promise<string> {
  const p = screenshotPath(name || `full-${Date.now()}`);
  await page.screenshot({ path: p, fullPage: true });
  return p;
}

// ---------------------------------------------------------------------------
// Command handler
// ---------------------------------------------------------------------------

async function handleCommand(req: CommandRequest): Promise<CommandResult> {
  const { command } = req;
  const a = req.args ?? {};

  switch (command) {
    // ── Navigation ────────────────────────────────────────────────────
    case "navigate": {
      const url = a.url as string;
      if (!url) return { error: "Missing required arg: url" };
      if (!isAllowedUrl(url)) {
        return {
          error: `BLOCKED: "${url}" is not in the allowlist. Allowed hostnames: ${config.allowedOrigins.join(", ")}`,
        };
      }
      await page.goto(url, { waitUntil: "networkidle", timeout: COMMAND_TIMEOUT });
      const screenshot = await takeScreenshot();
      return { url: page.url(), title: await page.title(), screenshot };
    }

    case "back": {
      await page.goBack({ timeout: COMMAND_TIMEOUT });
      const screenshot = await takeScreenshot();
      return { url: page.url(), screenshot };
    }

    case "forward": {
      await page.goForward({ timeout: COMMAND_TIMEOUT });
      const screenshot = await takeScreenshot();
      return { url: page.url(), screenshot };
    }

    case "reload": {
      await page.reload({ waitUntil: "networkidle", timeout: COMMAND_TIMEOUT });
      const screenshot = await takeScreenshot();
      return { url: page.url(), screenshot };
    }

    case "url":
      return { url: page.url() };

    case "title":
      return { title: await page.title() };

    // ── Interaction ───────────────────────────────────────────────────
    case "click": {
      const selector = a.selector as string;
      if (!selector) return { error: "Missing required arg: selector" };
      await page.click(selector, { timeout: COMMAND_TIMEOUT });
      await page.waitForLoadState("networkidle").catch(() => {});
      const screenshot = await takeScreenshot();
      return { clicked: selector, url: page.url(), screenshot };
    }

    case "type": {
      const selector = a.selector as string;
      const text = a.text as string;
      if (!selector || text == null) return { error: "Missing required args: selector, text" };
      await page.fill(selector, text);
      const screenshot = await takeScreenshot();
      return { typed: text, selector, screenshot };
    }

    case "press": {
      const selector = a.selector as string;
      const key = a.key as string;
      if (!selector || !key) return { error: "Missing required args: selector, key" };
      await page.press(selector, key, { timeout: COMMAND_TIMEOUT });
      await page.waitForLoadState("networkidle").catch(() => {});
      const screenshot = await takeScreenshot();
      return { pressed: key, selector, screenshot };
    }

    case "hover": {
      const selector = a.selector as string;
      if (!selector) return { error: "Missing required arg: selector" };
      await page.hover(selector, { timeout: COMMAND_TIMEOUT });
      const screenshot = await takeScreenshot();
      return { hovered: selector, screenshot };
    }

    case "select": {
      const selector = a.selector as string;
      const value = a.value as string;
      if (!selector || !value) return { error: "Missing required args: selector, value" };
      await page.selectOption(selector, value);
      const screenshot = await takeScreenshot();
      return { selected: value, selector, screenshot };
    }

    case "scroll": {
      const direction = (a.direction as string) || "down";
      const amount = Number(a.amount ?? 500);
      await page.evaluate(
        ({ dir, amt }: { dir: string; amt: number }) => {
          window.scrollBy(0, dir === "up" ? -amt : amt);
        },
        { dir: direction, amt: amount },
      );
      await new Promise((r) => setTimeout(r, 300));
      const screenshot = await takeScreenshot();
      return { scrolled: direction, amount, screenshot };
    }

    case "wait": {
      const selector = a.selector as string;
      const timeout = Number(a.timeout ?? 5000);
      if (!selector) return { error: "Missing required arg: selector" };
      await page.waitForSelector(selector, { timeout });
      return { found: selector };
    }

    // ── Inspection ────────────────────────────────────────────────────
    case "screenshot": {
      const name = a.name as string | undefined;
      const fullPage = Boolean(a.fullPage);
      const s = fullPage ? await takeFullScreenshot(name) : await takeScreenshot(name);
      return { screenshot: s };
    }

    case "snapshot": {
      const tree = await page.locator("body").ariaSnapshot();
      return { snapshot: tree };
    }

    case "text": {
      const selector = a.selector as string;
      if (!selector) return { error: "Missing required arg: selector" };
      const text = await page.textContent(selector, { timeout: COMMAND_TIMEOUT });
      return { text, selector };
    }

    case "html": {
      const selector = a.selector as string;
      if (!selector) return { error: "Missing required arg: selector" };
      const html = await page.innerHTML(selector, { timeout: COMMAND_TIMEOUT });
      return { html, selector };
    }

    case "visible": {
      const selector = a.selector as string;
      if (!selector) return { error: "Missing required arg: selector" };
      const visible = await page.isVisible(selector);
      return { visible, selector };
    }

    case "count": {
      const selector = a.selector as string;
      if (!selector) return { error: "Missing required arg: selector" };
      const count = await page.locator(selector).count();
      return { count, selector };
    }

    // ── Blocked commands ──────────────────────────────────────────────
    case "evaluate":
      return { error: "evaluate is disabled for security reasons" };

    default:
      return { error: `Unknown command: "${command}". Use browser.sh help for available commands.` };
  }
}

// ---------------------------------------------------------------------------
// HTTP server
// ---------------------------------------------------------------------------

async function startServer() {
  config = loadAllowlist();
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  page = await context.newPage();

  // Network-level domain enforcement
  await page.route("**/*", (route) => {
    const reqUrl = route.request().url();
    if (reqUrl.startsWith("data:") || reqUrl.startsWith("blob:")) {
      return route.continue();
    }
    if (isAllowedUrl(reqUrl)) {
      return route.continue();
    }
    console.warn(`[BLOCKED] ${reqUrl}`);
    return route.abort("blockedbyclient");
  });

  const server = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");

    if (req.method === "GET" && req.url === "/health") {
      res.end(JSON.stringify({ status: "ok", url: page.url(), pid: process.pid, project: PROJECT_DIR }));
      return;
    }

    if (req.method !== "POST" || req.url !== "/command") {
      res.statusCode = 404;
      res.end(JSON.stringify({ error: "Not found. Use POST /command or GET /health" }));
      return;
    }

    let body = "";
    req.on("data", (chunk: Buffer) => {
      body += chunk.toString();
    });
    req.on("end", async () => {
      try {
        const cmdReq = JSON.parse(body) as CommandRequest;
        const result = await handleCommand(cmdReq);
        if (result.error) res.statusCode = 400;
        res.end(JSON.stringify(result, null, 2));
      } catch (err: unknown) {
        res.statusCode = 500;
        const message = err instanceof Error ? err.message : String(err);
        res.end(JSON.stringify({ error: message }));
      }
    });
  });

  server.listen(PORT, HOST, () => {
    fs.writeFileSync(PID_FILE, String(process.pid));
    console.log(`Browser server listening on http://${HOST}:${PORT}`);
    console.log(`PID: ${process.pid}`);
    console.log(`Project: ${PROJECT_DIR}`);
    console.log(`Allowlist: ${ALLOWLIST_PATH} → ${config.allowedOrigins.join(", ")}`);
    console.log(`Screenshots → ${SCREENSHOTS_DIR}`);
  });
}

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------

async function shutdown() {
  console.log("\nShutting down browser server...");
  await browser?.close().catch(() => {});
  try {
    fs.unlinkSync(PID_FILE);
  } catch {}
  process.exit(0);
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

startServer().catch((err) => {
  console.error("Failed to start browser server:", err);
  process.exit(1);
});
