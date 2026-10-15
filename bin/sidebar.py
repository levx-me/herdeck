"""Run by bin/sidebar (Claude Code hook: PostToolUse on any tool, and Stop), in the
background with the hook's event on stdin: the agent's repository and
context use, as herdr pane tokens for its sidebar, with or without a deck open.

  $repo    the branch, commits not pushed (↑) or not pulled (↓), files not committed
  $model   the model
  $ctx     context used: a bar and a percentage, below 80%
  $ctxhot  the same from 80% on, so a ui.sidebar.agents.rows layout can colour it

Tool calls report at most every 3 seconds; the end of a turn always reports."""
import json
import os
import subprocess
import sys
import time

event = json.loads(sys.stdin.read() or "{}")
pane = os.environ.get("HERDR_PANE_ID", "")
if os.environ.get("HERDR_ENV") != "1" or not pane or event.get("agent_id"):
    sys.exit(0)  # outside herdr, or a subagent (its context is not the pane's agent's)

state = os.path.join(os.environ.get("TMPDIR", "/tmp"), "steading", pane.replace(":", "_").replace("/", "_"))
stamp = os.path.join(state, "sidebar")
try:
    if event.get("hook_event_name") != "Stop" and time.time() - os.path.getmtime(stamp) < 3:
        sys.exit(0)
except OSError:
    pass
os.makedirs(state, exist_ok=True)
open(stamp, "w").close()

os.environ.setdefault("STEADING_BASE", state)
from importlib.machinery import SourceFileLoader  # noqa: E402

v = SourceFileLoader("viewer", os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer.py")).load_module()

# The repository: as the git tab's border shows it.
cwd = event.get("cwd") or os.getcwd()
repo = v.ANSI.sub("", v.repo_state(cwd)).strip()

# The context: the latest request's tokens, from the end of the transcript.
d = v.Digest()
try:
    with open(event.get("transcript_path", ""), "rb") as f:
        f.seek(max(0, os.path.getsize(f.name) - 400_000))
        for raw in f.read().splitlines()[1:]:  # the first line may be cut
            try:
                d.feed(json.loads(raw))
            except ValueError:
                pass
except OSError:
    pass
model = v.re.sub(r"-\d{8}$", "", (d.model or d.requested or "").replace("claude-", ""))
ctx = ""
if d.context:
    pct, _ = v.context_use(d)
    filled = min(5, round(pct / 20))
    ctx = f"{'▰' * filled}{'▱' * (5 - filled)} {pct}%"

args = [os.environ.get("HERDR_BIN_PATH", "herdr"), "pane", "report-metadata", pane, "--source", "steading"]
hot = ctx and v.context_use(d)[0] >= 80
for name, value in (("repo", repo), ("model", model), ("ctx", "" if hot else ctx), ("ctxhot", ctx if hot else "")):
    args += ["--token", f"{name}={value}"] if value else ["--clear-token", name]
try:
    subprocess.run(args, capture_output=True, timeout=5)
except (OSError, subprocess.TimeoutExpired):
    pass
