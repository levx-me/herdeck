#!/usr/bin/env python3
"""Steading deck: one read-only pane beside the agent with six sections.

    viewer.py <base>

<base>/<kind>/runs/<n>/ holds one run of kind build, test, git or deploy (written by
bin/run and bin/record): cmd, cwd, log, start, pid, and once finished rc and
end. The servers section lists the project's dev servers (bin/servers on
<base>/root). A new run, or a write to <base>/focus naming a section, brings
that section forward; a new run is also followed live.

Keys: [ ] (or tab/shift-tab) and 1-6 switch sections,
q close the pane. In a run section j/k pick a run from the list and enter (or l)
moves into its output; esc comes back. The output takes herdr's copy-mode keys: h j k l, w b e, W B E,
0 ^ $, { }, gg G, ctrl-d/u, v V select, y yank (the line when nothing is
selected), / ? search, n N repeat, esc cancel. Servers work the same way, their
log being the server's output; o opens the selected one in the browser.
Mouse: click a section tab or a run (or server) in the list to switch, click in a log to place the
cursor, wheel to scroll.

Standard library only, so it runs on the python3 that macOS and Linux ship.
Editing this file restarts every open deck with the new code.
"""
import functools
import glob
import json
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import textwrap
import threading
import time
import tty

BASE = sys.argv[1] if len(sys.argv) > 1 else os.environ["STEADING_BASE"]  # plugin panes pass it in the env
SERVERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servers")
SECTIONS = ["subagents", "servers", "build", "test", "git", "deploy"]
# Screen rows (1-based, as in mouse reports): section tabs, then the section's
# head (run list box, command, info line, rule) and its body.
HEAD_ROW = 2
LIST_ROWS = 10  # run list height (rows) before it scrolls

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
STRIP_RGB, TAB_RGB, ACTIVE_RGB = "33;37;43", "44;49;58", "62;68;81"
MUTED_RGB, BRIGHT_RGB = "120;127;140", "220;223;228"
DIMMED_RGB = "88;94;105"  # the side without the cursor: list labels or the log's plain text
GREEN_RGB, RED_RGB, AMBER_RGB, CYAN_RGB = "152;177;125", "208;134;141", "216;180;106", "127;180;180"
ACCENT = {"subagents": "143;168;210", "servers": GREEN_RGB, "build": AMBER_RGB, "test": "122;162;200", "git": "180;142;173", "deploy": "209;154;102"}
MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
KEYS = re.compile(r"\x1b\[[0-9;]*[A-Za-z~]|\x1bO.|\x1b|.", re.S)
# Arrow keys move as h j k l do, in the list and in the log (normal and application cursor mode).
ARROWS = {"\x1b[A": "k", "\x1b[B": "j", "\x1b[C": "l", "\x1b[D": "h",
          "\x1bOA": "k", "\x1bOB": "j", "\x1bOC": "l", "\x1bOD": "h"}
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def background(fn, *args):
    """Run fn in a thread, so the deck never waits on it."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def spinner():
    return SPIN[int(time.time() * 8) % len(SPIN)]


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def fg(rgb):
    return f"\033[38;2;{rgb}m"


def bg(rgb):
    return f"\033[48;2;{rgb}m"


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


SGR = re.compile(r"\x1b\[([0-9;]*)m")


def tint(line, rgb):
    """The line faint, its default-coloured text in rgb: text the program
    coloured keeps its hue but loses its brightness, so none of it outshines
    the list."""
    def after(m):
        params = m.group(1).split(";") if m.group(1) else ["0"]
        # Back to the default foreground: a reset or 39 with no colour set after it.
        # A reset or 22 also ends faint, so it goes back on. Bold would outshine
        # faint (and in some terminals cancel it), so it is left out.
        back, faint, kept, i = False, False, [], 0
        while i < len(params):
            p = params[i]
            if p in ("38", "48"):  # extended colour: 38;5;n or 38;2;r;g;b
                n = 5 if params[i + 1:i + 2] == ["2"] else 3
                kept += params[i:i + n]
                back = back and p != "38"
                i += n
                continue
            if p in ("", "0"):
                back = faint = True
            elif p == "22":
                faint = True
            elif p == "39":
                back = True
            elif p.isdigit() and (30 <= int(p) <= 37 or 90 <= int(p) <= 97):
                back = False
            if p != "1":
                kept.append(p)
            i += 1
        seq = f"\033[{';'.join(kept)}m" if kept else ""
        return seq + (fg(rgb) if back else "") + ("\033[2m" if faint else "")
    return fg(rgb) + "\033[2m" + SGR.sub(after, line)


def visible_len(s):
    return len(ANSI.sub("", s))


def fit(s, cols):
    """Cut s to cols visible columns, keeping its escape codes, with … when cut."""
    if visible_len(s) <= cols:
        return s
    out, width, pos = [], 0, 0
    for m in ANSI.finditer(s + "\x1b[0m"):
        for ch in s[pos:m.start()]:
            if width >= cols - 1:
                # The mark keeps the line's background and faintness, in the muted grey.
                return "".join(out) + fg(MUTED_RGB) + "…" + RESET
            out.append(ch)
            width += 1
        out.append(m.group())
        pos = m.end()
    return "".join(out)


def rule(cols):
    return f"{DIM}{'─' * cols}{RESET}"


def link(url, text):
    return f"\033]8;;{url}\a{text}\033]8;;\a"


def strip(cells, cols, active):
    """Lay cells [(width, text)] on a dark strip, scrolled so `active` shows.

    Returns the line and where each shown cell landed: [(index, first col, last col)],
    columns counted from 1 like mouse reports."""
    room = cols - 2
    first = 0
    while first < active and sum(w + 1 for w, _ in cells[first:active + 1]) > room:
        first += 1
    out, used, last, spans = [bg(STRIP_RGB), fg(MUTED_RGB), "‹" if first else " "], 0, first, []
    for w, text in cells[first:]:
        if used + w + 1 > room:
            break
        spans.append((last, 2 + used, 1 + used + w))
        out += [text, bg(STRIP_RGB), " "]
        used += w + 1
        last += 1
    out.append(f"{fg(MUTED_RGB)}{'›' if last < len(cells) else ''}\033[K{RESET}")
    return "".join(out), spans


GIT_READS = {"status", "diff", "log", "show", "blame", "fetch", "rev-parse", "ls-files", "ls-tree", "grep",
             "describe", "shortlog", "reflog", "cat-file", "show-ref", "for-each-ref", "rev-list", "name-rev",
             "merge-base", "check-ignore", "count-objects", "help", "version"}


def git_summary(cmd):
    """`cd x && git add -A && git commit -q -m "Fix it\n\nbody" && git push`
    -> 'git add · git commit "Fix it" · git push', for the run list."""
    return " · ".join(git_steps(cmd))


def _classify():
    """bin/classify, loaded once: the shell-token reading both hooks use."""
    global _CLASSIFY
    if "_CLASSIFY" not in globals():
        from importlib.machinery import SourceFileLoader
        _CLASSIFY = SourceFileLoader("classify", os.path.join(os.path.dirname(os.path.abspath(__file__)), "classify")).load_module()
    return _CLASSIFY


@functools.lru_cache(maxsize=1024)  # pure: same command, same steps
def git_steps(cmd, messages=True):
    """The git steps of cmd, read as shell tokens (quotes kept whole), flags
    dropped, read-only ones left out when there are writes; with messages, a
    commit carries its message's first line."""
    c = _classify()
    heredoc = re.search(r"""<<-?\s*['"]?\w+['"]?\n\s*([^\n]*)""", cmd)  # first line of a heredoc message
    steps = []
    for words, _ in c.simple_commands(cmd):
        w = c.unwrap(words)
        if not w or w[0] != "git":
            continue
        args = w[1:]
        while args and args[0] in ("-C", "-c"):
            args = args[2:]
        sub = next((a for a in args if not a.startswith("-")), "")
        step = f"git {sub}".strip()
        if sub == "commit" and messages and "-m" in args[:-1]:
            msg = args[args.index("-m") + 1].strip("'\"")
            if msg.startswith("$(") and heredoc:  # -m "$(cat <<'EOF' ... EOF)"
                msg = heredoc.group(1)
            msg = msg.strip().splitlines()[0].strip() if msg.strip() else "…"
            step += f' "{msg[:40]}…"' if len(msg) > 40 else f' "{msg}"'
        if step not in steps:
            steps.append(step)
    # Only the steps that change something, when there are any.
    writes = [st for st in steps if st.split()[1:2] and st.split()[1] not in GIT_READS]
    return writes or steps


