#!/usr/bin/env python3
"""Read-only viewer panes beside the agent.

    viewer.py test|build <dir>    the agent's test or build runs, one tab each
    viewer.py servers <dir>       the project's running dev servers

Runs: <dir>/runs/<n>/ holds one run (written by bin/run): cmd, cwd, log, start,
pid, and once finished rc and end. A new run takes the focus and is followed
live. Keys: h/l or tab/shift-tab switch tabs, 1-9 jump, j/k scroll, ctrl-d/u
half page, g/G top/bottom (G follows again), x drop the run, q close the pane.

Servers: bin/servers on <dir>/root, refreshed every 2s. Keys: j/k pick a
server, enter/o open it in the browser, q close the pane.

Standard library only, so it runs on the python3 that macOS and Linux ship.
Editing this file restarts every open viewer with the new code.
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

KIND, DIR = sys.argv[1], sys.argv[2]
RUNS = os.path.join(DIR, "runs")
SERVERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servers")

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
STRIP_RGB, TAB_RGB, ACTIVE_RGB = "33;37;43", "44;49;58", "62;68;81"
MUTED_RGB, BRIGHT_RGB = "120;127;140", "220;223;228"
GREEN_RGB, RED_RGB, AMBER_RGB, CYAN_RGB, BLUE_RGB = "152;177;125", "208;134;141", "216;180;106", "127;180;180", "122;162;200"
ACCENT = {"test": BLUE_RGB, "build": AMBER_RGB, "servers": GREEN_RGB}[KIND]
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


def badge(text):
    return f"{bg(ACCENT)}{fg(STRIP_RGB)}{BOLD} {text} \033[22m{bg(STRIP_RGB)} "


class Run:
    def __init__(self, n):
        self.n, self.path = n, os.path.join(RUNS, str(n))
        self.cmd = read(os.path.join(self.path, "cmd"))

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
        # A carriage return redraws the line: keep what was drawn last.
        return [line.rsplit("\r", 1)[-1] if "\r" in line.rstrip("\r") else line.rstrip("\r")
                for line in text.split("\n")]


class RunsView:
    def __init__(self):
        self.tab = 0
        self.offset, self.follow = 0, True
        self.known = set()
        self.runs = []

    def refresh(self):
        try:
            ns = sorted(int(n) for n in os.listdir(RUNS) if n.isdigit())
        except OSError:
            ns = []
        new = [n for n in ns if n not in self.known]
        self.known = set(ns)
        self.runs = [Run(n) for n in ns]
        if new:  # a new run takes the focus
            self.tab, self.follow = len(self.runs) - 1, True
        self.tab = max(0, min(self.tab, len(self.runs) - 1))

    def tab_bar(self, cols):
        """A KIND badge, then the tabs on a dark strip, the focused one raised."""
        room = cols - (len(KIND) + 3) - 2
        cells = []  # (visible width, rendered)
        for i, run in enumerate(self.runs):
            icon, colour = run.status()
            label = run.label()
            if len(label) > room - 7:  # one tab wider than the strip: cut its label
                label = label[:max(1, room - 8)] + "…"
            num = str(i + 1) if i < 9 else " "
            if i == self.tab:
                text = f"{bg(ACTIVE_RGB)}{fg(ACCENT)}▎{fg(BRIGHT_RGB)}{BOLD}{num} {fg(colour)}{icon}{fg(BRIGHT_RGB)} {label} \033[22m"
            else:
                text = f"{bg(TAB_RGB)}{fg(MUTED_RGB)} {num} {fg(colour)}{icon}{fg(MUTED_RGB)} {label} "
            cells.append((len(num) + len(label) + 5, text))

        # Scroll the strip so the focused tab is visible; ‹ › mark hidden tabs.
        first = 0
        while first < self.tab and sum(w + 1 for w, _ in cells[first:self.tab + 1]) > room:
            first += 1
        out, used, last = [badge(KIND.upper()), f"{fg(MUTED_RGB)}{'‹' if first else ' '}"], 0, first
        for w, text in cells[first:]:
            if used + w + 1 > room:
                break
            out += [text, bg(STRIP_RGB), " "]
            used += w + 1
            last += 1
        out.append(f"{fg(MUTED_RGB)}{'›' if last < len(cells) else ''}\033[K{RESET}")
        return "".join(out)

    def frame(self, cols, rows):
        if self.runs:
            run = self.runs[self.tab]
            info, body = run.info(), run.lines()
        else:
            info, body = f"{DIM}no {KIND} runs yet{RESET}", []
        height = max(1, rows - 3)
        top = max(0, len(body) - height)
        if self.follow:
            self.offset = top
        self.offset = max(0, min(self.offset, top))
        self.height, self.top = height, top
        return self.tab_bar(cols), info, body[self.offset:self.offset + height]

    def key(self, k):
        last = len(self.runs) - 1
        if k in ("l", "\t"):
            self.tab, self.follow = min(last, self.tab + 1), True
        elif k in ("h", "\x1b[Z"):
            self.tab, self.follow = max(0, self.tab - 1), True
        elif k.isdigit() and k != "0":
            self.tab, self.follow = min(last, int(k) - 1), True
        elif k == "j":
            self.offset, self.follow = self.offset + 1, self.offset + 1 >= self.top
        elif k == "k":
            self.offset, self.follow = max(0, self.offset - 1), False
        elif k == "\x04":
            self.offset += self.height // 2
            self.follow = self.offset >= self.top
        elif k == "\x15":
            self.offset, self.follow = max(0, self.offset - self.height // 2), False
        elif k == "g":
            self.offset, self.follow = 0, False
        elif k == "G":
            self.follow = True
        elif k == "x" and self.runs:
            shutil.rmtree(self.runs[self.tab].path, ignore_errors=True)


class ServersView:
    def __init__(self):
        self.servers, self.at, self.pick = [], 0.0, 0

    def refresh(self):
        if time.time() - self.at < 2:
            return
        root, out = read(os.path.join(DIR, "root")), ""
        if root:
            try:
                out = subprocess.run([SERVERS, root], capture_output=True, text=True, timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                pass
        self.servers = [l.split("\t", 2) for l in out.splitlines() if l.count("\t") == 2]
        self.pick = max(0, min(self.pick, len(self.servers) - 1))
        self.at = time.time()

    def frame(self, cols, rows):
        n = len(self.servers)
        bar = f"{badge('SERVERS')}{fg(MUTED_RGB)} {n} running\033[K{RESET}"
        info = f"{DIM}{read(os.path.join(DIR, 'root'))}{RESET}"
        if not n:
            return bar, info, [f"{DIM}no dev servers running in this project{RESET}"]
        body = []
        for i, (port, pid, cmd) in enumerate(self.servers):
            url = f"http://localhost:{port}"
            mark = f"{fg(ACCENT)}▎{RESET}" if i == self.pick else " "
            body.append(f"{mark}{fg(GREEN_RGB)}●{RESET} {link(url, fg(CYAN_RGB) + url + RESET)}"
                        f"  {cmd}  {DIM}pid {pid}{RESET}")
        return bar, info, body[:max(1, rows - 3)]

    def key(self, k):
        if k == "j":
            self.pick = min(len(self.servers) - 1, self.pick + 1)
        elif k == "k":
            self.pick = max(0, self.pick - 1)
        elif k in ("\r", "\n", "o") and self.servers:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, f"http://localhost:{self.servers[self.pick][0]}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def draw(view):
    cols, rows = shutil.get_terminal_size()
    bar, info, body = view.frame(cols, rows)
    out = ["\033[H\033[2J", bar, "\r\n", fit(info, cols), "\r\n", DIM, "─" * cols, RESET]
    for line in body:
        out += ["\r\n", fit(line, cols), RESET]
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def main():
    view = ServersView() if KIND == "servers" else RunsView()
    me = os.path.abspath(__file__)
    born = os.path.getmtime(me)
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    def restore():
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\033[?7h\033[?25h\033[?1049l")
        sys.stdout.flush()

    # Alternate screen, hidden cursor, no autowrap: long lines are cut, not wrapped.
    sys.stdout.write("\033[?1049h\033[?25l\033[?7l")
    try:
        tty.setcbreak(fd)
        while True:
            if os.path.getmtime(me) != born:  # edited: restart in place with the new code
                restore()
                os.execv(sys.executable, [sys.executable, me] + sys.argv[1:])
            view.refresh()
            draw(view)
            if select.select([fd], [], [], 0.3)[0]:
                k = os.read(fd, 16).decode("utf-8", "replace")
                if k == "q":
                    break
                view.key(k)
    except KeyboardInterrupt:
        pass
    finally:
        restore()


if __name__ == "__main__":
    main()
