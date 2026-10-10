import { describe, expect, test } from "bun:test";
import { join } from "node:path";

const root = join(import.meta.dir, "..");
const hook = join(root, "bin/hook");

// Run bin/hook on a Bash tool call; returns the rewritten command or null.
function rewrite(command: string, env: Record<string, string> = {}) {
  const input = JSON.stringify({ tool_name: "Bash", tool_input: { command, description: "x" } });
  const r = Bun.spawnSync([hook], {
    stdin: Buffer.from(input),
    env: { PATH: process.env.PATH!, HERDR_ENV: "1", HERDR_PANE_ID: "w1:p1", CLAUDE_PLUGIN_ROOT: root, ...env },
  });
  expect(r.exitCode).toBe(0);
  const out = r.stdout.toString().trim();
  if (!out) return null;
  const json = JSON.parse(out).hookSpecificOutput;
  expect(json.permissionDecision).toBe("allow");
  expect(json.updatedInput.description).toBe("x"); // other tool fields survive
  const [, kind, core = "", post = ""] = json.updatedInput.command.split(" ");
  const dec = (s: string) => Buffer.from(s, "base64").toString();
  return { kind, core: dec(core), post: dec(post) };
}

describe("hook rewrites plain test runs", () => {
  const cases: [string, string, string][] = [
    ["npx vitest run", "npx vitest run", ""],
    ["npx jest --runInBand", "npx jest --runInBand", ""],
    ["pytest tests/ -x", "pytest tests/ -x", ""],
    ["python3 -m pytest -q", "python3 -m pytest -q", ""],
    ["cargo test --all", "cargo test --all", ""],
    ["cargo nextest run", "cargo nextest run", ""],
    ["go test ./...", "go test ./...", ""],
    ["bun test", "bun test", ""],
    ["deno test -A", "deno test -A", ""],
    ["forge test -vvv", "forge test -vvv", ""],
    ["forge test --match-contract SwapTest", "forge test --match-contract SwapTest", ""],
    ["cd contracts && forge test", "cd contracts && forge test", ""],
    ["FOUNDRY_PROFILE=ci forge test", "FOUNDRY_PROFILE=ci forge test", ""],
    ["pnpm test", "pnpm test", ""],
    ["npm run test 2>&1", "npm run test", ""],
    ["npm run test 2>&1 | tail -40", "npm run test", "tail -40"],
    ["npx jest | head -n 20", "npx jest", "head -n 20"],
  ];
  for (const [cmd, core, post] of cases) {
    test(cmd, () => expect(rewrite(cmd)).toEqual({ kind: "test", core, post }));
  }
});

describe("hook rewrites plain builds", () => {
  for (const cmd of [
    "forge build",
    "forge build --sizes",
    "cargo build --release",
    "cargo check",
    "go build ./...",
    "npx tsc --noEmit",
    "npx next build",
    "npm run build",
    "pnpm build",
    "cd web && bun run build",
  ]) {
    test(cmd, () => expect(rewrite(cmd)).toEqual({ kind: "build", core: cmd, post: "" }));
  }
});

describe("hook leaves everything else alone", () => {
  for (const cmd of [
    "npm install && npm test",
    "forge test; rm -rf /",
    "pytest $(cat list)",
    "forge test `whoami`",
    "forge test > out.txt",
    "npm test | grep FAIL",
    "forge fmt",
    "echo vitest",
    'git commit -m "add test"',
    "vitest-like-tool run",
  ]) {
    test(cmd, () => expect(rewrite(cmd)).toBeNull());
  }

  test("outside herdr", () => expect(rewrite("forge test", { HERDR_ENV: "" })).toBeNull());
});

describe("hook does not rewrite dev servers (it only opens the servers pane)", () => {
  // CLAUDE_PLUGIN_ROOT points nowhere, so no pane is opened while testing.
  for (const cmd of ["npm run dev", "pnpm dev", "npx next dev -p 3950", "cd web && bun run dev", "anvil --port 8545", "npm start &"]) {
    test(cmd, () => expect(rewrite(cmd, { CLAUDE_PLUGIN_ROOT: "/nonexistent" })).toBeNull());
  }
});

