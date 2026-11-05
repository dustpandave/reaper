"""MCP server exposing REAPER to AI agents (Claude Desktop, Claude Code, ...)."""

from __future__ import annotations

import functools
import random
from typing import Annotated, Literal

import anyio
from pydantic import BaseModel, Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import theory
from .bridge import BridgeError, ReaperBridge

INSTRUCTIONS = """\
Controls the REAPER DAW that's open on the user's computer.

Conventions:
- Tracks are addressed by 0-based index or exact name; -1 is the master track.
- Positions in beats are quarter notes from project start (bar N in 4/4 starts at beat (N-1)*4).
- MIDI notes: {pitch 0-127, start (beats from clip start), length (beats), velocity 1-127, channel 0-15}.
  Drums use channel 9 with General MIDI note numbers.
- Every change is a single undo step in REAPER, so it's safe to try ideas; the user can Cmd+Z.

For writing complementary parts: read_midi on existing parts first (it returns key and chord
estimates), then either compose notes yourself with insert_midi or start from generate_part and edit.
Put new ideas on new tracks named clearly (e.g. "Idea - Counter melody") rather than overwriting.
"""

mcp = MCPServer("reaper-agent", instructions=INSTRUCTIONS)
bridge = ReaperBridge()

def tool(fn):
    """Register an MCP tool whose bridge/validation errors are shown to the model verbatim."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (BridgeError, ValueError, KeyError) as e:
            raise ToolError(str(e)) from e
    return mcp.tool()(wrapper)


TrackRef = Annotated[int | str, Field(description="Track index (0-based), exact track name, or -1 for master")]


async def call(cmd: str, timeout: float | None = None, **params):
    return await anyio.to_thread.run_sync(lambda: bridge.call(cmd, timeout=timeout, **params))


class Note(BaseModel):
    pitch: int = Field(ge=0, le=127)
    start: float = Field(ge=0, description="Beats from clip start")
    length: float = Field(gt=0, description="Beats")
    velocity: int = Field(default=96, ge=1, le=127)
    channel: int = Field(default=0, ge=0, le=15)


class ChordSpan(BaseModel):
    chord: str = Field(description="Chord symbol, e.g. 'Am7', 'F', 'C/E', 'Gsus4'")
    beats: float = 4


class Section(BaseModel):
    name: str
    bars: int = Field(gt=0)


# ---------------------------------------------------------------------------
# Project & tracks
# ---------------------------------------------------------------------------

@tool
async def reaper_status() -> dict:
    """Check that REAPER and the AgentBridge script are running."""
    return await call("ping")


@tool
async def get_project() -> dict:
    """Project overview: tempo, time signature, cursor, time selection, and every track with its FX."""
    return await call("get_project")


@tool
async def list_items(track: TrackRef) -> list:
    """List the media items on a track (position/length in seconds and beats, MIDI or audio source)."""
    return await call("list_items", track=track)


@tool
async def add_track(name: str, instrument: str | None = None, index: int | None = None) -> dict:
    """Create a track. `instrument` adds a plugin by name, e.g. 'ReaSynth', 'VST3i: Serum', 'AU: Alchemy'."""
    return await call("add_track", name=name, instrument=instrument, index=index)


@tool
async def set_track(track: TrackRef, name: str | None = None, volume_db: float | None = None,
                    pan: Annotated[float | None, Field(ge=-1, le=1)] = None, mute: bool | None = None,
                    solo: bool | None = None, rec_arm: bool | None = None, selected: bool | None = None,
                    color_rgb: list[int] | None = None) -> dict:
    """Change track name, volume (dB), pan (-1..1), mute, solo, record-arm, selection, or color."""
    return await call("set_track", track=track, name=name, volume_db=volume_db, pan=pan, mute=mute,
                      solo=solo, rec_arm=rec_arm, selected=selected, color=color_rgb)


@tool
async def delete_track(track: TrackRef) -> bool:
    """Delete a track."""
    return await call("delete_track", track=track)


@tool
async def add_send(source: TrackRef, dest: TrackRef, volume_db: float | None = None) -> dict:
    """Route a send from one track to another (e.g. to a reverb bus)."""
    return await call("add_send", source=source, dest=dest, volume_db=volume_db)


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------

@tool
async def list_installed_fx(filter: str | None = None, limit: int = 200) -> list:
    """Search the plugins installed in REAPER (e.g. filter='reverb' or 'VST3i')."""
    return await call("list_installed_fx", filter=filter, limit=limit)


@tool
async def add_fx(track: TrackRef, name: str) -> dict:
    """Add a plugin to a track's FX chain, e.g. 'ReaEQ', 'ReaComp', 'VST3: Pro-Q 3 (FabFilter)'."""
    return await call("add_fx", track=track, name=name)


