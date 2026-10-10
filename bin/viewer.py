#!/usr/bin/env python3
"""Herdeck deck: one read-only pane beside the agent with four sections.

    viewer.py <base>

<base>/<kind>/runs/<n>/ holds one run of kind build, test or git (written by
bin/run and bin/record): cmd, cwd, log, start, pid, and once finished rc and
end. The servers section lists the project's dev servers (bin/servers on
<base>/root). A new run, or a write to <base>/focus naming a section, brings
that section forward; a new run is also followed live.

Keys: tab/shift-tab or 1-4 switch sections, [ ] switch runs, x drop the run,
q close the pane. Run logs take herdr's copy-mode keys: h j k l, w b e, W B E,
0 ^ $, { }, gg G, ctrl-d/u, v V select, y yank (the line when nothing is
selected), / ? search, n N repeat, esc cancel. In servers, j/k pick a server and
enter/o opens it.
Mouse: click a section or run tab to switch, click in a log to place the
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
import time
import tty

BASE = sys.argv[1]
SERVERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servers")
SECTIONS = ["servers", "build", "test", "git"]
# Screen rows (1-based, as in mouse reports): section tabs, a blank row, the
# section's own strip (run tabs), the info line, a rule, then the body.
STRIP_ROW, BODY_ROW = 3, 6

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
STRIP_RGB, TAB_RGB, ACTIVE_RGB = "33;37;43", "44;49;58", "62;68;81"
MUTED_RGB, BRIGHT_RGB = "120;127;140", "220;223;228"
GREEN_RGB, RED_RGB, AMBER_RGB, CYAN_RGB = "152;177;125", "208;134;141", "216;180;106", "127;180;180"
ACCENT = {"servers": GREEN_RGB, "build": AMBER_RGB, "test": "122;162;200", "git": "180;142;173"}
MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
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


class Run:
    def __init__(self, path):
        self.path = path
        # Shown on one line: a multi-line command (a heredoc, say) gets ⏎ marks.
        self.cmd = " ⏎ ".join(read(os.path.join(path, "cmd")).splitlines())

    def status(self):
        rc = read(os.path.join(self.path, "rc"))
        if rc:
            return ("✓", GREEN_RGB) if rc == "0" else ("✗", RED_RGB)
        try:
            os.kill(int(read(os.path.join(self.path, "pid"), "0")), 0)
            return ("⋯", AMBER_RGB)
        except (OSError, ValueError):
            return ("⊘", MUTED_RGB)  # the runner died without an exit code

    def label(self):
        cmd = re.sub(r"^cd \S+ && ", "", self.cmd)
        cmd = re.sub(r"^([A-Za-z_]\w*=\S* )+", "", cmd)
        cmd = re.sub(r"^(npx|bunx|pnpm( exec| dlx)?|yarn|uv run|poetry run|python3? -m) ", "", cmd)
        return cmd if len(cmd) <= 22 else cmd[:21] + "…"

    def info(self):
        start = int(read(os.path.join(self.path, "start"), "0") or 0)
        end = read(os.path.join(self.path, "end"))
        secs = (int(end) if end else int(time.time())) - start
        rc = read(os.path.join(self.path, "rc"))
        tail = f" · exit {rc}" if rc else " · running"
        return f"$ {self.cmd}  {DIM}· {read(os.path.join(self.path, 'cwd'))} · {secs}s{tail}{RESET}"

    def lines(self):
        try:
            with open(os.path.join(self.path, "log"), "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return []
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
        self.spans = []
        if not self.runs:
            return f"{bg(STRIP_RGB)}\033[K{RESET}", f"{DIM}no {self.kind} runs yet{RESET}", []
        cells = []
        for i, run in enumerate(self.runs):
            icon, colour = run.status()
            label = run.label()
            if len(label) > cols - 9:
                label = label[:max(1, cols - 10)] + "…"
            if i == self.tab:
                text = f"{bg(ACTIVE_RGB)}{fg(ACCENT[self.kind])}▎{fg(colour)}{icon}{fg(BRIGHT_RGB)}{BOLD} {label} \033[22m"
            else:
                text = f"{bg(TAB_RGB)} {fg(colour)}{icon}{fg(MUTED_RGB)} {label} "
            cells.append((len(label) + 4, text))
        run = self.runs[self.tab]
        self.raw = run.lines()
        self.plain = [ANSI.sub("", l) for l in self.raw]
        self.height = height = max(1, rows - BODY_ROW + 1)
        if self.follow:  # pinned to the end while the run grows
            self.cy = max(0, len(self.plain) - 1)
        self.clamp()
        if self.cy < self.offset:
            self.offset = self.cy
        elif self.cy >= self.offset + height:
            self.offset = self.cy - height + 1
        self.offset = max(0, min(self.offset, max(0, len(self.plain) - height)))

        body = []
        for y in range(self.offset, min(len(self.raw), self.offset + height)):
            if y == self.cy or self.selected(y, 0) or self.selected(y, len(self.plain[y]) - 1):
                body.append(self.marked(y))
            else:
                body.append(self.raw[y])
        if self.search is not None:
            info = f"{fg(ACCENT[self.kind])}{'?' if self.back else '/'}{RESET}{self.search}▏"
        else:
            mode = ("-- VISUAL LINE -- " if self.linewise else "-- VISUAL -- ") if self.anchor else ""
            note = f"{fg(AMBER_RGB)}{self.msg}{RESET}  " if self.msg else ""
            info = f"{BOLD}{mode}{RESET}{note}{run.info()}"
        bar, self.spans = strip(cells, cols, self.tab)
        return bar, info, body

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
            self.anchor = None
        elif k in ("/", "?"):
            self.search, self.back = "", k == "?"
        elif k == "]":
            self.select(min(len(self.runs) - 1, self.tab + 1))
        elif k == "[":
            self.select(max(0, self.tab - 1))
        elif k == "x":
            shutil.rmtree(self.runs[self.tab].path, ignore_errors=True)

    def click(self, row, col):
        """A click on the run strip switches runs; in the log it moves the cursor."""
        if row == STRIP_ROW:
            for i, x0, x1 in getattr(self, "spans", []):
                if x0 <= col <= x1:
                    self.select(i)
        elif row >= BODY_ROW and self.plain:
            self.cy, self.cx, self.follow = self.offset + row - BODY_ROW, col - 1, False
            self.clamp()

    def wheel(self, down):
        self.follow = False
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

    def frame(self, cols, rows):
        n = len(self.servers)
        line2 = f"{bg(STRIP_RGB)}{fg(MUTED_RGB)} {n} running\033[K{RESET}"
        info = f"{DIM}{read(os.path.join(BASE, 'root'))}{RESET}"
        if not n:
            return line2, info, [f"{DIM}no dev servers running in this project{RESET}"]
        body = []
        for i, (port, pid, cmd) in enumerate(self.servers):
            url = f"http://localhost:{port}"
            mark = f"{fg(ACCENT['servers'])}▎{RESET}" if i == self.pick else " "
            body.append(f"{mark}{fg(GREEN_RGB)}●{RESET} {link(url, fg(CYAN_RGB) + url + RESET)}"
                        f"  {cmd}  {DIM}pid {pid}{RESET}")
        return line2, info, body[:max(1, rows - BODY_ROW + 1)]

    def click(self, row, col):
        """A click on a server row opens that server."""
        if 0 <= row - BODY_ROW < len(self.servers):
            self.pick = row - BODY_ROW
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
        self.focus_path = os.path.join(BASE, "focus")
        self.focus_at = self.focus_mtime()
        if read(self.focus_path) in SECTIONS:  # open on the section that asked for the deck
            self.section = SECTIONS.index(read(self.focus_path))

    def focus_mtime(self):
        try:
            return os.path.getmtime(self.focus_path)
        except OSError:
            return 0

    def refresh(self):
        for i, view in enumerate(self.views):
            if view.refresh():  # a new run brings its section forward
                self.section = i
        at = self.focus_mtime()
        if at != self.focus_at:  # a section was asked for (e.g. a dev server started)
            self.focus_at = at
            if read(self.focus_path) in SECTIONS:
                self.section = SECTIONS.index(read(self.focus_path))

    def section_bar(self, cols):
        cells = []
        for i, view in enumerate(self.views):
            icon, colour = view.badge()
            name = view.kind.upper()
            if i == self.section:
                text = f"{bg(ACCENT[view.kind])}{fg(STRIP_RGB)}{BOLD} {i + 1} {name} {icon} \033[22m"
            else:
                text = f"{bg(TAB_RGB)}{fg(MUTED_RGB)} {i + 1} {name} {fg(colour)}{icon} "
            cells.append((len(name) + 6, text))
        bar, self.spans = strip(cells, cols, self.section)
        return bar

    def draw(self):
        cols, rows = shutil.get_terminal_size()
        line2, info, body = self.views[self.section].frame(cols, rows)
        out = ["\033[H\033[2J", self.section_bar(cols), "\r\n\r\n", line2, "\r\n", fit(info, cols),
               "\r\n", DIM, "─" * cols, RESET]
        for line in body:
            out += ["\r\n", fit(line, cols), RESET]
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def mouse(self, button, col, row):
        """SGR mouse report: 0 = left press, 64/65 = wheel up/down."""
        if button in (64, 65):
            self.views[self.section].wheel(button == 65)
        elif button == 0 and row == 1:
            for i, x0, x1 in self.spans:
                if x0 <= col <= x1:
                    self.section = i
        elif button == 0:
            self.views[self.section].click(row, col)

    def key(self, k):
        if k == "\t":
            self.section = (self.section + 1) % len(self.views)
        elif k == "\x1b[Z":
            self.section = (self.section - 1) % len(self.views)
        elif k in ("1", "2", "3", "4"):
            self.section = int(k) - 1
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
