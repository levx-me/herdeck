#!/usr/bin/env python3
"""Draw the README demo, a Claude Code session beside the deck, as docs/demo.svg.

    python3 docs/demo.py > docs/demo.svg

The deck side cycles through its six sections in tab order, each a
layer of the SVG that CSS shows in turn; viewers who ask for reduced motion see
the first one still. Every run of text sits on a character grid at its own x,
so the columns line up whatever monospace font the viewer has. Colours are the
deck's own (bin/viewer.py).
"""
from xml.sax.saxutils import escape

CW, LH, FS = 8.4, 19, 14          # cell width, line height, font size (px)
PAD, TOP = 14, 38                 # inner padding, title bar height
A, D = 90, 60                     # agent and deck columns: 60:40
SCENE_SECS = 3                    # how long each section shows
SPINNER = "·✢✳✶✻✽✻✶✳✢"            # Claude Code's spinner, forward and back
SPIN_SECS = 0.12                  # one spinner frame
TIMER_FROM = 12                   # the "Testing..." timer's first second

BG, STRIP, TAB, ACTIVE = "#1b1e23", "#21252b", "#2c313a", "#3e4451"
MUTED, BRIGHT, TEXT, DIMMED = "#787f8c", "#dcdfe4", "#abb2bf", "#585e69"
GREEN, RED, AMBER, CYAN, BLUE = "#98b17d", "#d0868d", "#d8b46a", "#7fb4b4", "#7aa2c8"
ACCENT = {"AGENTS": "#8fa8d2", "SERVERS": GREEN, "BUILD": AMBER, "TEST": BLUE, "GIT": "#b48ead", "DEPLOY": "#d19a66"}
CLAUDE = "#d77757"                # Claude Code's accent
RULE = "rule"


def tool(dot, name, arg):
    return [("● ", dot), (name, BRIGHT, True), (f"({arg})", TEXT)]


def result(*runs):
    return [("  └ ", MUTED), *runs]


def changed(added, removed):
    return result(("Updated with ", MUTED), (str(added), GREEN), (f" addition{'s' * (added != 1)} and ", MUTED),
                  (str(removed), RED), (f" removal{'s' * (removed != 1)}", MUTED))


# Agent pane: one list of (text, colour[, bold]) runs per row; RULE draws a line.
agent = [
    [],
    [("> ", MUTED), ("add refresh tokens to the auth middleware, with rotation and reuse detection", BRIGHT)],
    [],
    [("● ", BRIGHT), ("I'll add a refresh-token flow beside the access token, then build and test.", TEXT)],
    [],
    tool(GREEN, "Bash", "npm run dev"),
    result(("Running in the background: ", MUTED), ("http://localhost:3000", CYAN)),
    [],
    tool(GREEN, "Agent", "Map the auth tests"),
    result(("Running in the background", MUTED)),
    [],
    tool(GREEN, "Write", "src/auth/refresh.ts"),
    result(("Wrote 64 lines to src/auth/refresh.ts", MUTED)),
    [],
    tool(GREEN, "Update", "src/middleware/auth.ts"),
    changed(18, 4),
    [],
    tool(RED, "Bash", "npm test"),
    result(("Tests  ", TEXT), ("1 failed", RED), (" | ", MUTED), ("44 passed", GREEN), (" (45)", MUTED)),
    [("    +24 lines (ctrl+o to expand)", MUTED)],
    [],
    [("● ", BRIGHT), ("The expiry check rejects a token in its last second: ", TEXT), ("exp < now", CYAN),
     (" should be", TEXT)],
    [("  ", TEXT), ("exp <= now", CYAN), (". Fixing it and running the tests again.", TEXT)],
    [],
    tool(GREEN, "Update", "src/middleware/auth.ts"),
    changed(1, 1),
    [],
    tool(GREEN, "Bash", 'git commit -m "Refresh-token rotation"'),
    result(("[feat/refresh a41c9e2] Refresh-token rotation", MUTED)),
    [],
    tool(MUTED, "Bash", "npm test"),
    result(("Running...", MUTED)),
    [],
    "TESTING",  # the spinner and the timer: animated layers, drawn below
    [],
    RULE,
    [("> ", BRIGHT)],
    RULE,
    [("  ▸▸ ", CLAUDE), ("accept edits on", CLAUDE), (" (shift+tab to cycle)", MUTED)],
]
ROWS = len(agent)

