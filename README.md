# Claude Pet

A small pixel-art pet that lives on your desktop and shows what Claude Code is doing right now. It works on Windows and macOS.

## States

The pet reads its state from `state.txt`, next to `pet.pyw`, and plays a matching animation and sound:

| State | Meaning |
| --- | --- |
| `working` | Claude is working |
| `idle` | Claude is waiting for you |
| `question` | Claude is asking you something |
| `sleeping` | No active session |
| `tired HH:MM` | You hit your usage limit; it resets at `HH:MM` |

The pet also checks the latest Claude Code transcript. If it finds a usage limit message, it switches to `tired` on its own.

## Connecting it to Claude Code

Use [Claude Code hooks](https://docs.claude.com/en/docs/claude-code/hooks) to write the state. Example for `~/.claude/settings.json` (replace the path):

```json
{
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "echo working > /path/to/claude-pet/state.txt" }] }],
    "Notification":     [{ "hooks": [{ "type": "command", "command": "echo question > /path/to/claude-pet/state.txt" }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "echo idle > /path/to/claude-pet/state.txt" }] }],
    "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "echo sleeping > /path/to/claude-pet/state.txt" }] }]
  }
}
```

## Running it

Requirements: Python 3 with Tkinter (included with most installs). On Windows, `pip install pystray pillow` adds the tray icon.

```bash
python pet.pyw
```

- **Windows**: bottom right of the screen, with a tray icon. `kill_pet.bat` stops it.
- **macOS**: top center, just under the notch.

## Controls

- **Drag** the pet to move it. Its position is saved.
- **Right-click** (or the tray icon on Windows) opens the menu:
  - Move / reset position
  - Size: small, medium, large
  - Screen: primary or secondary
  - Sounds on / off
  - Wander around the desktop
  - Always on top, or behind windows on the desktop
  - Eco mode: pauses the animation when the pet is covered
  - Hide / quit

## Configuration

Settings are saved in `config.json`, next to the script:

| Key | Default | Values |
| --- | --- | --- |
| `size` | `moyen` | `petit`, `moyen`, `grand` |
| `monitor` | `primary` | `primary`, `secondary` |
| `sounds` | `true` | `true`, `false` |
| `wander` | `false` | `true`, `false` |
| `layer` | `top` | `top`, `desktop` |
| `eco` | `true` | `true`, `false` |
| `pos` | `null` | saved position, or `null` for the default spot |
