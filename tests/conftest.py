"""Shared fixture: the real AgentBridge.lua running against an in-memory fake REAPER."""

import threading
import time
from pathlib import Path

import pytest

from reaper_agent.bridge import ReaperBridge

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reaper(tmp_path):
    lua = __import__('lupa').LuaRuntime(unpack_returned_tuples=True)
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