@tool
async def remove_fx(track: TrackRef, fx: int) -> bool:
    """Remove the plugin at index `fx` from a track."""
    return await call("remove_fx", track=track, fx=fx)


@tool
async def get_fx_params(track: TrackRef, fx: int, filter: str | None = None) -> list:
    """List a plugin's parameters (normalized 0..1 value plus REAPER's display string)."""
    return await call("get_fx_params", track=track, fx=fx, filter=filter)


@tool
async def set_fx_param(track: TrackRef, fx: int, param: int | str, value: Annotated[float, Field(ge=0, le=1)],
                       enabled: bool | None = None) -> dict:
    """Set a plugin parameter by index or exact name to a normalized 0..1 value. Check the returned display value."""
    return await call("set_fx_param", track=track, fx=fx, param=param, value=value, enabled=enabled)


# ---------------------------------------------------------------------------
# MIDI & ideas
# ---------------------------------------------------------------------------

@tool
async def read_midi(track: TrackRef, item: int = 0, include_notes: bool = True) -> dict:
    """Read a MIDI item's notes plus an analysis: key candidates, chord per bar, range, density."""
    info = await call("read_midi", track=track, item=item)
    proj = await call("get_project")
    info["analysis"] = theory.summarize_notes(info["notes"], proj.get("beats_per_measure", 4))
    if not include_notes:
        info.pop("notes")
    return info


@tool
async def insert_midi(track: TrackRef, notes: list[Note], start_beat: float | None = None,
                      length_beats: float | None = None, name: str | None = None) -> dict:
    """Create a MIDI item on a track at `start_beat` (defaults to edit cursor) containing `notes`."""
    return await call("insert_midi", track=track, notes=[n.model_dump() for n in notes],
                      start_beat=start_beat, length_beats=length_beats, name=name)


@tool
async def replace_midi(track: TrackRef, notes: list[Note], item: int = 0) -> dict:
    """Replace all notes in an existing MIDI item (note starts are relative to the item start)."""
    return await call("replace_midi", track=track, item=item, notes=[n.model_dump() for n in notes])


@tool
async def delete_item(track: TrackRef, item: int) -> bool:
    """Delete a media item from a track."""
    return await call("delete_item", track=track, item=item)


@tool
async def chords_from_roman(progression: str, key: str, beats_per_chord: float = 4) -> list:
    """Turn roman numerals into chord symbols, e.g. 'vi IV I V' in 'G major'. Use b/# for borrowed chords."""
    return theory.roman_to_chords(progression, key, beats_per_chord)


@tool
async def suggest_progressions(key: str, mood: str | None = None) -> dict:
    """Starter chord progressions for a key, grouped by mood (pop, melancholic, uplifting, jazzy, dark, soulful, epic)."""
    moods = [mood] if mood else list(theory.PROGRESSIONS)
    out = {}
    for m in moods:
        if m not in theory.PROGRESSIONS:
            raise ValueError(f"mood must be one of {list(theory.PROGRESSIONS)}")
        out[m] = [{"roman": p, "chords": [c["chord"] for c in theory.roman_to_chords(p, key)]}
                  for p in theory.PROGRESSIONS[m]]
    return out


