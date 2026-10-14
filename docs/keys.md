# Keys and mouse

**English** · [한국어](keys.ko.md) · [← README](../README.md)

## Open, close, move

The deck opens on its own beside any agent herdr recognises (Claude Code, Codex, OpenCode, ...) as it
starts. For the rest, add a key to herdr's `config.toml`:

```toml
[[keys.command]]
key = "prefix+d"
type = "plugin_action"
command = "steading.toggle"
```

- `prefix+d` opens the deck beside the focused pane, or closes it; on the deck itself it closes that deck.
- The deck opens to the right of the agent, or on top of it when the agent pane is too narrow for a
  deck beside it (under 140 columns). `L` in the deck moves it to the other side, and that agent's
  deck keeps opening there.
- A deck you closed stays closed: runs are still recorded, and show when you open it again.
- To stop decks opening when an agent starts: `touch ~/.config/herdr/plugins/config/steading/no-auto-open`.

## Keys

Focus the deck (click it, or use herdr's pane navigation). The bottom row always shows the keys that
apply right now.

| Where | Keys | Action |
|---|---|---|
| anywhere | `[` `]` (or `tab` `shift+tab`), `1`–`7` | previous / next tab, jump to one |
| | `L` | move the deck to the other side of the agent |
| | `q` | close the deck (in a log: back to the list) |
| run list | `j` `k` (or `↓` `↑`), `gg` `G` | pick a run (newest on top) |
| | `enter` `l` | move into its output |
| output | `h` `j` `k` `l`, `w` `b` `e`, `W` `B` `E`, `0` `^` `$`, `{` `}` | move the cursor (herdr copy-mode keys) |
| | `gg` `G`, `ctrl+d` `ctrl+u` | top / bottom (`G` follows the live run again), half page |
| | `v` `V`, `y` | select by character / line, yank (the cursor line when nothing is selected) |
| | `/` `?`, `n` `N` | search forward / back, repeat |
| | `esc` | cancel the selection, or back to the run list |
| servers | `o` | open the server in the browser |
| agents | `enter`, then `j` `k`, `enter`, `esc` | move between an agent's controls (its jobs, the fold of a long request) and press one |

Arrow keys work as `h` `j` `k` `l` everywhere. While the cursor is in the list, the run's command and
log are drawn faint; moving into the log turns that around.

## Mouse

- Click a tab, a run in the list, a log line or a server.
- The wheel scrolls the log; over the run list it moves through the runs.
- Drag over a log to select it: it is copied as in herdr, with a note above the selection.

Yanks and selections go to `pbcopy`, `wl-copy` or `xclip`, falling back to the terminal clipboard (OSC 52).