SECTIONS = [("AGENTS", "◐", AMBER), ("SERVERS", "●", GREEN), ("BUILD", "✓", GREEN), ("TEST", "◐", AMBER),
            ("GIT", "✓", GREEN), ("DEPLOY", "·", MUTED)]

out = []


def x(col):
    return PAD + col * CW


def top(row):
    return TOP + PAD + row * LH


def rect(col, row, cols, fill, rows=1, rx=0, stroke=None):
    paint = f'fill="none" stroke="{stroke}"' if stroke else f'fill="{fill}"'
    out.append(f'<rect x="{x(col):.1f}" y="{top(row):.1f}" width="{cols * CW:.1f}" height="{rows * LH:.1f}" '
               f'rx="{rx}" {paint}/>')


def line(col0, row0, col1, row1, colour):
    out.append(f'<line x1="{x(col0):.1f}" y1="{top(row0):.1f}" x2="{x(col1):.1f}" y2="{top(row1):.1f}" '
               f'stroke="{colour}" stroke-width="1"/>')


def text(col, row, runs, limit):
    for run in runs:
        s, colour, bold = run[0], run[1], len(run) > 2
        lead, body = len(s) - len(s.lstrip(" ")), s.strip(" ")
        assert col + len(s) <= limit, f"row {row} runs past column {limit}: {s!r}"
        if body:
            # textLength pins the run to its cells, so a wider font cannot push it out of line.
            weight = ' font-weight="bold"' if bold else ""
            out.append(f'<text x="{x(col + lead):.1f}" y="{top(row) + LH * 0.72:.1f}" '
                       f'textLength="{len(body) * CW:.1f}" lengthAdjust="spacingAndGlyphs" '
                       f'fill="{colour}"{weight}>{escape(body)}</text>')
        col += len(s)


W = x(A + 1 + D) + PAD
H = TOP + PAD * 2 + ROWS * LH
d0, end = A + 1, A + 1 + D

# Window, title bar, pane divider.
out.append(f'<rect width="{W:.0f}" height="{H:.0f}" rx="10" fill="{BG}"/>')
out.append(f'<path d="M10 0H{W - 10:.0f}a10 10 0 0 1 10 10V{TOP}H0V10a10 10 0 0 1 10-10z" fill="{STRIP}"/>')
for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
    out.append(f'<circle cx="{20 + i * 20}" cy="{TOP / 2}" r="6" fill="{c}"/>')
out.append(f'<text x="{W / 2:.0f}" y="{TOP / 2 + 5}" fill="{MUTED}" text-anchor="middle">herdr</text>')
line(A + 0.5, 0, A + 0.5, ROWS, ACTIVE)

css = []


def layers(prefix, n, secs):
    """CSS showing layers {prefix}0..{prefix}{n-1} one at a time over a secs-long
    loop, each for its slot, switching at once (step-end)."""
    css.append(f".{prefix}{{opacity:0;animation-duration:{secs}s;animation-iteration-count:infinite;"
               "animation-timing-function:step-end}" f".{prefix}0{{opacity:1}}")
    for i in range(n):
        a, b = 100 * i / n, 100 * (i + 1) / n
        frames = f"0%{{opacity:{1 if i == 0 else 0}}}" + (f"{a:.3f}%{{opacity:1}}" if i else "")
        frames += f"{b:.3f}%{{opacity:0}}100%{{opacity:0}}"
        css.append(f"@keyframes {prefix}k{i}{{{frames}}}.{prefix}{i}{{animation-name:{prefix}k{i}}}")


