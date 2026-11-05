"""End-to-end: Python ReaperBridge <-> real AgentBridge.lua running against a fake REAPER API."""

import threading
import time
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

from reaper_agent.bridge import BridgeError, ReaperBridge  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reaper(tmp_path):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    fake = lua.execute((ROOT / "tests" / "fake_reaper.lua").read_text())
    fake._resource_path = str(tmp_path)
    fake.time_precise = time.monotonic
    lua.globals().reaper = fake
    lua.execute((ROOT / "reaper" / "AgentBridge.lua").read_text())

    stop = threading.Event()

    def pump():
        while not stop.is_set():
            fn = fake._state.deferred
            if fn:
                fn()
            time.sleep(0.005)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    bridge = ReaperBridge(tmp_path / "AgentBridge", timeout=5)
    for _ in range(200):
        if bridge.bridge_alive():
            break
        time.sleep(0.01)
    yield bridge, fake
    stop.set()
    t.join()


def test_ping_and_project(reaper):
    bridge, _ = reaper
    assert bridge.call("ping")["bridge_version"]
    proj = bridge.call("get_project")
    assert proj["bpm"] == 120 and proj["tracks"] == []


def test_tracks_fx_and_errors(reaper):
    bridge, fake = reaper
    tr = bridge.call("add_track", name="Keys", instrument="ReaSynth")
    assert tr["name"] == "Keys" and tr["fx"][0]["name"].startswith("VSTi: ReaSynth")
    bridge.call("set_track", track="keys", volume_db=-6, pan=0.25, mute=True)
    proj = bridge.call("get_project")
    assert round(proj["tracks"][0]["volume_db"], 2) == -6.0 and proj["tracks"][0]["mute"] is True
    p = bridge.call("set_fx_param", track=0, fx=0, param="Gain", value=0.8)
    assert p["display"] == "80%"
    assert [x["name"] for x in bridge.call("get_fx_params", track=0, fx=0)] == ["Gain", "Mix"]
    with pytest.raises(BridgeError, match="no track named"):
        bridge.call("set_track", track="Nope", mute=True)
    with pytest.raises(BridgeError, match="unknown command"):
        bridge.call("frobnicate")


def test_midi_roundtrip(reaper):
    bridge, _ = reaper
    bridge.call("add_track", name="Bass")
    notes = [{"pitch": 45, "start": 0, "length": 1, "velocity": 100, "channel": 0},
             {"pitch": 52, "start": 1.5, "length": 0.5, "velocity": 90, "channel": 0}]
    item = bridge.call("insert_midi", track="Bass", notes=notes, start_beat=8, name="Idea")
    assert item["position_qn"] == 8 and item["length_qn"] == 4 and item["take_name"] == "Idea"
    back = bridge.call("read_midi", track="Bass", item=0)
    assert [(n["pitch"], n["start"], n["length"]) for n in back["notes"]] == [(45, 0, 1), (52, 1.5, 0.5)]
    bridge.call("replace_midi", track="Bass", item=0, notes=notes[:1])
    assert len(bridge.call("read_midi", track="Bass")["notes"]) == 1


def test_markers_transport_actions_lua(reaper):
    bridge, fake = reaper
    bridge.call("add_marker", name="Verse", start_beat=0, end_beat=32)
    bridge.call("add_marker", name="Drop", start_beat=32)
    m = bridge.call("list_markers")
    assert [(x["name"], x["is_region"]) for x in m] == [("Verse", True), ("Drop", False)]
    assert bridge.call("clear_markers", regions=True)["removed"] == 1
    assert bridge.call("transport", action="play", cursor_beat=4)["play_state"] == 1
    bridge.call("run_action", id="_SWS_TEST")
    assert list(fake._state.commands.values())[-1] == 55555
    r = bridge.call("run_lua", code="print('hi', 2); return {n = reaper.CountTracks(0)}")
    assert r["output"] == ["hi\t2"] and r["result"] == {"n": 0}
    with pytest.raises(BridgeError, match="compile error"):
        bridge.call("run_lua", code="return (")


def test_render_restores_settings(reaper, tmp_path):
    bridge, fake = reaper
    fake._state.info_s.RENDER_FILE = "/orig"
    res = bridge.call("render", bounds="time_selection", directory=str(tmp_path / "out"))
    assert res["files"][0]["path"].endswith("mix.wav")
    assert fake._state.info_s.RENDER_FILE == "/orig"
    assert list(fake._state.commands.values())[-1] == 42230


def test_unicode_and_json_edge_cases(reaper):
    bridge, _ = reaper
    tr = bridge.call("add_track", name='Vox "lead" — ünïcødé 🎤\n')
    assert tr["name"] == 'Vox "lead" — ünïcødé 🎤\n'
    assert bridge.call("list_items", track=0) == []


def test_bridge_down_message(tmp_path):
    with pytest.raises(BridgeError, match="isn't running"):
        ReaperBridge(tmp_path).call("ping")


def test_mcp_tools_end_to_end(reaper):
    import anyio

    from reaper_agent import server

    bridge, fake = reaper
    server.bridge = bridge

    async def go():
        await server.mcp.call_tool("add_track", {"name": "Chords"})
        await server.mcp.call_tool("add_track", {"name": "Idea - Bass"})
        await server.mcp.call_tool("generate_part", {
            "kind": "pad", "chords": [{"chord": c} for c in ["Am", "F", "C", "G"]],
            "insert_on_track": "Chords", "start_beat": 0})
        read = await server.mcp.call_tool("read_midi", {"track": "Chords", "include_notes": False})
        bass = await server.mcp.call_tool("generate_part", {
            "kind": "bass", "style": "syncopated", "against_track": "Chords", "insert_on_track": "Idea - Bass",
            "seed": 3})
        arr = await server.mcp.call_tool("create_arrangement", {"template": "pop"})
        return read, bass, arr

    read, bass, arr = anyio.run(go)
    text = str(read)
    assert "Am" in text and ("A minor" in text or "C major" in text)
    assert "'chord': 'F'" in str(bass) or '"chord":"F"' in str(bass).replace(" ", "")
    assert len(bridge.call("list_markers")) == 10
