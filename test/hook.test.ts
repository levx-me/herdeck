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
  const dec = (s: string) => (s === "-" ? "" : Buffer.from(s, "base64").toString());
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

describe("hook passes the agent's description along as the run's label", () => {
  const label = (command: string, description: string) => {
    const r = Bun.spawnSync([hook], {
      stdin: Buffer.from(JSON.stringify({ tool_name: "Bash", tool_input: { command, description } })),
      env: { PATH: process.env.PATH!, HERDR_ENV: "1", HERDR_PANE_ID: "w1:p1", CLAUDE_PLUGIN_ROOT: root },
    });
    const parts = JSON.parse(r.stdout.toString()).hookSpecificOutput.updatedInput.command.split(" ");
    expect(parts).toHaveLength(5); // run, kind, command, filter, label: none may vanish
    return parts[4] === "-" ? "" : Buffer.from(parts[4], "base64").toString();
  };
  test("kind prefix dropped", () => expect(label("forge test -vvv", "test: forge test -vvv")).toBe("forge test -vvv"));
  test("prose is not a label: the command is", () => expect(label("cd contracts && forge test", "Run the tests")).toBe("forge test"));
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

  test("an empty filter followed by a label does not run the label", () => {
    const r = Bun.spawnSync([join(root, "bin/run"), "test", b64("echo once"), "-", b64("echo twice")], {
      env: { PATH: process.env.PATH!, HERDR_ENV: "" },
    });
    expect(r.stdout.toString()).toBe("once\n");
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
    const tmp = mkdtempSync(join(tmpdir(), "steading-"));
    const r = Bun.spawnSync([join(root, "bin/record")], {
      stdin: Buffer.from(JSON.stringify({ tool_name: "Bash", cwd: "/tmp", ...event })),
      // A dead socket keeps the test from opening a real herdr pane.
      env: { PATH: process.env.PATH!, TMPDIR: tmp, HERDR_ENV: "1", HERDR_PANE_ID: "w9:p9", HERDR_SOCKET_PATH: "/nonexistent", ...env },
    });
    expect(r.exitCode).toBe(0);
    for (const kind of ["test", "build", "git", "deploy"]) {
      const runs = join(tmp, "steading/w9_p9", kind, "runs");
      if (!existsSync(runs)) continue;
      const [n] = readdirSync(runs);
      const f = (name: string) => readFileSync(join(runs, n, name), "utf8").trim();
      const branch = existsSync(join(runs, n, "branch")) ? { branch: f("branch") } : {};
      return { kind, cmd: f("cmd"), log: f("log"), rc: f("rc"), cwd: f("cwd"), ...branch } as Record<string, string>;
    }
    return null;
  }
  const ok = (command: string, stdout = "", description?: string) =>
    record({ hook_event_name: "PostToolUse", tool_input: { command, description }, tool_response: { stdout, stderr: "" } });
  const labelOf = (description: string, command: string) => {
    const tmp = mkdtempSync(join(tmpdir(), "steading-"));
    Bun.spawnSync([join(root, "bin/record")], {
      stdin: Buffer.from(JSON.stringify({ hook_event_name: "PostToolUse", tool_name: "Bash", cwd: "/tmp",
        tool_input: { command, description }, tool_response: { stdout: "x" } })),
      env: { PATH: process.env.PATH!, TMPDIR: tmp, HERDR_ENV: "1", HERDR_PANE_ID: "w9:p9", HERDR_SOCKET_PATH: "/nonexistent" },
    });
    for (const kind of ["test", "build", "git", "deploy"]) {
      const f = join(tmp, "steading/w9_p9", kind, "runs/1/label");
      if (existsSync(f)) return { kind, label: readFileSync(f, "utf8").trim() };
    }
    return null;
  };

  test("a description in the note's format names the section and the label", () =>
    expect(labelOf("build: ./build.sh", "/usr/bin/time -p ./build.sh > /tmp/b.log 2>&1; git status --short"))
      .toEqual({ kind: "build", label: "./build.sh" }));

  test("the description wins over the command's own guess", () =>
    expect(labelOf("test: forge test --match-path 'test/*'", "git status -sb | head -1; forge test --match-path 'test/*' | grep FAIL"))
      .toEqual({ kind: "test", label: "forge test --match-path 'test/*'" }));

  test("vercel and railway go to deploy", () => {
    expect(ok("vercel --prod")?.kind).toBe("deploy");
    expect(ok("cd web && npx vercel deploy 2>&1 | tail -5")?.kind).toBe("deploy");
    expect(ok("railway up --detach")?.kind).toBe("deploy");
    expect(ok("echo vercel")).toBeNull();
  });

  test("vercel and railway that ship nothing stay out of deploy", () => {
    for (const cmd of ["vercel --version", "vercel ls --format json", "npx vercel logs x", "vercel env pull", "railway logs", "railway status"])
      expect(ok(cmd)).toBeNull();
    expect(ok("vercel ls", "", "deploy: vercel ls")).toBeNull(); // the description cannot make it one
    expect(ok("npm run build && vercel --prod", "", "deploy: vercel --prod")?.kind).toBe("deploy");
  });

  test("other deploy CLIs, by the subcommand that ships", () => {
    for (const cmd of ["netlify deploy --prod", "fly deploy", "npx wrangler deploy", "wrangler pages deploy dist",
      "firebase deploy --only hosting", "gcloud run deploy api --source .", "aws cloudformation deploy --template-file t.yml",
      "terraform apply -auto-approve", "kubectl apply -f k8s/", "helm upgrade --install api ./chart",
      "forge script script/Deploy.s.sol --rpc-url x --broadcast", "forge create src/A.sol:A",
      "npx hardhat ignition deploy ./m.ts", "git push heroku main", "pnpm dlx sst deploy", "gh-pages -d dist", "time kamal deploy"])
      expect(ok(cmd)?.kind).toBe("deploy");
    for (const cmd of ["netlify status", "wrangler dev", "gcloud run services list", "terraform plan", "kubectl get pods",
      "helm list", "forge script script/Deploy.s.sol", "supabase start", "sam --version"])
      expect(ok(cmd)).toBeNull();
    expect(ok("git push origin main")?.kind).toBe("git");
  });

  test("a deploy: description", () =>
    expect(labelOf("deploy: vercel --prod", "vercel --prod --yes 2>&1 | tail -3")).toEqual({ kind: "deploy", label: "vercel --prod" }));

  test("a free-form description falls back to the command for the kind, and still labels the run", () => {
    expect(ok("git push", "", "Push to origin")?.kind).toBe("git");
    expect(labelOf("Build giwaswap-status", 'cd x; pnpm build 2>&1 | grep -v "^$" | tail -25; echo "exit=$?"'))
      .toEqual({ kind: "build", label: "pnpm build" });
    expect(labelOf("Run tests", "git status -sb | head -1; forge test --match-path 'test/*' 2>&1 | grep FAIL"))
      .toEqual({ kind: "test", label: "forge test --match-path 'test/*'" });
  });

  test("a git: description on a read-only command is still skipped", () =>
    expect(ok("git status", "", "git: git status")).toBeNull());

  test("a successful git command", () => {
    expect(ok("git add -A", "")).toEqual({ kind: "git", cmd: "git add -A", log: "", rc: "0", cwd: "/tmp" });
  });

  test("a git run notes its branch: in the repo it cd's into, else its cwd", () => {
    const repo = mkdtempSync(join(tmpdir(), "steading-repo-"));
    Bun.spawnSync(["git", "init", "-q", "-b", "feature/x", repo]);
    const commit = (command: string, cwd: string) =>
      record({ hook_event_name: "PostToolUse", cwd, tool_input: { command }, tool_response: { stdout: "" } });
    expect(commit(`cd ${repo} && git commit -m "Fix"`, "/tmp")?.branch).toBe("feature/x");
    expect(commit('git commit -m "Fix"', repo)?.branch).toBe("feature/x");
    expect(commit('git commit -m "Fix"', "/tmp")?.branch).toBeUndefined(); // not a repo: no branch
  });

  test("read-only git is not recorded", () => {
    for (const c of ["git status --short", "git -C ~/repo log --oneline -5", "git diff --stat; git show HEAD", "git stash list",
                     "git branch -a", "git tag -l", "git remote -v", "git config --get user.name", "git fetch origin"]) {
      expect(ok(c, "x")).toBeNull();
    }
  });

  test("a chain with one write is recorded", () =>
    expect(ok("git status -sb; git add -A && git commit -m 'x'")?.kind).toBe("git"));

  test("write forms of branch, stash, tag, remote, config", () => {
    for (const c of ["git branch -D old", "git stash", "git stash pop", "git tag v1.0", "git remote add up x", "git config user.name x"]) {
      expect(ok(c)?.kind).toBe("git");
    }
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
    const tmp = mkdtempSync(join(tmpdir(), "steading-"));
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
    const run = join(tmp, "steading/w9_p9/test/runs/1");
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

describe("restore refills an empty deck from the transcript", () => {
  const { mkdtempSync, readFileSync, readdirSync, writeFileSync } = require("node:fs");
  const { tmpdir } = require("node:os");

  test("tests, builds and git writes come back with output and exit code", () => {
    const tmp = mkdtempSync(join(tmpdir(), "steading-"));
    const use = (id: string, command: string, description: string, at: string) => ({
      type: "assistant", timestamp: at, cwd: "/tmp",
      message: { content: [{ type: "tool_use", id, name: "Bash", input: { command, description } }] },
    });
    const result = (id: string, content: string, at: string, is_error = false, toolUseResult?: object) => ({
      type: "user", timestamp: at, toolUseResult,
      message: { content: [{ type: "tool_result", tool_use_id: id, content, is_error }] },
    });
    const transcript = join(tmp, "session.jsonl");
    writeFileSync(transcript, [
      use("a", "npm test", "test: npm test", "2026-01-01T00:00:00Z"),
      result("a", "Exit code 1\n1 failed", "2026-01-01T00:00:05Z", true),
      use("b", "git status", "Show status", "2026-01-01T00:01:00Z"), // read-only: left out
      result("b", "clean", "2026-01-01T00:01:01Z", false, { stdout: "clean", stderr: "" }),
      use("c", 'git commit -m "Fix"', 'git: git commit -m "Fix"', "2026-01-01T00:02:00Z"),
      result("c", "[main abc1234] Fix", "2026-01-01T00:02:01Z", false, { stdout: "[main abc1234] Fix", stderr: "" }),
    ].map((l) => JSON.stringify(l)).join("\n"));
    const r = Bun.spawnSync(["python3", "-I", join(root, "bin/restore"), transcript], {
      env: { PATH: process.env.PATH!, TMPDIR: tmp, HERDR_ENV: "1", HERDR_PANE_ID: "w9:p9", HERDR_SOCKET_PATH: "/nonexistent" },
    });
    expect(r.exitCode).toBe(0);
    const base = join(tmp, "steading/w9_p9");
    const f = (kind: string, name: string) => readFileSync(join(base, kind, "runs/1", name), "utf8").trim();
    expect(readdirSync(base).sort()).toEqual(["git", "test"]);
    expect([f("test", "rc"), f("test", "log"), f("test", "end")]).toEqual(["1", "1 failed", "1767225605"]);
    expect([f("git", "rc"), f("git", "log"), f("git", "label")]).toEqual(["0", "[main abc1234] Fix", 'git commit -m "Fix"']);
  });
});

describe("viewer sums up unlabelled git runs", () => {
  // Call a function of bin/viewer.py on stdin.
  const py = (fn: string, cmd: string) => {
    const code = `import sys, importlib.util as u
sys.argv = ["v", "/tmp"]
spec = u.spec_from_file_location("v", "${join(root, "bin/viewer.py")}")
m = u.module_from_spec(spec); spec.loader.exec_module(m)
print(m.${fn}(sys.stdin.read()), end="")`;
    return Bun.spawnSync(["python3", "-I", "-c", code], { stdin: Buffer.from(cmd) }).stdout.toString();
  };
  const summary = (cmd: string) => py("git_summary", cmd);

  test("a commit shows its whole message", () =>
    expect(py("commit_message", 'git add -A && git commit -q -m "Title\n\nBody with \\"quotes\\".\n\nCo-Authored-By: X" && git push'))
      .toBe('Title\n\nBody with "quotes".\n\nCo-Authored-By: X'));
  test("a heredoc commit message, whole", () =>
    expect(py("commit_message", "git commit -m \"$(cat <<'EOF'\nFix it\n\nWhy.\nEOF\n)\"")).toBe("Fix it\n\nWhy."));
  test("no message outside a commit", () => expect(py("commit_message", "git status")).toBe(""));
  test("commit hash from commit, push range, reset and a quiet commit's log line", () => {
    expect(py("git_hash", "[main (root-commit) a3d7fe2] first")).toBe("a3d7fe2");
    expect(py("git_hash", "[main 1111111] x\n   1111111..2222222  main -> main")).toBe("2222222");
    expect(py("git_hash", "HEAD is now at deadbee msg")).toBe("deadbee");
    expect(py("git_hash", "78c8eb3 Deck: no blank row")).toBe("78c8eb3");
    expect(py("git_hash", "Everything up-to-date")).toBe("");
  });
  test("git steps only, flags dropped, message cut", () =>
    expect(summary('cd repo && rm -rf x && git add -A && git diff --cached --stat && git commit -q -m "Ship it\n\nbody" && git push -q 2>&1 | tail -1'))
      .toBe('git add · git commit "Ship it" · git push'));
  test("-C path is not the subcommand", () => expect(summary("git -C ~/repo status --short")).toBe("git status"));
  test("a message line starting with git is not a step", () =>
    expect(summary('git add -A && git commit -q -m "Title\n\nright under\ngit commit." && git push -q')).toBe('git add · git commit "Title" · git push'));
  test("git after a heredoc is still found", () =>
    expect(summary("cd repo && python3 - <<'PY'\nprint('x')\nPY\nbash -n bin/x && git rm -q bin/a bin/b")).toBe("git rm"));
  test("reads dropped when there are writes", () =>
    expect(summary("git status -sb | head -1; git add -A && git diff --cached --stat && git commit -m 'x'")).toBe('git add · git commit "x"'));
  test("heredoc commit message", () =>
    expect(summary("git commit -m \"$(cat <<'EOF'\nFix the flicker\n\nbody\nEOF\n)\"")).toBe('git commit "Fix the flicker"'));
});

describe("classify reads a command line by its shell tokens", () => {
  const classify = (cmd: string) => JSON.parse(Bun.spawnSync(["python3", "-I", join(root, "bin/classify")], { stdin: Buffer.from(cmd) }).stdout.toString());
  const cases: [string, string, string][] = [
    ['cd /x; L=/tmp/s; env -u DATABASE_URL -u GIWA_RPC_URL pnpm test -- --force > $L/t.log 2>&1; echo "exit=$?"', "test", "pnpm test -- --force"],
    ["pnpm test:outage-watch:integration > /tmp/x 2>&1", "test", "pnpm test:outage-watch:integration"],
    ['cd /x/y; pnpm build 2>&1 | grep -vE "^\\s*$" | tail -25; echo "exit=$?"', "build", "pnpm build"],
    ["git status -sb | head -1; forge test --match-path 'test/*' 2>&1 | grep FAIL", "test", "forge test --match-path 'test/*'"],
    ["/usr/bin/time -p ./build.sh > /tmp/b.log 2>&1; git status --short", "build", "./build.sh"],
    ["sudo -E nice -n 5 cargo test", "test", "cargo test"],
    ["npx vercel --prod --yes 2>&1 | tail -3", "deploy", "npx vercel --prod --yes"],
    ["git add -A && git commit -m 'x' && git push", "git", ""],
    ["echo forge test", "", ""],
    ["git status --short", "", ""],
  ];
  for (const [cmd, kind, label] of cases) test(cmd, () => expect(classify(cmd)).toMatchObject({ kind, label }));
  test("a commit message mentioning a runner stays a commit", () =>
    expect(classify('git add -A && git commit -q -m "Fix it\n\nCatches e.g. env -u A pnpm test -- --force and\npnpm test:a:b." && git push -q'))
      .toMatchObject({ kind: "git", label: "" }));
  test("sudo in front is never run live", () => expect(classify("sudo cargo test").routable).toBe(false));
  test("time in front may run live", () => expect(classify("time forge test").routable).toBe(true));
});

describe("servers lists the project's dev servers", () => {
  const { mkdtempSync } = require("node:fs");
  const { tmpdir } = require("node:os");

  test("a server started from a workspace shell counts even outside the project", async () => {
    const project = mkdtempSync(join(tmpdir(), "steading-project-"));
    const elsewhere = mkdtempSync(join(tmpdir(), "steading-sibling-"));
    // A "pane shell" that starts a server in a sibling directory, like an agent would.
    const shell = Bun.spawn(["sh", "-c", "cd \"$0\" && python3 -m http.server 0 --bind 127.0.0.1", elsewhere], { stdout: "ignore", stderr: "ignore" });
    try {
      let out = "";
      for (let i = 0; i < 40 && !out; i++) {
        await Bun.sleep(100);
        out = Bun.spawnSync([join(root, "bin/servers"), project, String(shell.pid)]).stdout.toString();
      }
      expect(out).toMatch(/^\d+\t\d+\t\S*python3? -m http.server/im);
      expect(Bun.spawnSync([join(root, "bin/servers"), project]).stdout.toString()).toBe(""); // not without the shell
    } finally {
      shell.kill();
      Bun.spawnSync(["pkill", "-f", `http.server 0 --bind 127.0.0.1`]);
    }
  });
});

describe("the agents section reads the session's subagents", () => {
  const { mkdtempSync, mkdirSync, writeFileSync } = require("node:fs");
  const { tmpdir } = require("node:os");

  test("states come from the main transcript, activity from each subagent's own", () => {
    const dir = mkdtempSync(join(tmpdir(), "steading-session-"));
    const main = join(dir, "s1.jsonl");
    const subs = join(dir, "s1", "subagents");
    mkdirSync(join(subs, "workflows", "wf_1"), { recursive: true });
    const line = (o: object) => JSON.stringify(o);
    const agent = (folder: string, id: string, meta: object, rows: object[]) => {
      writeFileSync(join(folder, `agent-${id}.meta.json`), JSON.stringify(meta));
      writeFileSync(join(folder, `agent-${id}.jsonl`), rows.map(line).join("\n") + "\n");
    };
    const at = "2026-01-01T00:00:00Z";
    agent(subs, "aaa", { agentType: "Explore", description: "Map the repo", toolUseId: "t1" }, [
      { type: "user", timestamp: at, cwd: "/repo", message: { content: "List the files" } },
      { type: "assistant", timestamp: at, message: { content: [{ type: "tool_use", id: "x1", name: "Read", input: { file_path: "/repo/src/a.ts" } }] } },
      { type: "user", timestamp: at, message: { content: [{ type: "tool_result", tool_use_id: "x1" }] } },
      { type: "assistant", timestamp: at, message: { content: [{ type: "text", text: "Done mapping." }] } },
    ]);
    agent(subs, "bbb", { agentType: "general-purpose", description: "Fix it", toolUseId: "t2" }, [
      { type: "user", timestamp: at, message: { content: "Fix" } },
    ]);
    agent(join(subs, "workflows", "wf_1"), "ccc", { agentType: "Plan", description: "Plan it", toolUseId: "t3", requestShape: "background" }, [
      { type: "user", timestamp: at, message: { content: "Plan" } },
    ]);
    writeFileSync(main, [
      { type: "user", message: { content: [{ type: "tool_result", tool_use_id: "t1" }, { type: "tool_result", tool_use_id: "t2", is_error: true }] } },
      { type: "queue-operation", content: "<task-notification>\n<task-id>ccc</task-id>\n<status>completed</status>\n</task-notification>" },
    ].map(line).join("\n") + "\n");
    const check = `
import os, sys, time
from importlib.machinery import SourceFileLoader
v = SourceFileLoader("v", sys.argv[1]).load_module()
v.background = lambda fn, *a: fn(*a)  # inline, for the test
s = v.Agents(); s.session, s.looked = sys.argv[2], time.time()
s.read_main(); s.refresh()
print("listed", *(r.id for r in s.runs))  # ended subagents leave; the main agent stays, last (drawn on top)
for r in s.every:
    print(r.id, r.ended(), r.meta.get("agentType"), r.calls)
`;
    const r = Bun.spawnSync(["python3", "-I", "-c", check, join(root, "bin/viewer.py"), main], {
      env: { PATH: process.env.PATH!, STEADING_BASE: join(dir, "base") },
    });
    expect(r.stderr.toString()).toBe("");
    const out = r.stdout.toString().trim().split("\n").sort();
    expect(out).toEqual([
      "aaa done Explore 1",
      "bbb failed general-purpose 0",
      "ccc done Plan 0", // background, ended by its notification
      "listed main",
    ]);
  });
});