def layer(prefix, i, draw):
    out.append(f'<g class="{prefix} {prefix}{i}">')
    draw()
    out.append("</g>")


# Agent pane. "Testing..." spins and counts up as Claude Code's does.
for row, runs_ in enumerate(agent):
    if runs_ == RULE:
        line(0, row + 0.5, A, row + 0.5, MUTED)
    elif runs_ == "TESTING":
        for i, glyph in enumerate(SPINNER):
            layer("p", i, lambda: text(1, row, [(glyph, CLAUDE)], A))
        text(3, row, [("Testing...", CLAUDE), (" (", MUTED)], A)
        cycle = 6 * SCENE_SECS  # the timer counts through one loop of the deck
        for i in range(cycle):
            layer("t", i, lambda: text(15, row, [(f"{TIMER_FROM + i}s", MUTED)], A))
        text(18, row, [(" · esc to interrupt)", MUTED)], A)
        layers("p", len(SPINNER), round(len(SPINNER) * SPIN_SECS, 2))
        layers("t", cycle, cycle)
    else:
        text(1, row, runs_, A)


# The deck's pieces --------------------------------------------------------------

def section_bar(active):
    """The tabs, scrolled as bin/viewer.py's strip() does so the active one shows."""
    rect(d0, 0, D, STRIP)
    widths = [len(name) + 4 for name, _, _ in SECTIONS]
    room = D - 2
    first = 0
    while first < active and sum(w + 1 for w in widths[first:active + 1]) > room:
        first += 1
    col, used = d0 + 1, 0
    text(d0, 0, [("‹" if first else " ", MUTED)], end)
    shown_last = first
    for i in range(first, len(SECTIONS)):
        name, icon, colour = SECTIONS[i]
        if used + widths[i] + 1 > room:
            break
        on = i == active
        rect(col, 0, widths[i], ACCENT[name] if on else TAB)
        text(col, 0, [(f" {name} ", STRIP if on else MUTED, on), (icon, STRIP if on else colour, on)], end)
        col += widths[i] + 1
        used += widths[i] + 1
        shown_last = i
    if shown_last < len(SECTIONS) - 1:
        text(col, 0, [("›", MUTED)], end)


def run_box(rows, accent, noun):
    """The run list: a rounded box, its count in the border, the first row picked."""
    first = 2
    rect(d0 + 0.5, first - 0.5, D - 1, None, rows=len(rows) + 1, rx=5, stroke=accent)
    title = f" {noun} · {sum(1 for r in rows if r[0])} "
    out.append(f'<rect x="{x(d0 + 1.5):.1f}" y="{top(first - 1) + LH * 0.1:.1f}" width="{len(title) * CW:.1f}" '
               f'height="{LH * 0.8:.1f}" fill="{BG}"/>')
    text(d0 + 2, first - 1, [(title, MUTED)], end)
    rect(d0 + 1, first, D - 2, ACTIVE, rows=next((n for n, r in enumerate(rows[1:], 1) if r[0]), len(rows)))
    for i, (icon, colour, label, right) in enumerate(rows):
        left = [("   ", MUTED), (f"{icon} " if icon else "  ", colour), (label, BRIGHT if i == 0 else TEXT, i == 0)]
        width = sum(len(r[0]) for r in left)
        text(d0, first + i, left + [(right.rjust(D - 2 - width - 1), MUTED)], end)
    return first + len(rows) + 1


def hints(pairs):
    rect(d0, ROWS - 1, D, STRIP)
    text(d0, ROWS - 1, [r for k, what in pairs for r in ((f" {k}", BRIGHT), (f" {what} ", MUTED))], end)


def body(row, lines):
    for i, runs_ in enumerate(lines):
        if runs_ == RULE:
            line(d0, row + i + 0.5, end, row + i + 0.5, ACTIVE)
        else:
            text(d0, row + i, runs_, end)


