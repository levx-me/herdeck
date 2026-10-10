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
    ["./test.sh", "./test.sh", ""],
    ["make test", "make test", ""],
    ["time forge test", "time forge test", ""],
    ["npm run test:unit", "npm run test:unit", ""],
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
    "./build.sh",
    "/usr/bin/time -p ./build.sh",
    "bash scripts/build-all.sh",
    "make build",
    "npm run build:prod",
    "timeout 600 forge build",
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
    "./latest.sh",
    "./rebuild.sh",
    "make deploy",
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

describe("record keeps finished runs for the deck", () => {
  const { mkdtempSync, readFileSync, readdirSync, existsSync } = require("node:fs");
  const { tmpdir } = require("node:os");

  // Feed bin/record one hook event; returns the kind and run it wrote, or null.
  function record(event: object, env: Record<string, string> = {}) {
    const tmp = mkdtempSync(join(tmpdir(), "herdeck-"));
    const r = Bun.spawnSync([join(root, "bin/record")], {
      stdin: Buffer.from(JSON.stringify({ tool_name: "Bash", cwd: "/tmp", ...event })),
      // A dead socket keeps the test from opening a real herdr pane.
      env: { PATH: process.env.PATH!, TMPDIR: tmp, HERDR_ENV: "1", HERDR_PANE_ID: "w9:p9", HERDR_SOCKET_PATH: "/nonexistent", ...env },
    });
    expect(r.exitCode).toBe(0);
    for (const kind of ["test", "build", "git"]) {
      const runs = join(tmp, "herdeck/w9_p9", kind, "runs");
      if (!existsSync(runs)) continue;
      const [n] = readdirSync(runs);
      const f = (name: string) => readFileSync(join(runs, n, name), "utf8").trim();
      return { kind, cmd: f("cmd"), log: f("log"), rc: f("rc"), cwd: f("cwd") };
    }
    return null;
  }
  const ok = (command: string, stdout = "") =>
    record({ hook_event_name: "PostToolUse", tool_input: { command }, tool_response: { stdout, stderr: "" } });

  test("a successful git command", () => {
    expect(ok("git status --short", " M bin/run")).toEqual({ kind: "git", cmd: "git status --short", log: "M bin/run", rc: "0", cwd: "/tmp" });
  });

  test("a failed git command keeps its exit code and output", () => {
    const run = record({
      hook_event_name: "PostToolUseFailure",
      tool_input: { command: "cd repo && git push" },
      error: "Exit code 128\nfatal: no upstream",
    });
    expect(run).toEqual({ kind: "git", cmd: "cd repo && git push", log: "fatal: no upstream", rc: "128", cwd: "/tmp" });
  });

  test("git later in a chain", () => expect(ok("npm run lint && git add -A")?.kind).toBe("git"));

  test("a test run inside a chain and a pipe goes to test", () =>
    expect(ok("git status -sb | head -1; forge test --match-path 'test/*' 2>&1 | grep -E 'Suite result|FAIL'")?.kind).toBe("test"));

  test("a build piped through tail goes to build", () => expect(ok("forge build 2>&1 | tail -20")?.kind).toBe("build"));

  test("a timed build script with git status at the end goes to build", () =>
    expect(ok('/usr/bin/time -p ./build.sh > /tmp/build.log 2>&1; echo "exit $?"; grep -E "error|^real" /tmp/build.log | head -20; git status --short')?.kind).toBe("build"));

  test("a test run in a subshell", () => expect(ok("(cd contracts && forge test) | tail -5")?.kind).toBe("test"));

  test("quoted arguments do not hide the run", () =>
    expect(ok("forge test --match-path 'test/*' 2>&1 | grep -E \"Suite|FAIL\"")?.kind).toBe("test"));

  test("skips commands already routed through bin/run", () =>
    expect(ok(`"${root}/bin/run" test Zm9yZ2UgdGVzdA== `)).toBeNull());

  for (const command of [
    "ls", "echo git status", "gitk", "npx digit run", "echo forge test", "cat jest.config.js", "npm run testing",
    "grep 'forge test' notes.md",
    'sed -i "s/|bun run test/x/" README.md',
    "python3 - <<'PY'\nprint('ok')\n; forge test\nPY",
  ]) {
    test(`ignores: ${command}`, () => expect(ok(command, "x")).toBeNull());
  }

  test("a run sent to the background points at Claude's task output", () => {
    const { mkdirSync, writeFileSync } = require("node:fs");
    const tmp = mkdtempSync(join(tmpdir(), "herdeck-"));
    const uid = Bun.spawnSync(["id", "-u"]).stdout.toString().trim();
    const tasks = join(tmp, `claude-${uid}`, "project", "session", "tasks");
    mkdirSync(tasks, { recursive: true });
    writeFileSync(join(tasks, "bgtest123.output"), "Suite result: ok\n");
    const r = Bun.spawnSync([join(root, "bin/record")], {
      stdin: Buffer.from(JSON.stringify({
        hook_event_name: "PostToolUse", tool_name: "Bash", cwd: "/tmp",
        tool_input: { command: "forge test", run_in_background: true },
        tool_response: { stdout: "", stderr: "", backgroundTaskId: "bgtest123" },
      })),
      env: { PATH: process.env.PATH!, TMPDIR: tmp, HERDR_ENV: "1", HERDR_PANE_ID: "w9:p9", HERDR_SOCKET_PATH: "/nonexistent" },
    });
    expect(r.exitCode).toBe(0);
    const run = join(tmp, "herdeck/w9_p9/test/runs/1");
    expect(readFileSync(join(run, "source"), "utf8").trim()).toBe(join(tasks, "bgtest123.output"));
    expect(existsSync(join(run, "rc"))).toBe(false); // still running: no exit code yet
    expect(existsSync(join(run, "end"))).toBe(false);
  });

  test("ignores everything outside herdr", () =>
    expect(record({ hook_event_name: "PostToolUse", tool_input: { command: "git log" }, tool_response: { stdout: "x" } }, { HERDR_ENV: "" })).toBeNull());
});

describe("open tells the agent how to run things for the deck", () => {
  const open = (event: object, env: Record<string, string> = {}) =>
    Bun.spawnSync([join(root, "bin/open")], {
      stdin: Buffer.from(JSON.stringify({ hook_event_name: "SessionStart", cwd: "/tmp", ...event })),
      // compact never opens the deck; the dead socket keeps tests off real herdr.
      env: { PATH: process.env.PATH!, HERDR_ENV: "1", HERDR_PANE_ID: "w9:p9", HERDR_SOCKET_PATH: "/nonexistent", ...env },
    });

  test("adds the note to the session", () => {
    const r = open({ source: "compact" });
    expect(r.exitCode).toBe(0);
    const out = JSON.parse(r.stdout.toString()).hookSpecificOutput;
    expect(out.hookEventName).toBe("SessionStart");
    expect(out.additionalContext).toContain("plain commands");
  });

  test("says nothing outside herdr", () => expect(open({ source: "startup" }, { HERDR_ENV: "" }).stdout.toString()).toBe(""));

  test("says nothing to subagents", () => expect(open({ source: "startup", agent_id: "sub" }).stdout.toString()).toBe(""));
});