@tool
async def generate_part(
    kind: Literal["bass", "arpeggio", "pad", "counter_melody", "drums"],
    chords: list[ChordSpan] | None = None,
    key: str | None = None,
    style: str | None = None,
    bars: int = 4,
    octave: int | None = None,
    density: Annotated[float, Field(ge=0, le=1)] = 0.5,
    seed: int | None = None,
    against_track: TrackRef | None = None,
    against_item: int = 0,
    insert_on_track: TrackRef | None = None,
    start_beat: float | None = None,
) -> dict:
    """Generate a starting-point part that fits a chord progression, optionally inserting it into REAPER.

    kind/style:
      bass: root | pulse | root_fifth | octave | syncopated | walking
      arpeggio: up | down | updown | random
      pad: close | open
      counter_melody: needs `key`; pass `against_track` to move against an existing melody
      drums: rock | four_on_floor | half_time | breakbeat | trap | funk | bossa | shuffle

    If `chords` is omitted and `against_track` is given, chords are detected from that MIDI item.
    These are scaffolds - refine the notes to taste before calling it done. Change `seed` for variations.
    """
    rng = random.Random(seed)
    melody = None
    bpb = 4
    if against_track is not None:
        src = await call("read_midi", track=against_track, item=against_item)
        melody = src["notes"]
        proj = await call("get_project")
        bpb = proj.get("beats_per_measure", 4)
        if not chords:
            detected = theory.chords_by_bar(melody, bpb)
            chords = [ChordSpan(chord=c["chord"], beats=c["beats"]) for c in detected if c["chord"]]
        if not key:
            cands = theory.estimate_key(melody)
            key = cands[0]["key"] if cands else None
    chord_dicts = [c.model_dump() for c in chords] if chords else []

    if kind == "drums":
        notes = theory.drums(style or "rock", bars=bars, beats_per_bar=bpb, humanize=density * 0.3, rng=rng)
    else:
        if not chord_dicts:
            raise ValueError("give `chords` or `against_track` so the part has harmony to follow")
        if kind == "bass":
            notes = theory.bassline(chord_dicts, style or "root", octave=octave or 2, rng=rng)
        elif kind == "arpeggio":
            notes = theory.arpeggio(chord_dicts, style or "up", rate=0.25 if density > 0.6 else 0.5,
                                    octave=octave or 4, rng=rng)
        elif kind == "pad":
            notes = theory.pad(chord_dicts, octave=octave or 4, voicing=style or "close", rng=rng)
        else:
            if not key:
                raise ValueError("counter_melody needs `key` (or `against_track` to detect it)")
            notes = theory.counter_melody(chord_dicts, key, density=density, octave=octave or 5,
                                          melody=melody, rng=rng)

    result = {"kind": kind, "key": key, "chords": chord_dicts, "note_count": len(notes)}
    if insert_on_track is not None:
        label = f"{kind.replace('_', ' ').title()} ({style or 'default'})"
        result["item"] = await call("insert_midi", track=insert_on_track, notes=notes,
                                    start_beat=start_beat, name=label)
    else:
        result["notes"] = notes
    return result


@tool
async def import_audio(track: TrackRef, path: str, start_beat: float | None = None) -> dict:
    """Import an audio/MIDI file onto a track - e.g. a clip from Suno or another generator - at `start_beat`."""
    return await call("import_audio", track=track, path=path, start_beat=start_beat)


# ---------------------------------------------------------------------------
# Tempo, markers, arrangement, transport
# ---------------------------------------------------------------------------

@tool
async def set_tempo(bpm: float | None = None, time_signature: list[int] | None = None) -> dict:
    """Set project tempo and/or time signature, e.g. bpm=92, time_signature=[6, 8]."""
    return await call("set_tempo", bpm=bpm, time_signature=time_signature)


@tool
async def list_markers() -> list:
    """List markers and regions."""
    return await call("list_markers")


@tool
async def add_marker(name: str, start_beat: float, end_beat: float | None = None,
                     color_rgb: list[int] | None = None) -> dict:
    """Add a marker, or a region if `end_beat` is given."""
    return await call("add_marker", name=name, start_beat=start_beat, end_beat=end_beat, color=color_rgb)