@functools.lru_cache(maxsize=1024)
def commit_message(cmd):
    """The full message of a `git commit` in cmd: from -m "..." (any number of
    lines) or from a heredoc fed to it; "" when there is none."""
    if not re.search(r"\bgit\b[^\n;&|]*\bcommit\b", cmd.split("<<", 1)[0]):  # not in a heredoc body
        return ""
    heredoc = re.search(r"""<<-?\s*['"]?(\w+)['"]?\n(.*?)\n\s*\1\b""", cmd, re.S)
    m = re.search(r"""-m\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""", cmd, re.S)
    msg = (m.group(1) or m.group(2) or "") if m else ""
    if (not msg or msg.startswith("$(")) and heredoc:
        msg = heredoc.group(2)
    return msg.replace('\\"', '"').strip()


HASHES = [  # where git prints the commit a command leaves behind
    re.compile(r"^\[[^\]\s]+(?: \([^)]*\))? ([0-9a-f]{7,40})\]", re.M),    # commit: [main 1a2b3c4] msg
    re.compile(r"\b[0-9a-f]{7,40}\.\.\.?([0-9a-f]{7,40})\b"),            # push/pull/fetch: a..b
    re.compile(r"HEAD is now at ([0-9a-f]{7,40})\b"),                     # reset, checkout
    re.compile(r"^([0-9a-f]{7,40}) \S", re.M),                            # log --oneline after a quiet commit
]


def git_hash(text):
    """Short hash of the last commit the output mentions, or ""."""
    found = []
    for pattern in HASHES:
        found += [(m.start(), m.group(1)) for m in pattern.finditer(text)]
    return max(found)[1][:7] if found else ""


class Run:
    def __init__(self, path):
        self.path = path
        self.full = read(os.path.join(path, "cmd"))
        self.tag = read(os.path.join(path, "label"))  # "forge test", from the agent's description
        if not self.tag and os.path.basename(os.path.dirname(os.path.dirname(path))) == "git":
            self.tag = git_summary(self.full)
        # For the run list, one line: a multi-line command (a heredoc, say) gets ⏎ marks.
        self.cmd = " ⏎ ".join(self.full.splitlines())
        self.is_git = os.path.basename(os.path.dirname(os.path.dirname(path))) == "git"
        # The log, read in a background thread and kept until the file changes.
        self._log = (None, None, None, "")  # (file key, display lines, plain lines, hash)
        self._loading = None

    def source(self):
        """Claude's task output file, for a run sent to the background."""
        return read(os.path.join(self.path, "source"))

    def rc(self):
        """Exit code, or "" while running. A background run's code is the
        "[exited with code N]" line Claude Code appends to its output file."""
        rc = read(os.path.join(self.path, "rc"))
        src = self.source()
        if rc or not src:
            return rc
        try:
            with open(src, "rb") as f:
                f.seek(max(0, os.path.getsize(src) - 200))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            return ""
        m = re.search(r"\[exited with code (\d+)\]\s*$", tail)
        return m.group(1) if m else ""

    def status(self):
        rc = self.rc()
        if rc:
            return ("✓", GREEN_RGB) if rc == "0" else ("✗", RED_RGB)
        if self.source():
            return (spinner(), AMBER_RGB)  # background run still going
        try:
            os.kill(int(read(os.path.join(self.path, "pid"), "0")), 0)
            return (spinner(), AMBER_RGB)
        except (OSError, ValueError):
            return ("⊘", MUTED_RGB)  # the runner died without an exit code

    def label(self):
        if self.tag:
            return self.tag
        cmd = re.sub(r"^cd \S+ && ", "", self.cmd)
        cmd = re.sub(r"^([A-Za-z_]\w*=\S* )+", "", cmd)
        return re.sub(r"^(npx|bunx|pnpm( exec| dlx)?|yarn|uv run|poetry run|python3? -m) ", "", cmd)

    def age(self):
        """'running 4s' while it runs, else how long ago it finished."""
        end = self.end()
        if not end:
            start = int(read(os.path.join(self.path, "start"), "0") or 0)
            return f"running {int(time.time()) - start}s" if start else "running"
        s = int(time.time()) - int(end)
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if s >= size:
                return f"{s // size}{unit} ago"
        return f"{s}s ago"

    CMD_LINES = 8  # longest command shown before it is cut

    MSG_LINES = 15  # longest commit message shown

    def command(self, cols):
        """The run's label, or else its whole command, wrapped to the pane.
        A git commit also shows its whole message."""
        is_git = os.path.basename(os.path.dirname(os.path.dirname(self.path))) == "git"
        steps = git_steps(self.full, messages=False) if is_git else []
        if steps:  # one line per git step, the commit's whole message under it
            message = commit_message(self.full)
            rows = []
            for step in steps:
                rows.append(f"{BOLD}$ {step}{RESET}")
                if step == "git commit" and message:
                    lines = []
                    for line in message.splitlines():
                        lines += textwrap.wrap(line, max(10, cols - 4)) or [""]
                    if len(lines) > self.MSG_LINES:
                        lines = lines[:self.MSG_LINES - 1] + [f"… {len(lines) - self.MSG_LINES + 1} more lines"]
                    rows += [f"{fg(ACCENT['git'])}  │ {RESET}{l}" for l in lines]
            return rows
        if self.tag:
            rows = [f"{BOLD}{r}{RESET}" for r in textwrap.wrap(self.tag, max(10, cols), initial_indent="$ ",
                                                             subsequent_indent="    ")]
            if self.full.strip() != self.tag.strip():  # the real command, dimmed, two rows at most
                full = textwrap.wrap(" ⏎ ".join(self.full.splitlines()), max(10, cols - 2))
                rows += [f"{DIM}  {r}{RESET}" for r in full[:2]]
                if len(full) > 2:
                    rows[-1] = rows[-1][:-len(RESET)] + "…" + RESET
            return rows
        rows = []
        for i, line in enumerate(self.full.splitlines() or [""]):
            rows += textwrap.wrap(line.expandtabs(4), max(10, cols), initial_indent="$ " if i == 0 else "  ",
                                  subsequent_indent="    ", drop_whitespace=False) or ["  "]
        if len(rows) > self.CMD_LINES:
            rows = rows[:self.CMD_LINES - 1] + [f"{DIM}  … {len(rows) - self.CMD_LINES + 1} more lines{RESET}"]
        return [f"{BOLD}{r}{RESET}" if not r.startswith(DIM) else r for r in rows]

    def end(self):
        """When it finished: the end file, or a finished background run's output mtime."""
        end = read(os.path.join(self.path, "end"))
        if not end and self.source() and self.rc():
            try:
                end = str(int(os.path.getmtime(self.source())))
            except OSError:
                pass
        return end

    def hash(self):
        """For git runs, the commit it left behind (from its output); "" until read."""
        if not self.is_git:
            return ""
        self.lines()
        return self._log[3]

    def full_hash(self):
        """The whole hash behind the short one, looked up once (in a thread) in
        the run's repo; "" until found."""
        short = self.hash()
        if short and not hasattr(self, "_full"):
            self._full = ""
            threading.Thread(target=self._find_full, args=(short,), daemon=True).start()
        return getattr(self, "_full", "")

    def _find_full(self, short):
        cwd = read(os.path.join(self.path, "cwd"))
        # Every directory the command cd's into (newest last wins), then its cwd.
        cds = [os.path.join(cwd, os.path.expanduser(d.strip("'\""))) for d in re.findall(r"(?:^|[;&|(\n]\s*)cd\s+(\S+)", self.full)]
        for d in [*reversed(cds), cwd]:
            try:
                out = subprocess.run(["git", "-C", d, "rev-parse", "--verify", "--quiet", short + "^{commit}"],
                                     capture_output=True, text=True, timeout=2).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                out = ""
            if re.fullmatch(r"[0-9a-f]{40}", out):
                self._full = out
                break

    def info(self):
        """Where, how long, and how it ended."""
        start = int(read(os.path.join(self.path, "start"), "0") or 0)
        end = self.end()
        rc = self.rc()
        parts = [read(os.path.join(self.path, "cwd")).replace(os.path.expanduser("~"), "~", 1)]
        branch = read(os.path.join(self.path, "branch"))
        if branch:
            parts.append(f"on {branch}")
        if start:
            parts.append(f"{(int(end) if end else int(time.time())) - start}s")
        parts.append(f"exit {rc}" if rc else "running in the background" if self.source() else "running")
        return f"{DIM}  {' · '.join(p for p in parts if p)}{RESET}"

    MAX_BYTES = 2_000_000  # a bigger log shows its last 2 MB

    def lines(self):
        """Display lines of the log, or None while it is first being read.
        Reading happens in a thread and only when the file changed (mtime and
        size), so switching tabs never waits on a big log."""
        path = self.source() or os.path.join(self.path, "log")
        try:
            st = os.stat(path)
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return []
        if key != self._log[0] and self._loading != key:
            self._loading = key
            threading.Thread(target=self._load, args=(path, key), daemon=True).start()
        return self._log[1]

    def plain(self):
        self.lines()
        return self._log[2]

    def _load(self, path, key):
        try:
            with open(path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                cut = size > self.MAX_BYTES
                if cut:
                    f.seek(size - self.MAX_BYTES)
                data = f.read()
        except OSError:
            data, cut = b"", False
        text = data.decode("utf-8", "replace")
        if cut:  # drop the partial first line and say so
            text = f"{DIM}… earlier output cut ({size // 1_000_000} MB log){RESET}\n" + text.split("\n", 1)[-1]
        text = re.sub(r"\n*\[exited with code \d+\]\s*$", "", text)
        shown = clean(text, git=self.is_git)
        plain = [ANSI.sub("", l) for l in shown]
        sha = git_hash("\n".join(plain)) if self.is_git else ""
        self._log = (key, shown, plain, sha)  # one assignment: readers see old or new, never half
        if self._loading == key:
            self._loading = None


_panes = (0.0, [])
def herdr_panes(fresh=False):
    """Every herdr pane as (pane id, workspace id, shell pid, tty), looked up
    at most every 10s unless fresh. Call it from a thread: it asks herdr once per pane."""
    global _panes
    if not fresh and time.time() - _panes[0] < 10:
        return _panes[1]
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")

    def ask(*args):
        try:
            out = subprocess.run([herdr, *args], capture_output=True, text=True, timeout=3).stdout
            return json.loads(out).get("result", {})
        except (OSError, subprocess.SubprocessError, ValueError):
            return {}
    found = []
    for pane in ask("pane", "list").get("panes", []):
        shell = ask("pane", "process-info", "--pane", pane["pane_id"]).get("process_info", {}).get("shell_pid")
        if shell:
            tty = subprocess.run(["ps", "-o", "tty=", "-p", str(shell)], capture_output=True, text=True).stdout.strip()
            found.append((pane["pane_id"], pane.get("workspace_id", ""), str(shell), tty))
    _panes = (time.time(), found)
    return found


class ServerRun(Run):
    """A dev server in the servers list. Its log is wherever its output goes:
    a file (a server Claude sent to the background writes to its task output)
    or the herdr pane whose terminal it prints to."""

    def __init__(self, port, pid, cmd):
        self.port, self.pid = port, pid
        self.path, self.tag, self.is_git = "", "", False
        self.full = self.cmd = re.sub(r"^\S*/", "", cmd.strip())  # /long/path/Python -m x -> Python -m x
        self._log, self._loading, self._read_at = (None, None, None, ""), None, 0.0
        self.hold = False  # set while the user reads the log: a pane log is not re-read under them
        self.where = None  # ("file", path) | ("pane", pane id) | ("none", why); None while looking
        threading.Thread(target=self._find, daemon=True).start()

    def _find(self):
        """Follow stdout up the process tree (a dev server's workers write to a
        pipe their parent reads) until it is a file or a terminal."""
        pid = self.pid
        for _ in range(8):
            out = subprocess.run(["lsof", "-a", "-p", pid, "-d", "1", "-Fn"], capture_output=True, text=True).stdout
            name = next((l[1:] for l in out.splitlines() if l.startswith("n")), "")
            if name.startswith("/dev/tty"):
                def on_tty(panes):
                    return next((p for p, _, _, tty in panes if tty and name == "/dev/" + tty), None)
                pane = on_tty(herdr_panes()) or on_tty(herdr_panes(fresh=True))  # a pane opened since the last look
                self.where = ("pane", pane) if pane else ("none", f"it prints to {name}, outside herdr")
                return
            if os.path.isfile(name):
                self.where = ("file", name)
                return
            pid = subprocess.run(["ps", "-o", "ppid=", "-p", pid], capture_output=True, text=True).stdout.strip()
            if pid in ("", "0", "1"):
                break
        self.where = ("none", "its output goes nowhere the deck can read")

    def source(self):
        return self.where[1] if self.where and self.where[0] == "file" else ""

    def rc(self):
        return ""

    def status(self):
        return ("●", GREEN_RGB)

    def age(self):
        return f":{self.port}"

    def end(self):
        return ""

    def info(self):
        url = f"http://localhost:{self.port}"
        where = {"file": "log file", "pane": f"pane {self.where[1]}"}.get(self.where[0], "") if self.where else ""
        return f"  {link(url, fg(CYAN_RGB) + url + RESET)}{DIM} · pid {self.pid}{' · ' + where if where else ''}{RESET}"

    def lines(self):
        if self.where is None:
            return None
        kind, what = self.where
        if kind == "file":
            return super().lines()
        if kind == "none":
            self._log = (None, [f"{DIM}no log: {what}{RESET}"], [f"no log: {what}"], "")
        elif time.time() - self._read_at >= 1 and not self._loading and not self.hold:  # a pane: read it every second
            self._read_at, self._loading = time.time(), True
            threading.Thread(target=self._read_pane, args=(what,), daemon=True).start()
        return self._log[1]

    def _read_pane(self, pane):
        try:
            text = subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), "pane", "read", pane, "--source", "recent",
                                   "--lines", "2000", "--format", "ansi"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            text = ""
        shown = clean(text.rstrip("\n"))
        self._log = (None, shown, [ANSI.sub("", l) for l in shown], "")
        self._loading = None