def label(name, *runs):
    return [(f"{name:<10} ", MUTED), *runs]


def bar(parts, width):
    """A stacked bar as runs: [(cells, colour)], the rest of width in track grey."""
    runs = [("█" * n, c) for n, c in parts if n]
    used = sum(n for n, _ in parts)
    return runs + ([("░" * (width - used), TAB)] if width > used else [])


# The scenes ------------------------------------------------------------------

def scene_agents():
    section_bar(0)
    row = run_box([("◐", AMBER, "add refresh tokens to the auth middleware", "2m 14s"),
                   ("◐", AMBER, "  Map the auth tests", "38s")], ACCENT["AGENTS"], "agents")
    pill = [(" ◐ WORKING ", STRIP, True)]
    rect(d0 + 1, row, len(pill[0][0]), AMBER)
    beats = "▂▃▅▇▅▃▂▁▁▂▃▆█▆▃▂▁▂▃▅▃▂▁▁▁▂▄▆▇▅▃▂▃▅▆▅▃▂▁▁▂▃▅▇█▇▅▃▂▃▄▆▅▃▂▁"
    body(row, [
        [(" ", MUTED), *pill, ("   ", MUTED), ("↑", GREEN), (" 1.8k tok/min", MUTED)],
        [],
        [(" " + beats[:D - 2], AMBER)],
        [(" 8m ago", MUTED), ("now".rjust(D - 9), MUTED)],
        [],
        label("model", (" opus-5-5 ", BRIGHT), ("  ", MUTED), (" medium ", AMBER), ("  ✻ thought 1.3s", MUTED)),
        label("context", *bar([(8, GREEN)], 24), (" 34%", GREEN), (" 68k/200k", MUTED)),
        label("tokens", ("in ", MUTED), ("2k  ", TEXT), ("cache ", MUTED), ("1.4M  ", TEXT), ("out ", MUTED), ("18k", TEXT)),
        [],
        label("now", ("◐ ", AMBER), ("Bash ", ACCENT["AGENTS"]), ("npm test", TEXT), (" · 12s", MUTED)),
        label("says", ("“", MUTED), ("Fixing the expiry check and running", TEXT)),
        label("", ("the tests again.", TEXT)),
        label("tasks", *bar([(14, GREEN)], 24), (" 3/5", TEXT)),
        label("", ("✓ ", GREEN), ("Add the refresh-token store", TEXT)),
        label("", ("✓ ", GREEN), ("Rotate on every refresh", TEXT)),
        label("", ("✓ ", GREEN), ("Detect a reused token", TEXT)),
        label("", ("◐ ", AMBER), ("Fix the last-second expiry", TEXT)),
        label("", ("○ ", MUTED), ("Document the flow", TEXT)),
        [],
        label("time", ("started 14:02 · 2m 14s", TEXT)),
        label("tools", *bar([(9, GREEN), (6, BLUE), (5, AMBER), (2, CYAN)], 22)),
        label("", ("■ ", GREEN), ("run 9  ", MUTED), ("■ ", BLUE), ("read 6  ", MUTED), ("■ ", AMBER),
              ("edit 5  ", MUTED), ("■ ", CYAN), ("search 2", MUTED)),
        [],
        label("request", ("│ ", ACCENT["AGENTS"]), ("add refresh tokens to the auth", TEXT)),
        label("", ("│ ", ACCENT["AGENTS"]), ("middleware, with rotation and reuse", TEXT)),
        label("", ("│ ", ACCENT["AGENTS"]), ("detection", TEXT)),
    ])
    hints([("j/k", "select"), ("enter", "open"), ("[ ]", "sections"), ("q", "close")])