describe("bin/run outside herdr runs the command directly", () => {
  const b64 = (s: string) => Buffer.from(s).toString("base64");
  const run = (cmd: string, post = "") =>
    Bun.spawnSync([join(root, "bin/run"), "test", b64(cmd), b64(post)], {
      env: { PATH: process.env.PATH!, HERDR_ENV: "" },
    });

  test("passes output (stderr included) and exit code through", () => {
    const r = run("echo hi; echo oops >&2; exit 3");
    expect(r.stdout.toString()).toBe("hi\noops\n");
    expect(r.exitCode).toBe(3);
  });

  test("applies the output filter and keeps the command's exit code", () => {
    const r = run("printf '1\\n2\\n3\\n'; exit 1", "tail -1");
    expect(r.stdout.toString()).toBe("3\n");
    expect(r.exitCode).toBe(1);
  });
});

describe("record keeps the agent's git commands for the git pane", () => {
  const { mkdtempSync, readFileSync, readdirSync, existsSync } = require("node:fs");
  const { tmpdir } = require("node:os");

  // Feed bin/record one hook event; returns the run dir it wrote, or null.
  function record(event: object, env: Record<string, string> = {}) {
    const tmp = mkdtempSync(join(tmpdir(), "herdeck-"));
    const r = Bun.spawnSync([join(root, "bin/record")], {
      stdin: Buffer.from(JSON.stringify({ tool_name: "Bash", cwd: "/tmp", ...event })),
      // A dead socket keeps the test from opening a real herdr pane.
      env: { PATH: process.env.PATH!, TMPDIR: tmp, HERDR_ENV: "1", HERDR_PANE_ID: "w9:p9", HERDR_SOCKET_PATH: "/nonexistent", ...env },
    });
    expect(r.exitCode).toBe(0);
    const runs = join(tmp, "herdeck/w9_p9/git/runs");
    if (!existsSync(runs)) return null;
    const [n] = readdirSync(runs);
    const f = (name: string) => readFileSync(join(runs, n, name), "utf8").trim();
    return { cmd: f("cmd"), log: f("log"), rc: f("rc"), cwd: f("cwd") };
  }

  test("a successful git command", () => {
    const run = record({
      hook_event_name: "PostToolUse",
      tool_input: { command: "git status --short" },
      tool_response: { stdout: " M bin/run", stderr: "" },
    });
    expect(run).toEqual({ cmd: "git status --short", log: "M bin/run", rc: "0", cwd: "/tmp" });
  });

  test("a failed git command keeps its exit code and output", () => {
    const run = record({
      hook_event_name: "PostToolUseFailure",
      tool_input: { command: "cd repo && git push" },
      error: "Exit code 128\nfatal: no upstream",
    });
    expect(run).toEqual({ cmd: "cd repo && git push", log: "fatal: no upstream", rc: "128", cwd: "/tmp" });
  });

  test("git later in a chain", () => {
    const run = record({
      hook_event_name: "PostToolUse",
      tool_input: { command: "npm run lint && git add -A" },
      tool_response: { stdout: "", stderr: "" },
    });
    expect(run?.cmd).toBe("npm run lint && git add -A");
  });

  for (const command of ["ls", "echo git status", "gitk", "npx digit run"]) {
    test(`ignores: ${command}`, () =>
      expect(record({ hook_event_name: "PostToolUse", tool_input: { command }, tool_response: { stdout: "x" } })).toBeNull());
  }

  test("ignores everything outside herdr", () =>
    expect(record({ hook_event_name: "PostToolUse", tool_input: { command: "git log" }, tool_response: { stdout: "x" } }, { HERDR_ENV: "" })).toBeNull());
});