CSI = re.compile(r"\x1b\[([0-9;?]*)[ -/]*([@-~])")
OTHER = re.compile(r"\x1b\](?!8;)[^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>78]|[\x00-\x06\x08\x0b\x0c\x0e-\x1a\x1c-\x1f]")
GIT_COLOURS = [  # plain git output gets the colours git would use on a terminal
    (re.compile(r"^(\+\+\+|---) "), BOLD),
    (re.compile(r"^@@"), fg(CYAN_RGB)),
    (re.compile(r"^\+"), fg(GREEN_RGB)),
    (re.compile(r"^-"), fg(RED_RGB)),
    (re.compile(r"^(commit [0-9a-f]{7,}|[0-9a-f]{7,40} )"), fg(AMBER_RGB)),
    (re.compile(r"^( ?[MADRCU?]{1,2} )"), fg(GREEN_RGB)),
    (re.compile(r"^(fatal|error|hint):"), fg(RED_RGB)),
]


def clean(text, git=False):
    """Log text to display lines: carriage returns resolved, only colour (SGR)
    and link (OSC 8) codes kept, and colours carried across line breaks."""
    out, state = [], []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if "\r" in line:  # a carriage return redraws the line: keep what was drawn last
            line = line.rsplit("\r", 1)[-1]
        line = OTHER.sub("", line)
        start = "".join(state)
        kept = []
        pos = 0
        for m in CSI.finditer(line):
            kept.append(line[pos:m.start()])
            pos = m.end()
            if m.group(2) != "m":
                continue  # cursor moves, erases: meaningless in a log
            kept.append(m.group(0))
            state = [] if m.group(1) in ("", "0") else state + [m.group(0)]
        kept.append(line[pos:])
        line = "".join(kept)
        if git and "\x1b[" not in line:
            for pattern, colour in GIT_COLOURS:
                if pattern.search(line):
                    line = colour + line + RESET
                    break
        out.append(start + line)
    while out and not ANSI.sub("", out[-1]).strip():  # no cursor on trailing blank lines
        out.pop()
    return out