def scene_test():
    section_bar(3)
    row = run_box([("◐", AMBER, "npm test", "now"), ("✗", RED, "npm test", "1m ago"),
                   ("✓", GREEN, "npm test -- auth", "9m ago"), ("✓", GREEN, "npm test", "24m ago"),
                   ("✓", GREEN, "npm test", "1h ago")], ACCENT["TEST"], "runs")
    body(row, [
        [("$ ", MUTED), ("npm test", BRIGHT, True)],
        [("  ~/app · 12s · running", MUTED)],
        RULE,
        [(" RUN ", BLUE, True), (" v3.2.4 ", TEXT), ("~/app", MUTED)],
        [],
        [(" ✓ ", GREEN), ("src/db/pool.test.ts ", TEXT), ("(4 tests) 62ms", MUTED)],
        [(" ✓ ", GREEN), ("src/routes/health.test.ts ", TEXT), ("(2 tests) 9ms", MUTED)],
        [(" ✓ ", GREEN), ("src/routes/users.test.ts ", TEXT), ("(9 tests) 120ms", MUTED)],
        [(" ✓ ", GREEN), ("src/utils.test.ts ", TEXT), ("(12 tests) 18ms", MUTED)],
        [(" ✓ ", GREEN), ("src/session.test.ts ", TEXT), ("(5 tests) 41ms", MUTED)],
        [(" ✓ ", GREEN), ("src/auth/refresh.test.ts ", TEXT), ("(6 tests) 33ms", MUTED)],
        [(" > ", AMBER), ("src/middleware/auth.test.ts ", TEXT), ("(7 tests) 4/7", MUTED)],
        [("   ✓ ", GREEN), ("issues an access and refresh token pair", TEXT)],
        [("   ✓ ", GREEN), ("refreshes an access token before expiry", TEXT)],
        [("   ✓ ", GREEN), ("rotates the refresh token on every use", TEXT)],
        [("   ✓ ", GREEN), ("accepts a token in its last second", TEXT)],
        [],
        [("◐ running 12s", AMBER)],
    ])
    hints([("j/k", "select"), ("enter", "open"), ("[ ]", "sections"), ("q", "close")])


def scene_git():
    section_bar(4)
    row = run_box([("✓", GREEN, "git add", "feat/refresh  1m ago  a41c9e2"),
                   ("", MUTED, 'git commit "Refresh-token rotation"', ""),
                   ("✓", GREEN, "git push", "feat/refresh  9m ago  7d03b18"),
                   ("✓", GREEN, "git switch -c feat/refresh", "feat/refresh  14m ago"),
                   ("✓", GREEN, "git pull", "main  32m ago  51e2a90")], ACCENT["GIT"], "runs")
    bar_ = ("  │ ", ACCENT["GIT"])
    body(row, [
        [("$ git add", BRIGHT, True)],
        [("$ git commit", BRIGHT, True)],
        [bar_, ("Refresh-token rotation", TEXT)],
        [bar_],
        [bar_, ("Each refresh issues a new token and retires the old", TEXT)],
        [bar_, ("one; a retired token coming back revokes the family.", TEXT)],
        [("  ~/app · on feat/refresh · 1s · exit 0", MUTED)],
        [("  commit ", MUTED), ("a41c9e2b7f0d5c18e9a3", ACCENT["GIT"])],
        RULE,
        [("[feat/refresh a41c9e2] Refresh-token rotation", TEXT)],
        [(" 3 files changed, ", TEXT), ("82 insertions(+)", GREEN), (", ", TEXT), ("5 deletions(-)", RED)],
        [(" create mode 100644 src/auth/refresh.ts", MUTED)],
    ])
    hints([("j/k", "select"), ("enter", "open"), ("[ ]", "sections"), ("q", "close")])