@tool
async def create_arrangement(sections: list[Section] | None = None,
                             template: Literal["pop", "edm", "hiphop", "rock", "ambient", "lofi"] | None = None,
                             start_bar: int = 1, replace_existing_regions: bool = False) -> dict:
    """Lay out song sections as colored regions. Give explicit `sections` or pick a `template`."""
    if sections:
        secs = [(s.name, s.bars) for s in sections]
    elif template:
        secs = theory.ARRANGEMENTS[template]
    else:
        return {"templates": {k: [f"{n} ({b})" for n, b in v] for k, v in theory.ARRANGEMENTS.items()}}
    proj = await call("get_project")
    bpb = proj.get("beats_per_measure", 4)
    if replace_existing_regions:
        await call("clear_markers", regions=True)
    beat = (start_bar - 1) * bpb
    made = []
    for name, nbars in secs:
        end = beat + nbars * bpb
        await call("add_marker", name=name, start_beat=beat, end_beat=end, color=theory.section_color(name))
        made.append({"name": name, "bars": nbars, "start_bar": int(beat // bpb) + 1})
        beat = end
    return {"sections": made, "total_bars": int(beat // bpb) - start_bar + 1}


@tool
async def transport(action: Literal["play", "stop", "pause", "record", "toggle_repeat", "status"] = "status",
                    cursor_beat: float | None = None) -> dict:
    """Play/stop/pause/record, toggle loop, and/or move the edit cursor to a beat."""
    return await call("transport", action=action, cursor_beat=cursor_beat)


@tool
async def set_time_selection(start_beat: float, end_beat: float) -> dict:
    """Set the time selection (also the loop range)."""
    return await call("set_time_selection", start_beat=start_beat, end_beat=end_beat)


# ---------------------------------------------------------------------------
# Actions & scripting
# ---------------------------------------------------------------------------

@tool
async def run_action(id: int | str) -> dict:
    """Run a REAPER action by command ID (e.g. 40001) or named ID (e.g. '_SWS_SAVESEL', '_RS...')."""
    return await call("run_action", id=id)


@tool
async def run_lua(code: str) -> dict:
    """Run a Lua ReaScript inside REAPER for anything the other tools don't cover.

    The `reaper` API is available; `print(...)` output and the chunk's `return` value come back.
    Runs as one undo step. Example: `return reaper.CountSelectedMediaItems(0)`
    """
    return await call("run_lua", code=code, timeout=60)


# ---------------------------------------------------------------------------
# Render & analyze
# ---------------------------------------------------------------------------

@tool
async def render(bounds: Literal["project", "time_selection", "regions"] = "project",
                 source: Literal["master", "stems"] = "master", stem_tracks: list[int | str] | None = None,
                 directory: str | None = None, analyze: bool = True) -> dict:
    """Render the mix or stems with the project's current format settings, then optionally analyze each file.

    Renders into <project folder>/AgentRenders unless `directory` is given. For stems, pass `stem_tracks`.
    """
    res = await call("render", bounds=bounds, source=source, stem_tracks=stem_tracks,
                     directory=directory, timeout=900)
    if analyze:
        from .analysis import analyze_audio
        for f in res.get("files", []):
            if f.get("exists"):
                try:
                    f["analysis"] = await anyio.to_thread.run_sync(analyze_audio, f["path"])
                except Exception as e:  # unsupported format etc.
                    f["analysis_error"] = str(e)
    return res


@tool
async def analyze_audio_file(path: str) -> dict:
    """Loudness (LUFS, true peak), dynamics, stereo width, and spectral balance of an audio file."""
    from .analysis import analyze_audio
    return await anyio.to_thread.run_sync(analyze_audio, path)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def complementary_part(track: str, part: str = "counter melody") -> str:
    """Write a part that complements an existing track."""
    return (f"Look at my REAPER project with get_project, then read_midi on '{track}'. Tell me the key, chords "
            f"and rhythmic feel you hear in a couple of lines. Then write a {part} that complements it: leave "
            "space where the existing part is busy, use contrary or oblique motion, and stay out of its register. "
            "Offer three short variations on three new tracks named 'Idea A/B/C - ...', and tell me what makes "
            "each one different so I can audition them.")


@mcp.prompt()
def arrange_song(genre: str = "pop") -> str:
    """Turn the loop in this project into a full arrangement."""
    return (f"Treat the material in my REAPER project as the core idea of a {genre} song. Propose an arrangement "
            "(sections, lengths, what enters or drops out where, energy curve), then lay it out with "
            "create_arrangement. Suggest one or two new parts that would lift the chorus or drop, and "
            "write them once I agree.")


@mcp.prompt()
def mix_check(target_lufs: str = "-14") -> str:
    """Render and review the current mix."""
    return (f"Render my mix with render(analyze=true). Compare it against a {target_lufs} LUFS streaming target "
            "and -1 dBTP ceiling, comment on spectral balance and stereo image, and suggest the three changes "
            "with the biggest payoff. Ask before changing any FX.")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
