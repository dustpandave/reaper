"""Soundly search over a synthetic LocalFilesDB (real schema), and placing results in REAPER."""

import sqlite3

import anyio
import numpy as np
import pytest
import soundfile as sf

from reaper_agent.soundly import SoundlyDB

SCHEMA = """
CREATE TABLE Files(Id INTEGER PRIMARY KEY, TimeCreated text, Description text,
  CategoryID text, Tags text, Length float, Location text, SampleRate int,
  BitRate int, Channels int, Format text, LibraryID text, SearchString text,
  JoinFilesList text, FileID text, MetaData text);
CREATE TABLE Libraries(Id INTEGER PRIMARY KEY, TimeCreated text, LibraryID text,
  Name text, Categories text, Path text, DirList text, Monitor int);
"""

# (Description, CategoryID, Tags, Length, kind)
ROWS = [
    ("Big air whoosh pass by fast", "WHSHAir", "whoosh,swish,pass,transition", 1.2, "noise"),
    ("Cinematic swish transition riser", "WHSHMisc", "swish,riser,transition", 2.5, "noise"),
    ("Deep sub boom impact trailer hit", "HITCine", "impact,boom,sub,trailer", 3.0, "sub"),
    ("Low rumble earthquake ground", "DSGNRumble", "rumble,low,earthquake", 8.0, "sub"),
    ("Footsteps gravel walking slow", "FEETGrvl", "footstep,gravel,walk", 6.0, "noise"),
    ("Wind through pine trees", "AMBWind", "wind,pines,forest", 30.0, "noise"),
]


@pytest.fixture
def soundly_db(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    db = tmp_path / "LocalFilesDB"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO Libraries(Id,Name,Path,Monitor) VALUES (1,'Test SFX',?,1)", (str(lib),))
    sr = 48000
    rng = np.random.default_rng(0)
    for i, (desc, cat, tags, length, kind) in enumerate(ROWS, 1):
        n = int(sr * min(length, 2))
        t = np.arange(n) / sr
        audio = 0.5 * np.sin(2 * np.pi * 45 * t) if kind == "sub" else 0.2 * rng.standard_normal(n)
        path = lib / f"sfx_{i:02}.wav"
        sf.write(path, audio, sr)
        con.execute("INSERT INTO Files(Id,Description,CategoryID,Tags,Length,Location,SampleRate,Channels,Format,"
                    "SearchString) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (i, desc, cat, tags, length, str(path), sr, 1, "wav", f"{desc} {tags} {cat}".lower()))
    con.commit()
    con.close()
    monkeypatch.setenv("SOUNDLY_DB", str(db))
    return db


def test_multi_phrasing_search_ranks_consensus_first(soundly_db):
    with SoundlyDB(str(soundly_db)) as db:
        res = db.search(["whoosh transition", "swish pass by", "air swoosh"], limit=5)
        assert res[0]["Description"] == "Big air whoosh pass by fast"
        assert len(res[0]["matched_queries"]) == 3
        assert {r["CategoryID"][:4] for r in res[:2]} == {"WHSH"}
        # plural folding and stop words
        assert db.search(["the sound of pine"])[0]["Description"] == "Wind through pine trees"
        assert db.search(["footsteps"])[0]["CategoryID"] == "FEETGrvl"
        # filters
        assert all(r["Length"] <= 2 for r in db.search(["whoosh", "swish"], max_length=2))
        assert db.search(["zzzz"]) == []


def test_readonly(soundly_db):
    with SoundlyDB(str(soundly_db)) as db, pytest.raises(sqlite3.OperationalError):
        db._con.execute("DELETE FROM Files")


def test_tools_search_inspect_and_place(soundly_db, reaper):
    from reaper_agent import server

    bridge, fake = reaper
    server.bridge = bridge
    bridge.call("add_marker", name="Drop", start_beat=32)

    async def go():
        r = await server.mcp.call_tool("search_sounds", {"queries": ["low rumble", "sub boom impact"]})
        ins = await server.mcp.call_tool("inspect_sounds", {"sound_ids": [3, 1]})
        placed = await server.mcp.call_tool("place_sound", {"sound_id": 1, "at_marker": "drop", "offset_beats": -2})
        return r, ins, placed

    r, ins, placed = anyio.run(go)
    text = str(r)
    assert "Deep sub boom" in text and "Low rumble" in text
    assert "'low'" in str(ins) or '"low"' in str(ins)

    proj = bridge.call("get_project")
    assert [t["name"] for t in proj["tracks"]] == ["SFX"]
    items = bridge.call("list_items", track="SFX")
    assert len(items) == 1 and items[0]["position_qn"] == 30
    assert "/project/SFX/Big air whoosh" in items[0]["source_file"]


def test_missing_library_message(tmp_path, monkeypatch):
    from reaper_agent import server

    monkeypatch.setenv("SOUNDLY_DB", str(tmp_path / "nope"))
    with pytest.raises(Exception, match="wasn't found"):
        anyio.run(lambda: server.mcp.call_tool("search_sounds", {"queries": ["x"]}))
