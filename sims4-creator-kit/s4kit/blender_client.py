"""Client for the S4Kit Blender bridge add-on (blender_addon/s4kit_bridge.py)."""
from __future__ import annotations

import json
import os
import socket


class BlenderError(RuntimeError):
    pass


class BlenderClient:
    def __init__(self, host: str = "127.0.0.1", port: int | None = None, timeout: float = 600):
        self.host = host
        self.port = port or int(os.environ.get("S4KIT_BLENDER_PORT", "9876"))
        self.timeout = timeout
        self.token = os.environ.get("S4KIT_TOKEN", "")

    def call(self, cmd: str, **params):
        payload = {"cmd": cmd, "params": params, "timeout": self.timeout}
        if self.token:
            payload["token"] = self.token
        try:
            with socket.create_connection((self.host, self.port), timeout=5) as s:
                s.settimeout(self.timeout + 5)
                s.sendall((json.dumps(payload) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(1 << 16)
                    if not chunk:
                        break
                    buf += chunk
        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            raise BlenderError(
                f"Can't reach Blender bridge on {self.host}:{self.port} ({e}). Start Blender with "
                "the blender_launch tool, or enable the S4Kit add-on and press 'Start bridge'.") from e
        resp = json.loads(buf)
        if not resp.get("ok"):
            raise BlenderError(resp.get("error", "unknown error") + "\n" + resp.get("traceback", ""))
        return resp["result"]
