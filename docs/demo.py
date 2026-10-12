#!/usr/bin/env python3
"""Draw the README demo, a Claude Code session beside the deck, as docs/demo.svg.

    python3 docs/demo.py > docs/demo.svg

Every run of text sits on a character grid at its own x, so the columns line up
whatever monospace font the viewer has. Colours are the deck's own (bin/viewer.py).
"""
from xml.sax.saxutils import escape

CW, LH, FS = 8.4, 19, 14          # cell width, line height, font size (px)
PAD, TOP = 14, 38                 # inner padding, title bar height
A, D = 84, 56                     # agent and deck columns: 60:40, all five sections fit

BG, STRIP, TAB, ACTIVE = "#1b1e23", "#21252b", "#2c313a", "#3e4451"
MUTED, BRIGHT, TEXT = "#787f8c", "#dcdfe4", "#abb2bf"
GREEN, RED, AMBER, CYAN, BLUE = "#98b17d", "#d0868d", "#d8b46a", "#7fb4b4", "#7aa2c8"
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
    tool(GREEN, "Read", "src/middleware/auth.ts"),
    result(("Read 142 lines", MUTED)),
    [],
    tool(GREEN, "Write", "src/auth/refresh.ts"),
    result(("Wrote 64 lines to src/auth/refresh.ts", MUTED)),
    [],
    tool(GREEN, "Update", "src/middleware/auth.ts"),
    changed(18, 4),
    [],
    tool(GREEN, "Bash", "npm run build"),
    result(("built in 3.1s", MUTED)),
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
    tool(MUTED, "Bash", "npm test"),
    result(("Running...", MUTED)),
    [],
    [("✻ ", CLAUDE), ("Testing...", CLAUDE), (" (12s · esc to interrupt)", MUTED)],
    [],
    RULE,
    [("> ", BRIGHT)],
    RULE,
    [("  ▸▸ ", CLAUDE), ("accept edits on", CLAUDE), (" (shift+tab to cycle)", MUTED)],
]
ROWS = len(agent)

# Deck pane: section bar, run list, the selected run's command and output, key hints.
runs = [("◐", AMBER, "npm test", "now"), ("✗", RED, "npm test", "1m ago"),
        ("✓", GREEN, "npm test -- auth", "9m ago"), ("✓", GREEN, "npm test", "24m ago"),
        ("✓", GREEN, "npm test", "1h ago")]
output = [
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
]
sections = [("SERVERS", "●", GREEN), ("BUILD", "✓", GREEN), ("TEST", "◐", AMBER), ("GIT", "✓", GREEN),
            ("DEPLOY", "·", MUTED)]
ACTIVE_SECTION = "TEST"
hints = [("j/k", "select"), ("enter", "open"), ("[ ]", "sections"), ("q", "close")]

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

# Window, title bar, pane divider.
out.append(f'<rect width="{W:.0f}" height="{H:.0f}" rx="10" fill="{BG}"/>')
out.append(f'<path d="M10 0H{W - 10:.0f}a10 10 0 0 1 10 10V{TOP}H0V10a10 10 0 0 1 10-10z" fill="{STRIP}"/>')
for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
    out.append(f'<circle cx="{20 + i * 20}" cy="{TOP / 2}" r="6" fill="{c}"/>')
out.append(f'<text x="{W / 2:.0f}" y="{TOP / 2 + 5}" fill="{MUTED}" text-anchor="middle">herdr</text>')
line(A + 0.5, 0, A + 0.5, ROWS, ACTIVE)

# Agent pane.
for row, runs_ in enumerate(agent):
    if runs_ == RULE:
        line(0, row + 0.5, A, row + 0.5, MUTED)
    else:
        text(1, row, runs_, A)

# Deck pane. The section bar, laid out as bin/viewer.py's strip() does at this width.
d0, end = A + 1, A + 1 + D
rect(d0, 0, D, STRIP)
col = d0 + 1
for name, icon, colour in sections:
    w, active = len(name) + 4, name == ACTIVE_SECTION
    rect(col, 0, w, BLUE if active else TAB)
    text(col, 0, [(f" {name} ", STRIP if active else MUTED, active), (icon, STRIP if active else colour, active)], end)
    col += w + 1

# Run list: a rounded box with its count in the top border, the newest run selected.
first, last = 2, 2 + len(runs) - 1
rect(d0 + 0.5, first - 0.5, D - 1, None, rows=len(runs) + 1, rx=5, stroke=BLUE)
out.append(f'<rect x="{x(d0 + 1.5):.1f}" y="{top(first - 1) + LH * 0.1:.1f}" width="{11 * CW:.1f}" '
           f'height="{LH * 0.8:.1f}" fill="{BG}"/>')
text(d0 + 2, first - 1, [(f" runs {len(runs)} ", BLUE)], end)
rect(d0 + 1, first, D - 2, ACTIVE)
for i, (icon, colour, cmd, age) in enumerate(runs):
    left = [(" > " if i == 0 else "   ", BLUE), (f"{icon} ", colour), (cmd, BRIGHT if i == 0 else TEXT)]
    width = sum(len(r[0]) for r in left)
    text(d0, first + i, left + [(age.rjust(D - 2 - width - 1), MUTED)], end)

# The selected run: command, where and how long, a rule, its output.
row = last + 2
text(d0, row, [("$ ", MUTED), ("npm test", BRIGHT, True)], end)
text(d0, row + 1, [("  ~/app  2s", MUTED)], end)
line(d0, row + 2.5, end, row + 2.5, ACTIVE)
for i, runs_ in enumerate(output):
    text(d0, row + 3 + i, runs_, end)

# Fold mark and key hints at the bottom.
text(d0, ROWS - 2, [(" »", MUTED)], end)
rect(d0, ROWS - 1, D, STRIP)
text(d0, ROWS - 1, [r for k, what in hints for r in ((f" {k}", BRIGHT), (f" {what} ", MUTED))], end)

print(f'<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" viewBox="0 0 {W:.0f} {H:.0f}" '
      f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, \'Liberation Mono\', monospace" '
      f'font-size="{FS}">')
print("\n".join(out))
print("</svg>")
