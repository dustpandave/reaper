"""Client for the AgentBridge.lua script running inside REAPER (file-based IPC)."""

from __future__ import annotations

import json
import os
import platform
import threading
import time
import uuid
from pathlib import Path


def default_bridge_dir() -> Path:
    env = os.environ.get("REAPER_AGENT_BRIDGE_DIR")
    if env:
        return Path(env).expanduser()
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "REAPER"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home())) / "REAPER"
    else:
        base = Path.home() / ".config" / "REAPER"
    return base / "AgentBridge"


class BridgeError(RuntimeError):
    pass


class ReaperBridge:
    def __init__(self, directory: Path | None = None, timeout: float = 15.0):
        self.dir = Path(directory) if directory else default_bridge_dir()
        self.timeout = timeout
        self._lock = threading.Lock()

    @property
    def request_path(self) -> Path:
        return self.dir / "request.json"

    @property
    def response_path(self) -> Path:
        return self.dir / "response.json"

    def bridge_alive(self) -> bool:
        hb = self.dir / "heartbeat"
        try:
            return time.time() - hb.stat().st_mtime < 5
        except FileNotFoundError:
            return False

    def call(self, cmd: str, timeout: float | None = None, **params):
        if not self.bridge_alive():
            raise BridgeError(
                "REAPER bridge isn't running. Open REAPER and run the 'AgentBridge.lua' action "
                f"(looking for a heartbeat in {self.dir})."
            )
        params = {k: v for k, v in params.items() if v is not None}
        req_id = uuid.uuid4().hex
        payload = json.dumps({"id": req_id, "cmd": cmd, "params": params})
        deadline = time.time() + (timeout or self.timeout)
        with self._lock:
            self.response_path.unlink(missing_ok=True)
            tmp = self.dir / f"request.{req_id}.tmp"
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.request_path)
            while time.time() < deadline:
                try:
                    raw = self.response_path.read_text(encoding="utf-8")
                    resp = json.loads(raw)
                except (FileNotFoundError, json.JSONDecodeError):
                    time.sleep(0.02)
                    continue
                if resp.get("id") != req_id:
                    time.sleep(0.02)
                    continue
                self.response_path.unlink(missing_ok=True)
                if not resp.get("ok"):
                    raise BridgeError(resp.get("error", "unknown error from REAPER"))
                return resp.get("result")
            self.request_path.unlink(missing_ok=True)
        raise BridgeError(f"REAPER didn't answer '{cmd}' within {timeout or self.timeout:.0f}s "
                          "(a modal dialog may be open in REAPER).")
