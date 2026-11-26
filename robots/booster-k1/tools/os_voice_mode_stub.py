"""Dev-only stand-in for os-server's Harness voice-mode endpoint (port 5000).

HAL asks os-server which agent owns the microphone before every voice turn
(GET /api/harness/voice-mode) and fails closed when os-server is absent, so on a
dev PC without os-server HAL refuses every turn. This answers that one question
with os-server's default: Harness off, the device's own assistant owns the mic.

Not for a robot: there, the real os-server answers. Run next to HAL:
  hal/.venv/bin/python robots/booster-k1/tools/os_voice_mode_stub.py
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Same envelope and fields as system/harness/voice.go with Harness disabled.
_VOICE_MODE = {
    "status": 1,
    "data": {"enabled": False, "generation": 0, "focusAvailable": False, "supported": False},
    "message": None,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/api/harness/voice-mode":
            body = json.dumps(_VOICE_MODE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print("[os-stub] Harness voice-mode stub on http://127.0.0.1:5000 (Harness off)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 5000), Handler).serve_forever()
