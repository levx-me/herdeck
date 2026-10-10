#!/usr/bin/env python3
"""Herdeck deck: one read-only pane beside the agent with five sections.

    viewer.py <base>

<base>/<kind>/runs/<n>/ holds one run of kind build, test, git or deploy (written by
bin/run and bin/record): cmd, cwd, log, start, pid, and once finished rc and
end. The servers section lists the project's dev servers (bin/servers on
<base>/root). A new run, or a write to <base>/focus naming a section, brings
that section forward; a new run is also followed live.

Keys: [ ] (or tab/shift-tab) and 1-5 switch sections,
q close the pane. In a run section j/k pick a run from the list and enter (or l)
moves into its output; esc comes back. The output takes herdr's copy-mode keys: h j k l, w b e, W B E,
0 ^ $, { }, gg G, ctrl-d/u, v V select, y yank (the line when nothing is
selected), / ? search, n N repeat, esc cancel. In servers, j/k pick a server and
enter/o opens it.
Mouse: click a section tab or a run in the list to switch, click in a log to place the
cursor, click a server to open it, wheel to scroll.

Standard library only, so it runs on the python3 that macOS and Linux ship.
Editing this file restarts every open deck with the new code.
"""
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import textwrap
import time
import tty

BASE = sys.argv[1]
SERVERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servers")
SECTIONS = ["servers", "build", "test", "git", "deploy"]
# Screen rows (1-based, as in mouse reports): section tabs, then the section's
# head (run list box, command, info line, rule) and its body.
HEAD_ROW = 2
LIST_ROWS = 10  # run list height (rows) before it scrolls

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
STRIP_RGB, TAB_RGB, ACTIVE_RGB = "33;37;43", "44;49;58", "62;68;81"
MUTED_RGB, BRIGHT_RGB = "120;127;140", "220;223;228"
GREEN_RGB, RED_RGB, AMBER_RGB, CYAN_RGB = "152;177;125", "208;134;141", "216;180;106", "127;180;180"
ACCENT = {"servers": GREEN_RGB, "build": AMBER_RGB, "test": "122;162;200", "git": "180;142;173", "deploy": "209;154;102"}
MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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
                return "".join(out) + RESET + "…"
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
        """For git runs, the commit it left behind (from its output)."""
        if os.path.basename(os.path.dirname(os.path.dirname(self.path))) != "git":
            return ""
        return git_hash("\n".join(ANSI.sub("", l) for l in self.lines()))

    def full_hash(self):
        """The whole hash behind the short one, looked up once in the run's repo."""
        if not hasattr(self, "_full"):
            self._full = ""
            short = self.hash()
            dirs = [read(os.path.join(self.path, "cwd"))]
            m = re.match(r"cd\s+(\S+)\s*&&", self.full)
            if m:
                dirs.insert(0, os.path.join(dirs[0], os.path.expanduser(m.group(1))))
            for d in dirs if short else []:
                try:
                    out = subprocess.run(["git", "-C", d, "rev-parse", "--verify", "--quiet", short + "^{commit}"],
                                         capture_output=True, text=True, timeout=2).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    out = ""
                if re.fullmatch(r"[0-9a-f]{40}", out):
                    self._full = out
                    break
        return self._full

    def info(self):
        """Where, how long, and how it ended."""
        start = int(read(os.path.join(self.path, "start"), "0") or 0)
        end = self.end()
        rc = self.rc()
        parts = [read(os.path.join(self.path, "cwd")).replace(os.path.expanduser("~"), "~", 1)]
        if start:
            parts.append(f"{(int(end) if end else int(time.time())) - start}s")
        parts.append(f"exit {rc}" if rc else "running in the background" if self.source() else "running")
        return f"{DIM}  {' · '.join(p for p in parts if p)}{RESET}"

    def lines(self):
        try:
            with open(self.source() or os.path.join(self.path, "log"), "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        text = re.sub(r"\n*\[exited with code \d+\]\s*$", "", text)
        return clean(text, git=os.path.basename(os.path.dirname(os.path.dirname(self.path))) == "git")


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

    def __init__(self, kind):
        self.kind, self.dir = kind, os.path.join(BASE, kind, "runs")
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
        self.runs = [Run(os.path.join(self.dir, str(n))) for n in ns]
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
            return [f"{DIM} no {self.kind} runs yet{RESET}", rule(cols)], []
        run = self.runs[self.tab]
        self.raw = run.lines()
        self.plain = [ANSI.sub("", l) for l in self.raw]
        head = self.run_list(cols)
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
        for y in range(self.offset, min(len(self.raw), self.offset + height)):
            if (cursor and y == self.cy) or self.selected(y, 0) or self.selected(y, len(self.plain[y]) - 1):
                body.append(self.marked(y))
            else:
                body.append(self.raw[y])
        if running:
            start = int(read(os.path.join(run.path, "start"), "0") or 0)
            secs = f" {int(time.time()) - start}s" if start else ""
            body.append(f"{fg(AMBER_RGB)}{spinner()} running{secs}{RESET}")
        return head, body

    def run_list(self, cols):
        """The runs in a rounded box, newest on top, the one on show raised;
        scrolls past LIST_ROWS. The border takes the accent while the list has
        the focus."""
        edge = fg(ACCENT[self.kind]) if self.focus == "list" else fg("70;76;88")
        inner = cols - 2
        title = f" runs · {len(self.runs)} "
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
                right = f"{age}  {sha:7}" if self.kind == "git" else age
                shown = f"{age}  {fg(ACCENT['git'])}{sha:7}" if self.kind == "git" else age
            else:
                icon, colour, right, shown = " ", MUTED_RGB, "", ""
            # "➜ ✓ label<gap>right " and "  ✓ label<gap>right " both fill the line.
            fixed = 5
            room = cols - fixed - 1 - len(right)
            if len(label) > room:
                label = label[:max(1, room - 1)] + "…"
            gap = " " * max(1, cols - fixed - len(label) - len(right))
            if selected:
                arrow = (fg(ACCENT[self.kind]) if self.focus == "list" else fg(MUTED_RGB)) + ("➜" if first else " ")
                lines.append(f"{bg(ACTIVE_RGB)}{arrow}{fg(colour)} {icon} {fg(BRIGHT_RGB)}{BOLD}{label}"
                             f"\033[22m{gap}{fg(MUTED_RGB)}{shown} {RESET}")
            else:
                lines.append(f"  {fg(colour)}{icon} {fg(MUTED_RGB)}{label}{gap}{shown} {RESET}")
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
        """A click on the run list shows that run; in the log it moves the cursor."""
        listed = getattr(self, "listed", [])
        if self.runs and 0 <= row - HEAD_ROW - 1 < len(listed):  # below the box's top border
            self.select(listed[row - HEAD_ROW - 1])
            self.focus = "list"
        elif row >= getattr(self, "body_row", 99) and self.plain:
            self.focus = "log"
            self.cy, self.cx, self.follow = self.offset + row - self.body_row, col - 1, False
            self.clamp()

    def wheel(self, down):
        self.focus, self.follow = "log", False
        self.cy += 3 if down else -3
        self.clamp()
        if down and self.cy >= len(self.plain) - 1:
            self.follow = True


class Servers:
    """The project's running dev servers, refreshed every 2s."""

    kind = "servers"

    def __init__(self):
        self.servers, self.at, self.pick = [], 0.0, 0

    def refresh(self):
        if time.time() - self.at >= 2:
            root, out = read(os.path.join(BASE, "root")), ""
            if root:
                try:
                    out = subprocess.run([SERVERS, root], capture_output=True, text=True, timeout=5).stdout
                except (OSError, subprocess.SubprocessError):
                    pass
            self.servers = [l.split("\t", 2) for l in out.splitlines() if l.count("\t") == 2]
            self.pick = max(0, min(self.pick, len(self.servers) - 1))
            self.at = time.time()
        return False

    def badge(self):
        return ("●", GREEN_RGB) if self.servers else ("·", MUTED_RGB)

    def hints(self):
        return [("j/k", "select"), ("enter", "browser"), ("[ ]", "tabs"), ("q", "close")]

    def frame(self, cols, rows):
        n = len(self.servers)
        head = [f"{fg(MUTED_RGB)} {n} running {DIM}· {read(os.path.join(BASE, 'root'))}{RESET}", rule(cols)]
        self.body_row = HEAD_ROW + len(head)
        if not n:
            return head, [f"{DIM}no dev servers running in this project{RESET}"]
        body = []
        for i, (port, pid, cmd) in enumerate(self.servers):
            url = f"http://localhost:{port}"
            mark = f"{fg(ACCENT['servers'])}▎{RESET}" if i == self.pick else " "
            body.append(f"{mark}{fg(GREEN_RGB)}●{RESET} {link(url, fg(CYAN_RGB) + url + RESET)}"
                        f"  {cmd}  {DIM}pid {pid}{RESET}")
        return head, body[:max(1, rows - self.body_row + 1)]

    def click(self, row, col):
        """A click on a server row opens that server."""
        if 0 <= row - self.body_row < len(self.servers):
            self.pick = row - self.body_row
            self.key("o")

    def wheel(self, down):
        self.key("j" if down else "k")

    def key(self, k):
        if k == "j":
            self.pick = min(len(self.servers) - 1, self.pick + 1)
        elif k == "k":
            self.pick = max(0, self.pick - 1)
        elif k in ("\r", "\n", "o") and self.servers:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, f"http://localhost:{self.servers[self.pick][0]}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Deck:
    def __init__(self):
        self.views = [Servers()] + [Runs(k) for k in SECTIONS[1:]]
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
            # No number on the tab (1-5 still jump there): five fit a narrow pane.
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
        """SGR mouse report: 0 = left press, 64/65 = wheel up/down."""
        if button in (64, 65):
            self.views[self.section].wheel(button == 65)
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
        sys.stdout.write("\033[?1006l\033[?1000l\033[?7h\033[?25h\033[?1049l")
        sys.stdout.flush()

    # Alternate screen, hidden cursor, no autowrap (long lines are cut, not
    # wrapped), and SGR mouse reports so clicks and the wheel reach the deck.
    sys.stdout.write("\033[?1049h\033[?25l\033[?7l\033[?1000h\033[?1006h")
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
                    if kind == "M":  # press; releases (m) are ignored
                        deck.mouse(int(button), int(col), int(row))
                if events:
                    continue
                if k == "q":
                    break
                deck.key(k)
    except KeyboardInterrupt:
        pass
    finally:
        restore()


if __name__ == "__main__":
    main()