def char_class(ch, big):
    """0 blank, 1 word char, 2 punctuation; WORD motions (big) merge 1 and 2."""
    if ch.isspace():
        return 0
    return 1 if big or ch.isalnum() or ch == "_" else 2


def copy(text):
    """Put text on the clipboard: pbcopy, wl-copy or xclip, else OSC 52."""
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode(), check=False)
            return
    import base64
    sys.stdout.write(f"\033]52;c;{base64.b64encode(text.encode()).decode()}\a")


class Runs:
    """One run section (build, test or git): run tabs and the log under a
    copy-mode cursor, with the same keys as herdr's copy mode."""

    noun = "runs"

    def __init__(self, kind):
        self.kind, self.dir = kind, os.path.join(BASE, kind, "runs")
        self.empty = f"no {kind} runs yet"
        self.runs, self.known, self.tab = [], None, 0
        self.offset, self.follow = 0, True
        self.cy = self.cx = 0              # cursor line / column in the plain text
        self.anchor, self.linewise = None, False  # visual selection start
        self.pending = ""                  # "g" waiting for its second g
        self.search, self.query, self.back = None, "", False  # None = not typing
        self.msg = ""
        self.raw, self.plain, self.height = [], [], 1
        self.focus = "list"                # "list": j/k pick runs; "log": copy-mode cursor

    def refresh(self):
        """Reload the runs; True when a new one appeared since the last look."""
        try:
            ns = sorted(int(n) for n in os.listdir(self.dir) if n.isdigit())
        except OSError:
            ns = []
        new = self.known is not None and any(n not in self.known for n in ns)
        if self.known is None or new:  # start on, and jump to, the newest run
            self.select(len(ns) - 1)
        self.known = set(ns)
        # Keep Run objects (and their loaded logs) across refreshes.
        cache = {r.n: r for r in self.runs if hasattr(r, "n")}
        self.runs = [cache.get(n) or Run(os.path.join(self.dir, str(n))) for n in ns]
        for n, r in zip(ns, self.runs):
            r.n = n
        self.tab = max(0, min(self.tab, len(self.runs) - 1))
        return new

    def select(self, tab):
        self.tab, self.follow, self.anchor, self.msg = tab, True, None, ""

    def badge(self):
        return self.runs[-1].status() if self.runs else ("·", MUTED_RGB)

    # cursor ----------------------------------------------------------------

    def clamp(self):
        last = max(0, len(self.plain) - 1)
        self.cy = max(0, min(self.cy, last))
        width = len(self.plain[self.cy]) if self.plain else 0
        self.cx = max(0, min(self.cx, max(0, width - 1)))

    def at(self, y, x):
        line = self.plain[y] if 0 <= y < len(self.plain) else ""
        return line[x] if 0 <= x < len(line) else "\n"

    def step(self, y, x, forward):
        """Next / previous position across lines, a line break counting as blank."""
        if forward:
            if x + 1 < len(self.plain[y]):
                return y, x + 1
            return (y + 1, -1) if y + 1 < len(self.plain) else None
        if x > 0:
            return y, x - 1
        return (y - 1, len(self.plain[y - 1])) if y > 0 else None

    def word(self, motion, big):
        y, x = self.cy, self.cx
        cls = lambda p: char_class(self.at(*p), big)
        if motion == "w":  # past this word, then past blanks
            start = cls((y, x))
            while (n := self.step(y, x, True)) and cls(n) == start and start:
                y, x = n
            while (n := self.step(y, x, True)):
                y, x = n
                if cls((y, x)):
                    break
        elif motion == "e":  # off this spot, past blanks, to the word's end
            if (n := self.step(y, x, True)):
                y, x = n
            while cls((y, x)) == 0 and (n := self.step(y, x, True)):
                y, x = n
            c = cls((y, x))
            while (n := self.step(y, x, True)) and cls(n) == c:
                y, x = n
        else:  # "b": back past blanks, to the word's start
            if (n := self.step(y, x, False)):
                y, x = n
            while cls((y, x)) == 0 and (n := self.step(y, x, False)):
                y, x = n
            c = cls((y, x))
            while (n := self.step(y, x, False)) and cls(n) == c:
                y, x = n
        self.cy, self.cx = y, max(0, x)

    def paragraph(self, down):
        y = self.cy + (1 if down else -1)
        while 0 <= y < len(self.plain) and self.plain[y].strip():
            y += 1 if down else -1
        self.cy, self.cx = max(0, min(y, len(self.plain) - 1)), 0

    def find(self, backwards):
        if not self.query:
            return
        q = self.query
        fold = q.islower()
        lines = [l.lower() for l in self.plain] if fold else self.plain
        n = len(lines)
        for i in range(n + 1):
            y = (self.cy - i) % n if backwards else (self.cy + i) % n
            line = lines[y]
            if i == 0:
                x = line.rfind(q, 0, self.cx) if backwards else line.find(q, self.cx + 1)
            else:
                x = line.rfind(q) if backwards else line.find(q)
            if x >= 0:
                self.cy, self.cx, self.msg = y, x, ""
                return
        self.msg = f"not found: {q}"

    def selection(self):
        """Selected text, or the cursor line when nothing is selected."""
        if self.anchor is None:
            return self.plain[self.cy] + "\n" if self.plain else ""
        (y0, x0), (y1, x1) = sorted([self.anchor, (self.cy, self.cx)])
        if self.linewise:
            return "\n".join(self.plain[y0:y1 + 1]) + "\n"
        if y0 == y1:
            return self.plain[y0][x0:x1 + 1]
        return "\n".join([self.plain[y0][x0:]] + self.plain[y0 + 1:y1] + [self.plain[y1][:x1 + 1]])

    def selected(self, y, x):
        if self.anchor is None:
            return False
        (y0, x0), (y1, x1) = sorted([self.anchor, (self.cy, self.cx)])
        if not y0 <= y <= y1:
            return False
        if self.linewise:
            return True
        return (y > y0 or x >= x0) and (y < y1 or x <= x1)

    # drawing ---------------------------------------------------------------

    def frame(self, cols, rows):
        """Head lines (run list, info, rule) and the visible log lines."""
        if not self.runs:
            return [f"{DIM} {self.empty}{RESET}", rule(cols)], []
        run = self.runs[self.tab]
        loaded = run.lines()
        self.raw = loaded if loaded is not None else [f"{DIM}loading…{RESET}"]
        self.plain = run.plain() if loaded is not None else [""]
        head = self.run_list(cols)
        box = len(head)
        if self.search is not None:
            info = f"{fg(ACCENT[self.kind])}{'?' if self.back else '/'}{RESET}{self.search}▏"
        else:
            mode = ("-- VISUAL LINE -- " if self.linewise else "-- VISUAL -- ") if self.anchor else ""
            note = f"{fg(AMBER_RGB)}{self.msg}{RESET}  " if self.msg else ""
            info = f"{BOLD}{mode}{RESET}{note}{run.info()}"
        # The run list box, then the run's command.
        head += [*run.command(cols), info]
        if run.hash():  # the whole hash, on its own line so a narrow pane does not cut it
            head.append(f"{DIM}  commit {fg(ACCENT['git'])}{run.full_hash() or run.hash()}{RESET}")
        if self.focus == "list":  # the run's head reads faint with its log while the cursor is in the list
            head[box:] = [tint(line, DIMMED_RGB) for line in head[box:]]
        head.append(rule(cols))

        running = run.status()[1] == AMBER_RGB
        # While it runs, the last row shows a spinner under the output.
        self.height = height = max(1, rows - (HEAD_ROW - 1) - len(head) - (1 if running else 0))
        if self.follow or self.focus == "list":  # pinned to the end while the run grows
            self.cy = max(0, len(self.plain) - 1)
        self.clamp()
        if self.cy < self.offset:
            self.offset = self.cy
        elif self.cy >= self.offset + height:
            self.offset = self.cy - height + 1
        self.offset = max(0, min(self.offset, max(0, len(self.plain) - height)))
        self.body_row = HEAD_ROW + len(head)
        cursor = self.focus == "log"

        body = []
        tip = self.tooltip_at(cols)
        for y in range(self.offset, min(len(self.raw), self.offset + height)):
            if tip and y == tip[0]:  # the tooltip sits on this row, over its text
                _, x, label = tip
                line = self.plain[y].ljust(x)
                body.append(f"{line[:x]}{bg(ACCENT[self.kind])}{fg(STRIP_RGB)}{BOLD}{label}{RESET}{line[x + len(label):]}")
            elif (cursor and y == self.cy) or self.selected(y, 0) or self.selected(y, len(self.plain[y]) - 1):
                body.append(self.marked(y))
            else:
                # The log reads dim while the cursor is in the list.
                body.append(tint(self.raw[y], DIMMED_RGB) if self.focus == "list" else self.raw[y])
        if running:
            start = int(read(os.path.join(run.path, "start"), "0") or 0)
            secs = f" {int(time.time()) - start}s" if start else ""
            body.append(f"{fg(AMBER_RGB)}{spinner()} running{secs}{RESET}")
        return head, body

    def tooltip_at(self, cols):
        """(log line, column, label) of a live copy tooltip: on the line above the
        selection, from its first column, or below it when that line is off screen."""
        text, until = getattr(self, "tooltip", None) or ("", 0)
        if until < time.time() or self.anchor is None:
            return None
        (y0, x0), (y1, _) = sorted([self.anchor, (self.cy, self.cx)])
        label = f" {text} "
        row = y0 - 1 if y0 - 1 >= self.offset else y1 + 1
        return row, max(0, min(x0, cols - len(label))), label

    def run_list(self, cols):
        """The runs in a rounded box, newest on top, the one on show raised;
        scrolls past LIST_ROWS. The border takes the accent while the list has
        the focus."""
        edge = fg(ACCENT[self.kind]) if self.focus == "list" else fg("70;76;88")
        inner = cols - 2
        title = f" {self.noun} · {len(self.runs)} "
        top = f"{edge}╭─{fg(MUTED_RGB)}{title}{edge}{'─' * max(0, inner - 1 - len(title))}╮{RESET}"
        rows = [f"{edge}│{RESET}{line}{RESET}{edge}│{RESET}" for line in self.rows(inner)]
        return [top, *rows, f"{edge}╰{'─' * inner}╯{RESET}"]

    def rows(self, cols):
        """List rows, each exactly cols wide. A git run takes one row per git
        step; status, age and hash sit on its first row. Scrolls by rows, keeping
        the selected run in view; self.listed maps each row to its run."""
        entries = []  # (run index, first row of the run?, text)
        for i in range(len(self.runs) - 1, -1, -1):
            run = self.runs[i]
            steps = git_steps(run.full) if self.kind == "git" else []
            for n, text in enumerate(steps or [run.label()]):
                entries.append((i, n == 0, text))
        at = next(n for n, (i, first, _) in enumerate(entries) if i == self.tab)
        span = sum(1 for i, _, _ in entries if i == self.tab)
        top = max(0, min(at - max(0, (LIST_ROWS - span) // 2), len(entries) - LIST_ROWS))
        shown_entries = entries[top:top + LIST_ROWS]
        self.listed = [i for i, _, _ in shown_entries]

        lines = []
        for i, first, label in shown_entries:
            run = self.runs[i]
            selected = i == self.tab
            if first:
                icon, colour = run.status()
                age = run.age()
                # git rows keep a fixed 7-column hash at the far right (blank when
                # there is none), so the variable-width age lines up before it.
                sha = run.hash()
                branch = read(os.path.join(run.path, "branch")) if self.kind == "git" else ""  # the branch it ran on
                right = f"{branch}  {age}  {sha:7}" if branch else f"{age}  {sha:7}" if self.kind == "git" else age
                shown = (f"{fg(DIMMED_RGB)}{branch}{fg(MUTED_RGB)}  " if branch else "") + \
                    (f"{age}  {fg(ACCENT['git'])}{sha:7}" if self.kind == "git" else age)
            else:
                icon, colour, right, shown = " ", MUTED_RGB, "", ""
            # "➜ ✓ label<gap>right " and "  ✓ label<gap>right " both fill the line.
            fixed = 5
            room = cols - fixed - 1 - len(right)
            if len(label) > room:
                label = label[:max(1, room - 1)] + "…"
            gap = " " * max(1, cols - fixed - len(label) - len(right))
            if selected:
                # The arrow only while the list is grey (the cursor in the log): whose output it is.
                arrow = fg(MUTED_RGB) + ("➜" if first and self.focus == "log" else " ")
                lines.append(f"{bg(ACTIVE_RGB)}{arrow}{fg(colour)} {icon} {fg(BRIGHT_RGB)}{BOLD}{label}"
                             f"\033[22m{gap}{fg(MUTED_RGB)}{shown} {RESET}")
            else:
                # Labels bright while the cursor is in the list, dim while it is in the log.
                text = BRIGHT_RGB if self.focus == "list" else DIMMED_RGB
                lines.append(f"  {fg(colour)}{icon} {fg(text)}{label}{fg(MUTED_RGB)}{gap}{shown} {RESET}")
        return lines

    def marked(self, y):
        """A plain line with the selection shaded and the cursor in reverse."""
        line = self.plain[y] or " "
        out = []
        for x, ch in enumerate(line):
            if y == self.cy and x == self.cx:
                out.append(f"\033[7m{ch}\033[27m")
            elif self.selected(y, x):
                out.append(f"{bg('74;84;104')}{ch}\033[49m")
            else:
                out.append(ch)
        return "".join(out)

    # input -----------------------------------------------------------------

    def key(self, k):
        if not self.runs:
            return
        if self.search is not None:  # typing a search
            if k in ("\r", "\n"):
                self.query, self.search = self.search, None
                self.find(self.back)
            elif k == "\x1b":
                self.search = None
            elif k in ("\x7f", "\x08"):
                self.search = self.search[:-1]
            elif k.isprintable():
                self.search += k
            return

        if self.focus == "list":
            self.list_key(k)
            return

        if self.pending == "g":
            self.pending = ""
            if k == "g":
                self.cy, self.cx, self.follow = 0, 0, False
            return

        self.msg = ""
        half = max(1, self.height // 2)
        moves = {
            "j": lambda: setattr(self, "cy", self.cy + 1),
            "k": lambda: setattr(self, "cy", self.cy - 1),
            "h": lambda: setattr(self, "cx", self.cx - 1),
            "l": lambda: setattr(self, "cx", self.cx + 1),
            "0": lambda: setattr(self, "cx", 0),
            "$": lambda: setattr(self, "cx", len(self.plain[self.cy]) if self.plain else 0),
            "^": lambda: setattr(self, "cx", len(self.plain[self.cy]) - len(self.plain[self.cy].lstrip()) if self.plain else 0),
            "w": lambda: self.word("w", False), "W": lambda: self.word("w", True),
            "b": lambda: self.word("b", False), "B": lambda: self.word("b", True),
            "e": lambda: self.word("e", False), "E": lambda: self.word("e", True),
            "{": lambda: self.paragraph(False), "}": lambda: self.paragraph(True),
            "\x04": lambda: setattr(self, "cy", self.cy + half),
            "\x15": lambda: setattr(self, "cy", self.cy - half),
            "n": lambda: self.find(self.back), "N": lambda: self.find(not self.back),
        }
        if k in moves:
            self.follow = False
            moves[k]()
            self.clamp()
            # Moving to the very end of a live run picks the live tail back up.
            self.follow = k in ("j", "\x04") and self.cy >= len(self.plain) - 1 and self.anchor is None
        elif k == "G":
            self.follow, self.cx = True, 0
        elif k == "g":
            self.pending = "g"
        elif k in ("v", "V"):
            if self.anchor and self.linewise == (k == "V"):
                self.anchor = None
            else:
                self.anchor = self.anchor or (self.cy, self.cx)
                self.linewise = k == "V"
                self.follow = False
        elif k == "y":
            text = self.selection()
            copy(text)
            lines = text.count("\n") + (0 if text.endswith("\n") else 1)
            self.msg, self.anchor = f"yanked {lines} line{'s' if lines != 1 else ''}", None
        elif k == "\x1b":
            if self.anchor is not None:
                self.anchor = None
            else:  # nothing to cancel: back to the run list
                self.focus, self.follow = "list", True
        elif k in ("/", "?"):
            self.search, self.back = "", k == "?"

    def hints(self):
        if self.search is not None:
            return [("enter", "find"), ("esc", "cancel")]
        if self.focus == "list":
            return [("j/k", "select"), ("enter", "open"), ("[ ]", "tabs"), ("q", "close")]
        return [("hjkl w b", "move"), ("v/V", "select"), ("y", "copy"), ("/ ?", "search"), ("esc", "back")]

    def list_key(self, k):
        """Keys while the run list has the focus (newest run on top)."""
        if self.pending == "g":
            self.pending = ""
            if k == "g":
                self.select(len(self.runs) - 1)
            return
        if k == "j":  # down the list: an older run
            self.select(max(0, self.tab - 1))
        elif k == "k":  # up the list: a newer run
            self.select(min(len(self.runs) - 1, self.tab + 1))
        elif k == "g":
            self.pending = "g"
        elif k == "G":
            self.select(0)
        elif k in ("\r", "\n", "l"):  # into the output, cursor on its last line
            self.focus, self.follow, self.cx = "log", False, 0

    def click(self, row, col):
        """A click on the run list opens that run; in the log it moves the cursor."""
        listed = getattr(self, "listed", [])
        if self.runs and 0 <= row - HEAD_ROW - 1 < len(listed):  # below the box's top border
            # A click opens the run, as enter does: the cursor goes into its output.
            self.select(listed[row - HEAD_ROW - 1])
            self.focus, self.follow, self.cx = "log", True, 0  # at the end of its output
        elif row >= getattr(self, "body_row", 99) and self.plain:
            self.focus, self.anchor = "log", None
            self.cy, self.cx, self.follow = self.offset + row - self.body_row, col - 1, False
            self.clamp()
            self.dragging = (self.cy, self.cx)

    def drag(self, row, col):
        """Dragging in the log selects from where the button went down, as in herdr."""
        if getattr(self, "dragging", None) is None:
            return
        self.cy, self.cx = self.offset + row - self.body_row, col - 1
        self.clamp()
        self.anchor, self.linewise = self.dragging, False

    def release(self):
        """Letting go copies what was dragged over (herdr's copy on select); it stays marked."""
        if getattr(self, "dragging", None) is not None and self.anchor is not None:
            text = self.selection()
            copy(text)
            # A tooltip over the selection for a moment, not a note in the info line.
            self.tooltip = (f"copied {len(text)} character{'s' if len(text) != 1 else ''}", time.time() + 1.5)
        self.dragging = None

    def wheel(self, down, row=0):
        """The wheel over the run list moves through the runs, as j/k do; elsewhere it scrolls the log."""
        if self.runs and HEAD_ROW < row <= HEAD_ROW + len(getattr(self, "listed", [])):
            self.focus = "list"
            self.list_key("j" if down else "k")
            return
        self.focus, self.follow = "log", False
        self.cy += 3 if down else -3
        self.clamp()
        if down and self.cy >= len(self.plain) - 1:
            self.follow = True


class Servers(Runs):
    """The dev servers of the project, or started from this workspace, as a run
    list with each server's log under it; rescanned every 2s."""

    noun = "servers"

    def __init__(self):
        super().__init__("servers")
        self.empty = "no dev servers running in this project"
        self.found, self.at, self.scanning = [], 0.0, False

    def refresh(self):
        # lsof takes ~0.2s: scan in a thread so the deck never waits on it.
        if time.time() - self.at >= 2 and not self.scanning:
            self.scanning = True
            threading.Thread(target=self.scan, daemon=True).start()
        # Keep each server's object (and its log) and the one picked; lowest port on top.
        keep = {(r.port, r.pid): r for r in self.runs}
        picked = (self.runs[self.tab].port, self.runs[self.tab].pid) if self.runs else None
        self.runs = [keep.get((port, pid)) or ServerRun(port, pid, cmd)
                     for port, pid, cmd in sorted(self.found, key=lambda s: -int(s[0]))]
        keys = [(r.port, r.pid) for r in self.runs]
        if picked in keys:
            self.tab = keys.index(picked)
        elif self.runs:
            self.select(len(self.runs) - 1)
        return False

    def scan(self):
        root, out = read(os.path.join(BASE, "root")), ""
        # The shells of the agent's workspace: servers started from any of them count too.
        agent = os.path.basename(BASE).replace("_", ":")
        panes = herdr_panes()
        ws = next((w for p, w, _, _ in panes if p == agent), None)
        shells = [shell for _, w, shell, _ in panes if ws and w == ws]
        if root:
            try:
                out = subprocess.run([SERVERS, root, *shells], capture_output=True, text=True, timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                pass
        self.found = [l.split("\t", 2) for l in out.splitlines() if l.count("\t") == 2]
        self.at, self.scanning = time.time(), False

    def badge(self):
        return ("●", GREEN_RGB) if self.runs else ("·", MUTED_RGB)

    def frame(self, cols, rows):
        # Reading the log (not following its end): a pane log stays as it was until
        # esc, since each re-read of the pane's last lines would slide it under the cursor.
        for i, run in enumerate(self.runs):
            run.hold = i == self.tab and self.focus == "log" and not self.follow
        return super().frame(cols, rows)

    def hints(self):
        if self.focus == "list" and self.search is None:
            return [("j/k", "select"), ("enter", "log"), ("o", "browser"), ("[ ]", "tabs"), ("q", "close")]
        return super().hints()

    def list_key(self, k):
        if k == "o" and self.runs:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, f"http://localhost:{self.runs[self.tab].port}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            super().list_key(k)


# Subagents ------------------------------------------------------------------
# What the agent's Claude Code subagents are doing, read from its session on
# disk the way zoetrope does (github.com/furkankly/zoetrope):
#   ~/.claude/projects/<project>/<session>.jsonl            the main transcript
#   ~/.claude/projects/<project>/<session>/subagents/       one transcript per subagent,
#       agent-<id>.jsonl + agent-<id>.meta.json             (type, description, toolUseId, depth),
#       workflows/<run>/agent-<id>.jsonl                    the agents of a workflow run.
# A foreground subagent has ended when the main transcript answers its Agent call
# (tool_result for its toolUseId; is_error = failed); a background one when a
# <task-notification> names it. Otherwise it is running while its file moves.

LIVE_SECS = 300  # a subagent unended and silent this long reads as idle
HARNESS_TOOLS = {"SubagentHandback"}  # the harness's own steps, not the agent's work
TOOL_KINDS = {"Read": "read", "NotebookRead": "read", "Grep": "search", "Glob": "search", "WebSearch": "search",
              "WebFetch": "search", "ToolSearch": "search", "Edit": "edit", "MultiEdit": "edit", "Write": "edit",
              "NotebookEdit": "edit", "Bash": "run", "Agent": "agent", "Task": "agent", "Workflow": "agent"}


def duration(secs):
    secs = int(max(0, secs))
    for unit, size in (("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} {secs % size // 60 if unit == 'h' else secs % 60}{'m' if unit == 'h' else 's'}"
    return f"{secs}s"
NOTIFICATION = re.compile(r"<task-id>([^<]+)</task-id>.*?<status>([^<]+)</status>", re.S)


def short(text, cols=80):
    line = " ".join((text or "").split())
    return line if len(line) <= cols else line[:cols - 1] + "…"


def tool_summary(name, args, cwd=""):
    """One line for a tool call, picked as zoetrope does: the field that says what it does."""
    if not isinstance(args, dict):
        return ""
    pick = lambda *keys: next((str(args[k]) for k in keys if isinstance(args.get(k), str) and args[k]), "")
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        path = pick("file_path", "path", "notebook_path")
        return os.path.relpath(path, cwd) if cwd and path.startswith(cwd + "/") else path
    if name == "Bash":
        return pick("command", "description")
    if name in ("Agent", "Task", "Workflow"):
        return pick("description", "subagent_type")
    return pick("pattern", "url", "query", "description", "prompt", "command", "path")


def clock(ts):
    try:
        return time.strftime("%H:%M:%S", time.localtime(datetime_epoch(ts)))
    except (TypeError, ValueError):
        return "        "


def datetime_epoch(ts):
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class AgentRun(Run):
    """One subagent: its row in the list and, as its log, what it has done."""

    def __init__(self, path, deck):
        self.path, self.deck = path, deck   # the agent's transcript; the Subagents view
        self.id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        try:
            with open(path[:-len(".jsonl")] + ".meta.json") as f:
                self.meta = json.load(f)
        except (OSError, ValueError):
            self.meta = {}
        self.tag, self.is_git = "", False
        self.full = self.cmd = self.meta.get("description") or self.id
        self._log, self._loading, self.prompt, self.cols, self.parsed = (None, None, None, 0), None, "", 80, None
        self.first = os.path.getmtime(path)  # until the transcript says when it began
        self.lines()  # read it now: the list orders and dates agents by their first line

    def ended(self):
        """"done", "failed" or "stopped" once the main transcript says so, else ""."""
        if self.meta.get("requestShape") != "background":
            hit = self.deck.results.get(self.meta.get("toolUseId", ""))
            if hit is not None:
                return "failed" if hit else "done"
        return self.deck.notified.get(self.id, "")

    def status(self):
        end = self.ended()
        if end:
            return {"done": ("✓", GREEN_RGB), "failed": ("✗", RED_RGB)}.get(end, ("⊘", MUTED_RGB))
        try:
            quiet = time.time() - os.path.getmtime(self.path)
        except OSError:
            quiet = LIVE_SECS
        return (spinner(), AMBER_RGB) if quiet < LIVE_SECS else ("·", MUTED_RGB)

    def rc(self):
        return {"done": "0", "failed": "1"}.get(self.ended(), "")

    def source(self):
        return ""

    def end(self):
        return ""

    def label(self):
        depth = int(self.meta.get("spawnDepth") or 1)
        return "  " * (depth - 1) + short(self.full, 200)

    def age(self):
        kind = self.meta.get("agentType") or "agent"
        s = int(time.time() - self.first)
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if s >= size:
                return f"{kind}  {s // size}{unit} ago"
        return f"{kind}  {s}s ago"

    def info(self):
        self.lines()
        calls = self._log[3]
        end = self.ended() or ("running" if self.status()[1] == AMBER_RGB else "idle")
        parts = [self.meta.get("agentType") or "agent", f"{calls} tool call{'s' * (calls != 1)}", end]
        if self.meta.get("requestShape") == "background":
            parts.insert(1, "background")
        return f"{DIM}  {' · '.join(parts)}{RESET}"

    def hash(self):
        return ""

    def command(self, cols):
        """The head: the agent's description. (Its prompt opens the log, which scrolls.)"""
        if cols != self.cols:  # the log's prompt is wrapped to the pane: wrap it again
            self.cols = cols
        return [f"{BOLD}{line}{RESET}" for line in textwrap.wrap(self.full, max(10, cols))[:2]]

    def lines(self):
        """The log: what the agent is doing now, its latest message, how far it has got,
        the files it changed, then its whole prompt. Parsed when the transcript changes,
        laid out on every call so the clocks tick."""
        try:
            st = os.stat(self.path)
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return []
        if key != self._log[0] and self._loading != key:
            self._loading = key
            background(self._load, self.path, key)
        if self.parsed is None:
            return None
        shown = self.summary(self.parsed)
        self._log = (self._log[0], shown, [ANSI.sub("", l) for l in shown], self.parsed["calls"])
        return shown

    def summary(self, p):
        width = max(20, self.cols)
        label = lambda name: f"{fg(MUTED_RGB)}{name:<10}{RESET}"
        pad = " " * 10
        out = []
        tool = p["last_tool"]
        if tool:
            name, what, ts, done = tool
            since = duration(time.time() - ts) if ts else ""
            if done is None and self.status()[1] == AMBER_RGB:
                mark, when = f"{fg(AMBER_RGB)}{spinner()}", f"{since}"
            else:
                mark = f"{fg(RED_RGB)}✗" if done else f"{fg(GREEN_RGB)}✓"
                when = f"{since} ago" if since else ""
            line = f"{mark} {fg(ACCENT['subagents'])}{name}{RESET} {what}"
            out.append(label("now") + fit(line, width - 10 - len(when) - 3) + f"{fg(MUTED_RGB)} · {when}{RESET}")
        else:
            out.append(label("now") + f"{DIM}starting{RESET}")
        says = textwrap.wrap(" ".join(p["says"].split()), width - 10)[:4] if p["says"] else []
        for n, part in enumerate(says or [f"{DIM}—{RESET}"]):
            out.append((label("says") if n == 0 else pad) + part)
        end = time.time() if not self.ended() and p["first"] else (p["last"] or time.time())
        took = duration(end - p["first"]) if p["first"] else ""
        counts = " · ".join(f"{kind} {n}" for kind, n in sorted(p["kinds"].items(), key=lambda kv: -kv[1]))
        out.append(label("progress") + " · ".join(x for x in (took, counts) if x))
        for n, path in enumerate(p["changed"] or ["—"]):
            out.append((label("changed") if n == 0 else pad) + (f"{fg(AMBER_RGB)}✎ {RESET}{path}" if p["changed"] else f"{DIM}{path}{RESET}"))
        out.append(rule(width))
        bar = f"{fg(ACCENT['subagents'])}│ {RESET}"
        out += [bar + part for line in p["prompt"].strip().splitlines()
                for part in (textwrap.wrap(line, width - 2) or [""])]
        return out

    def _load(self, path, key):
        """Read the agent's transcript into what the summary needs."""
        prompt, says, last_tool, results, kinds, changed, calls = "", "", None, {}, {}, [], 0
        try:
            with open(path) as f:
                rows = [json.loads(l) for l in f if l.strip()]
        except (OSError, ValueError):
            rows = []
        cwd = next((r.get("cwd") for r in rows if r.get("cwd")), "")
        stamps = [r["timestamp"] for r in rows if r.get("timestamp")]
        for r in rows:
            content = (r.get("message") or {}).get("content")
            if r.get("type") == "user":
                if isinstance(content, str) and not prompt:
                    prompt = content
                elif isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            results[b.get("tool_use_id")] = bool(b.get("is_error"))
            elif r.get("type") == "assistant" and isinstance(content, list):
                for b in content:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        says = b["text"]
                    elif b.get("type") == "tool_use" and b.get("name") not in HARNESS_TOOLS:
                        name = b.get("name", "")
                        what = tool_summary(name, b.get("input"), cwd)
                        last_tool = (name, what, r.get("timestamp"), b.get("id"))
                        kind = TOOL_KINDS.get(name, "other")
                        kinds[kind] = kinds.get(kind, 0) + 1
                        calls += 1
                        if kind == "edit" and what and what not in changed:
                            changed.append(what)
        if last_tool:
            name, what, ts, call = last_tool
            try:
                ts = datetime_epoch(ts) if ts else None
            except ValueError:
                ts = None
            last_tool = (name, what, ts, results.get(call))
        epoch = lambda t: datetime_epoch(t) if t else None
        try:
            first, last = (epoch(stamps[0]), epoch(stamps[-1])) if stamps else (None, None)
        except ValueError:
            first = last = None
        if first:
            self.first = first
        self.prompt = prompt
        self.parsed = {"prompt": prompt, "says": says, "last_tool": last_tool, "kinds": kinds,
                       "changed": changed, "calls": calls, "first": first, "last": last}
        self._log = (key, None, None, calls)
        if self._loading == key:
            self._loading = None


class Subagents(Runs):
    """The agent's Claude Code subagents: what each is doing, and how it ended."""

    noun = "subagents"

    def __init__(self):
        super().__init__("subagents")
        self.empty = "no subagents at work"
        self.session, self.looked = "", 0.0          # the main transcript, found through herdr
        self.results, self.notified = {}, {}          # what the main transcript says about them
        self.read_to, self.reading = 0, False

    def find_session(self):
        """The newest transcript of the Claude session herdr runs in the agent's pane."""
        agent = os.path.basename(BASE).replace("_", ":")
        try:
            out = subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), "pane", "get", agent],
                                 capture_output=True, text=True, timeout=3).stdout
            ref = json.loads(out)["result"]["pane"].get("agent_session") or {}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            ref = {}
        found = []
        if ref.get("agent") == "claude" and ref.get("value"):
            root = os.path.expanduser("~/.claude/projects")
            try:
                found = [os.path.join(root, d, ref["value"] + ".jsonl") for d in os.listdir(root)]
            except OSError:
                pass
        found = [f for f in found if os.path.isfile(f)]
        session = max(found, key=os.path.getmtime) if found else ""
        if session != self.session:
            self.session, self.results, self.notified, self.read_to = session, {}, {}, 0

    def read_main(self):
        """What the main transcript has said since the last read: answers to Agent calls
        and task notifications (a big transcript is read once, then only its new lines)."""
        try:
            with open(self.session, "rb") as f:
                f.seek(self.read_to)
                data = f.read()
        except OSError:
            data = b""
        done = data.rfind(b"\n") + 1  # leave a half-written last line for next time
        for raw in data[:done].splitlines():
            if b"tool_result" not in raw and b"task-notification" not in raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            content = (row.get("message") or {}).get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            # A notification for a busy session waits in the queue first (a top-level
            # "content", type queue-operation) and reaches the messages only later.
            if isinstance(row.get("content"), str):
                blocks.append({"type": "text", "text": row["content"]})
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    self.results[b.get("tool_use_id")] = bool(b.get("is_error"))
                text = b.get("text") if b.get("type") == "text" else None
                if isinstance(text, str) and "<task-notification>" in text:
                    for agent, status in NOTIFICATION.findall(text):
                        self.notified[agent.strip()] = {"completed": "done", "failed": "failed"}.get(
                            status.strip(), "stopped" if status.strip() in ("stopped", "killed") else "")
        self.read_to += done
        self.reading = False

    def refresh(self):
        if time.time() - self.looked >= 5:
            self.looked = time.time()
            background(self.find_session)
        if self.session and not self.reading:
            self.reading = True
            background(self.read_main)
        folder = self.session[:-len(".jsonl")] + "/subagents" if self.session else ""
        paths = glob.glob(f"{folder}/agent-*.jsonl") + glob.glob(f"{folder}/workflows/*/agent-*.jsonl") if folder else []
        keep = {r.path: r for r in getattr(self, "every", [])}
        self.every = [keep.get(p) or AgentRun(p, self) for p in paths]
        # Only the subagents at work now: an ended or long-quiet one leaves the list.
        runs = sorted((r for r in self.every if r.status()[1] == AMBER_RGB), key=lambda r: r.first)
        new = self.known is not None and any(r.path not in self.known for r in runs)
        picked = self.runs[self.tab].path if self.runs else None
        self.runs = runs
        paths = [r.path for r in runs]
        if self.known is None or new or picked not in paths:
            self.select(len(runs) - 1)  # the newest subagent, on top
        else:
            self.tab = paths.index(picked)
        self.known = set(paths)
        return new

    def badge(self):
        return (spinner(), AMBER_RGB) if self.runs else ("·", MUTED_RGB)


class Deck:
    def __init__(self):
        self.views = [Subagents(), Servers()] + [Runs(k) for k in SECTIONS[2:]]
        self.section = 0
        self.last_frame = None
        self.focus_path = os.path.join(BASE, "focus")
        # The section last picked by hand in any deck, shared by all of them.
        self.shared_path = os.path.join(os.path.dirname(BASE), "section")
        self.focus_at = self.mtime(self.focus_path)
        self.shared_at = self.mtime(self.shared_path)
        # Open on whichever is newer: the section that asked for this deck, or
        # the one last picked anywhere.
        latest = self.focus_path if self.focus_at > self.shared_at else self.shared_path
        if read(latest) in SECTIONS:
            self.section = SECTIONS.index(read(latest))

    @staticmethod
    def mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0

    def choose(self, i):
        """A section picked by hand: show it here and in every other deck."""
        self.section = i % len(self.views)
        try:
            with open(self.shared_path, "w") as f:
                f.write(SECTIONS[self.section])
            self.shared_at = self.mtime(self.shared_path)
        except OSError:
            pass

    def refresh(self):
        for i, view in enumerate(self.views):
            if view.refresh():  # a new run brings its section forward
                self.section = i
        at = self.mtime(self.focus_path)
        if at != self.focus_at:  # a section was asked for (e.g. a dev server started)
            self.focus_at = at
            if read(self.focus_path) in SECTIONS:
                self.section = SECTIONS.index(read(self.focus_path))
        at = self.mtime(self.shared_path)
        if at != self.shared_at:  # picked by hand in another deck
            self.shared_at = at
            if read(self.shared_path) in SECTIONS:
                self.section = SECTIONS.index(read(self.shared_path))

    def section_bar(self, cols):
        cells = []
        for i, view in enumerate(self.views):
            icon, colour = view.badge()
            name = view.kind.upper()
            # No number on the tab (1-6 still jump there): more fit a narrow pane.
            if i == self.section:
                text = f"{bg(ACCENT[view.kind])}{fg(STRIP_RGB)}{BOLD} {name} {icon} \033[22m"
            else:
                text = f"{bg(TAB_RGB)}{fg(MUTED_RGB)} {name} {fg(colour)}{icon} "
            cells.append((len(name) + 4, text))
        bar, self.spans = strip(cells, cols, self.section)
        return bar

    def draw(self):
        cols, rows = shutil.get_terminal_size()
        cols -= 1  # herdr's pane scrollbar covers the last column
        view = self.views[self.section]
        head, body = view.frame(cols, rows - 1)  # the last row holds the key hints
        # Overwrite in place (each line clears its own tail, \033[J the rest)
        # instead of clearing the screen, so nothing flashes between frames.
        out = [self.section_bar(cols), "\033[K"]
        for line in head + body:
            out += ["\r\n", fit(line, cols), RESET, "\033[K"]
        out.append("\033[J")
        hints = "  ".join(f"{fg(BRIGHT_RGB)}{key} {fg(MUTED_RGB)}{what}" for key, what in view.hints())
        out += [f"\033[{rows};1H", bg(STRIP_RGB), " ", fit(hints, cols - 1), bg(STRIP_RGB), "\033[K", RESET]
        frame = "".join(out)
        if frame == self.last_frame:  # nothing changed: leave the screen alone
            return
        self.last_frame = frame
        # Synchronized output: terminals that support it show the frame at once.
        sys.stdout.write("\033[?2026h\033[H" + frame + "\033[?2026l")
        sys.stdout.flush()

    def mouse(self, button, col, row):
        """SGR mouse report: 0 = left press, 32 = drag with it held, 64/65 = wheel up/down."""
        if button == 32:
            getattr(self.views[self.section], "drag", lambda r, c: None)(row, col)
        elif button in (64, 65):
            self.views[self.section].wheel(button == 65, row)
        elif button == 0 and row == 1:
            for i, x0, x1 in self.spans:
                if x0 <= col <= x1:
                    self.choose(i)
        elif button == 0:
            self.views[self.section].click(row, col)

    def key(self, k):
        view = self.views[self.section]
        if getattr(view, "search", None) is not None:  # typing a search: every key is text
            view.key(k)
            return
        if k in ("\t", "]"):
            self.choose(self.section + 1)
        elif k in ("\x1b[Z", "["):
            self.choose(self.section - 1)
        elif k.isdigit() and 1 <= int(k) <= len(self.views):
            self.choose(int(k) - 1)
        else:
            self.views[self.section].key(k)


def main():
    deck = Deck()
    me = os.path.abspath(__file__)
    born = os.path.getmtime(me)
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    def restore():
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\033[?1006l\033[?1002l\033[?1000l\033[?7h\033[?25h\033[?1049l")
        sys.stdout.flush()

    # Alternate screen, hidden cursor, no autowrap (long lines are cut, not
    # wrapped), and SGR mouse reports so clicks and the wheel reach the deck.
    sys.stdout.write("\033[?1049h\033[?25l\033[?7l\033[?1000h\033[?1002h\033[?1006h")
    try:
        tty.setcbreak(fd)
        while True:
            if os.path.getmtime(me) != born:  # edited: restart in place with the new code
                restore()
                os.execv(sys.executable, [sys.executable, me] + sys.argv[1:])
            deck.refresh()
            deck.draw()
            if select.select([fd], [], [], 0.3)[0]:
                k = os.read(fd, 256).decode("utf-8", "replace")
                events = MOUSE.findall(k)
                for button, col, row, kind in events:
                    if kind == "M":  # press or drag
                        deck.mouse(int(button), int(col), int(row))
                    elif int(button) == 0:  # left button let go
                        getattr(deck.views[deck.section], "release", lambda: None)()
                if events:
                    continue
                # One read can hold several keys (fast presses, arrows): take them one at a time.
                for key in KEYS.findall(k):
                    typing = getattr(deck.views[deck.section], "search", None) is not None
                    if key == "q" and not typing:
                        return
                    deck.key(key if typing else ARROWS.get(key, key))
    except KeyboardInterrupt:
        pass
    finally:
        restore()


if __name__ == "__main__":
    main()