def scene_servers():
    section_bar(1)
    row = run_box([("●", GREEN, "next dev --turbopack", ":3000"),
                   ("●", GREEN, "anvil --block-time 2", ":8545")], ACCENT["SERVERS"], "servers")
    body(row, [
        [("$ ", MUTED), ("next dev --turbopack", BRIGHT, True)],
        [("  ", MUTED), ("http://localhost:3000", CYAN), (" · pid 41233 · log file", MUTED)],
        RULE,
        [("   ▲ Next.js 15.4.1 (Turbopack)", TEXT)],
        [("   - Local:        ", MUTED), ("http://localhost:3000", CYAN)],
        [("   - Environments: .env.local", MUTED)],
        [],
        [(" ✓ ", GREEN), ("Starting...", TEXT)],
        [(" ✓ ", GREEN), ("Ready in 812ms", TEXT)],
        [(" ○ ", MUTED), ("Compiling /api/auth/refresh ...", TEXT)],
        [(" ✓ ", GREEN), ("Compiled /api/auth/refresh in 214ms", TEXT)],
        [(" POST ", TEXT), ("/api/auth/refresh ", TEXT), ("200", GREEN), (" in 38ms", MUTED)],
        [(" POST ", TEXT), ("/api/auth/refresh ", TEXT), ("401", RED), (" in 12ms", MUTED)],
        [(" GET  ", TEXT), ("/api/health ", TEXT), ("200", GREEN), (" in 4ms", MUTED)],
    ])
    hints([("j/k", "select"), ("enter", "log"), ("o", "browser"), ("[ ]", "tabs"), ("q", "close")])


def scene_build():
    section_bar(2)
    row = run_box([("◐", AMBER, "npm run build", "now"), ("✓", GREEN, "npm run build", "6m ago"),
                   ("✗", RED, "npm run build", "21m ago")], ACCENT["BUILD"], "runs")
    body(row, [
        [("$ ", MUTED), ("npm run build", BRIGHT, True)],
        [("  ~/app · 9s · running", MUTED)],
        RULE,
        [("> app@0.1.0 build", MUTED)],
        [("> next build", MUTED)],
        [],
        [("   ▲ Next.js 15.4.1", TEXT)],
        [],
        [("   Creating an optimized production build ...", TEXT)],
        [(" ✓ ", GREEN), ("Compiled successfully in 6.2s", TEXT)],
        [(" ✓ ", GREEN), ("Linting and checking validity of types", TEXT)],
        [("   Collecting page data ", TEXT), ("...", MUTED)],
        [],
        [("◐ running 9s", AMBER)],
    ])
    hints([("j/k", "select"), ("enter", "open"), ("[ ]", "sections"), ("q", "close")])


def scene_deploy():
    section_bar(5)
    row = run_box([("✓", GREEN, "vercel --prod", "2m ago"), ("✓", GREEN, "vercel", "18m ago")],
                  ACCENT["DEPLOY"], "runs")
    body(row, [
        [("$ ", MUTED), ("vercel --prod", BRIGHT, True)],
        [("  ~/app · 41s · exit 0", MUTED)],
        RULE,
        [("Vercel CLI 44.2.0", MUTED)],
        [("Inspect: ", TEXT), ("https://vercel.com/acme/app/9Ft2", CYAN)],
        [("Production: ", TEXT), ("https://app.vercel.app", CYAN), (" [41s]", MUTED)],
        [],
        [("Building", TEXT), ("  ✓ Compiled successfully", GREEN)],
        [("Deploying outputs ...", TEXT)],
        [("Aliased ", TEXT), ("https://app.example.com", CYAN)],
    ])
    hints([("j/k", "select"), ("enter", "open"), ("[ ]", "sections"), ("q", "close")])


# In the order of the tabs.
scenes = [scene_agents, scene_servers, scene_build, scene_test, scene_git, scene_deploy]
for i, draw in enumerate(scenes):
    layer("s", i, draw)
layers("s", len(scenes), len(scenes) * SCENE_SECS)
css.append("@media (prefers-reduced-motion:reduce){.s,.p,.t{animation:none}}")

print(f'<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" viewBox="0 0 {W:.0f} {H:.0f}" '
      f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, \'Liberation Mono\', monospace" '
      f'font-size="{FS}">')
print(f"<style>{''.join(css)}</style>")
print("\n".join(out))
print("</svg>")
