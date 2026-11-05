# reaper-agent

Lets Claude (or any MCP-capable agent) work inside your REAPER session: build tracks and FX chains, write complementary parts and arrangements as MIDI, run ReaScripts and actions, and render and analyze your mix.

```
Claude Desktop ──MCP/stdio──> reaper-agent (Python) ──JSON files──> AgentBridge.lua (inside REAPER)
```

The Lua script runs inside REAPER and talks to the Python server through a small folder of JSON files, so there's nothing to compile and no extensions to install. Every change the agent makes is one undo step, so you can Cmd+Z anything you don't like.

## What it can do

| Area | Tools |
| --- | --- |
| Project | `get_project`, `list_items`, `add_track`, `set_track`, `delete_track`, `add_send`, `set_tempo` |
| FX | `list_installed_fx`, `add_fx`, `remove_fx`, `get_fx_params`, `set_fx_param` |
| Ideas & MIDI | `read_midi` (with key and chord detection), `insert_midi`, `replace_midi`, `generate_part`, `suggest_progressions`, `chords_from_roman`, `import_audio` |
| Arrangement | `create_arrangement` (colored regions from a template or your own sections), `add_marker`, `list_markers` |
| Transport | `transport`, `set_time_selection` |
| Scripting | `run_action` (any action or SWS/ReaPack command ID), `run_lua` (arbitrary ReaScript) |
| Render & analyze | `render` (mix or stems; reports LUFS, true peak, crest factor, stereo width, spectral balance), `analyze_audio_file` |

It also ships three prompts you can pick from Claude Desktop's prompt menu: **complementary_part**, **arrange_song** and **mix_check**.

## Setup (macOS + Claude Desktop)

**1. Install uv** (runs the Python server without you managing a virtualenv):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2. Load the bridge in REAPER.** Copy `reaper/AgentBridge.lua` into REAPER's Scripts folder (in REAPER: *Options > Show REAPER resource path in Finder*, then open `Scripts`). Then *Actions > Show action list > New action > Load ReaScript...*, pick the file, and run it. It keeps running in the background.

To start it automatically with REAPER, add this line to `Scripts/__startup.lua` (create the file if it doesn't exist):

```lua
dofile(reaper.GetResourcePath() .. "/Scripts/AgentBridge.lua")
```

**3. Add the server to Claude Desktop.** Open *Claude > Settings > Developer > Edit Config* and add:

```json
{
  "mcpServers": {
    "reaper": {
      "command": "/Users/YOU/.local/bin/uvx",
      "args": ["--from", "git+https://github.com/OWNER/reaper-agent", "reaper-agent"]
    }
  }
}
```

Use the full path to `uvx` (`which uvx` prints it) since Claude Desktop doesn't inherit your shell's PATH. For a local checkout, use `"args": ["--directory", "/path/to/reaper-agent", "run", "reaper-agent"]` with `uv` instead of `uvx`.

**4. Restart Claude Desktop** and ask: *"Check REAPER is connected and tell me what's in my project."*

### Claude Code

```bash
claude mcp add reaper -- uvx --from git+https://github.com/OWNER/reaper-agent reaper-agent
```

## Working with ideas

Things that work well to ask:

- *"Read the Keys track and write three bass lines that lock with the kick on the Drums track. Put each on its own track."*
- *"This is an 8-bar loop. Lay out a pop arrangement around it and tell me what should drop out in verse 2."*
- *"Give me a counter melody against the Lead track that stays below it and moves when it holds."*
- *"Suggest four melancholic progressions in D minor, then put the one with the borrowed chord on a pad track."*

`generate_part` gives Claude a musically sound scaffold (bass, arpeggio, pad, counter melody or drums that follow the chords); Claude then edits the notes to fit your track. Change the `seed` for variations.

### Suno and other audio generators

Suno doesn't have an official public API. Community-made Suno MCP servers do exist, but they rely on unofficial access that can break or conflict with Suno's terms, so this project doesn't bundle one. The workflow that works today:

1. Generate in Suno (or any tool, including another MCP server you've added to Claude Desktop) and download the stems or the track.
2. Ask Claude to *"import ~/Downloads/suno-idea.wav onto a new track at bar 17"*. It uses `import_audio`, and from there `render` and `analyze_audio_file` can compare it to your mix.

If you install a separate music-generation MCP server alongside this one, Claude can chain them: generate, download, then import into REAPER.

## Safety

`run_lua` and `run_action` can do anything a ReaScript can, including deleting files. Claude Desktop asks before each tool call unless you've allowed it, so keep that prompt on for these two if you're working on a session you care about. Renders go to `<project folder>/AgentRenders` and your render settings are restored afterwards.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `REAPER_AGENT_BRIDGE_DIR` | `~/Library/Application Support/REAPER/AgentBridge` | Where the JSON files are exchanged. Must match REAPER's resource path. Set it if you use a portable REAPER install. |

## Troubleshooting

- **"REAPER bridge isn't running"**: run the AgentBridge action in REAPER. The script writes a heartbeat file every second; if REAPER asks whether to terminate the script when you run it again, choose *Terminate*, then run it once more.
- **Calls time out**: a modal dialog (render dialog, plugin scan, save prompt) blocks REAPER's script loop. Close it and retry.
- **Plugin not found**: use `list_installed_fx` with a filter and pass the exact name it returns.

## Development

```bash
uv run --extra dev pytest
```

The tests run the real `AgentBridge.lua` (via `lupa`) against an in-memory fake of the REAPER API (`tests/fake_reaper.lua`), so the whole Python ↔ Lua path is covered without REAPER installed. Behavior inside real REAPER still needs a manual check.

## License

MIT
