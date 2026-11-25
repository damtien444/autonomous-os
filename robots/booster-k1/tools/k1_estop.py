"""Standalone K1 stop page: one big button that stops the robot and keeps it standing.

Stop = zero walking velocity, then Prepare mode (kPrepare): the robot halts any
walking or gesture and holds a standing pose instead of going limp.

Runs as its own process with its own DDS connection to the robot; it does not
depend on HAL, the voice agent or any bridge. This is a software stop over the
network, not a hardware e-stop; keep Booster's remote in hand (LT+BACK there
is damping, the limp stop).

Run (HAL's venv has the Booster SDK):
  BOOSTER_NET_IFACE=enx00e04cfbda07 hal/.venv/bin/python robots/booster-k1/tools/k1_estop.py
then open http://127.0.0.1:8099
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>K1 Stop</title>
<style>
  html,body{margin:0;height:100%;background:#111;color:#eee;font-family:system-ui,sans-serif}
  body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:24px;padding:16px;box-sizing:border-box}
  button{width:min(80vw,420px);height:min(80vw,420px);border-radius:50%;border:8px solid #fff;
         background:#d00;color:#fff;font-size:clamp(40px,12vw,84px);font-weight:800;cursor:pointer}
  button:active{background:#900}
  #st{font-size:20px;min-height:1.5em;text-align:center}
  #mode{font-size:16px;color:#aaa}
</style></head><body>
<button id="b" title="Space or Enter also stops">STOP</button>
<div id="st">Ready. Stop = halt and stand (Prepare mode).</div>
<div id="mode">mode: ?</div>
<script>
const st=document.getElementById('st'), md=document.getElementById('mode');
async function stop(){
  st.textContent='Stopping...';
  try{
    const r=await fetch('/stop',{method:'POST'}); const j=await r.json();
    st.textContent=j.ok?('STOPPED, standing (Prepare) in '+j.ms+' ms'):('FAILED: '+j.error);
    st.style.color=j.ok?'#6f6':'#f66';
  }catch(e){st.textContent='FAILED: '+e;st.style.color='#f66'}
  poll();
}
async function poll(){
  try{const j=await (await fetch('/status')).json(); md.textContent='mode: '+(j.mode||j.error)}
  catch(e){md.textContent='mode: unreachable'}
}
document.getElementById('b').onclick=stop;
document.addEventListener('keydown',e=>{if(e.code==='Space'||e.code==='Enter'){e.preventDefault();stop()}});
poll(); setInterval(poll,2000);
</script></body></html>
"""


class Robot:
    """One loco client, created once so a stop press costs only the RPC itself."""

    def __init__(self, iface: str, domain: int):
        import booster_robotics_sdk_python as sdk

        self._sdk = sdk
        sdk.ChannelFactory.Instance().Init(domain, iface)
        self._client = sdk.B1LocoClient()
        self._client.Init()
        self._lock = threading.Lock()
        self.ready = self._client.WaitForService(10000)

    def stop_and_stand(self) -> None:
        """Zero walking velocity first (fastest halt), then hold a standing pose."""
        with self._lock:
            try:
                self._client.Move(0.0, 0.0, 0.0)
            except Exception as e:
                print(f"[estop] zero-velocity command failed: {e}", flush=True)
            self._client.ChangeMode(self._sdk.RobotMode.kPrepare)

    def mode(self) -> str:
        with self._lock:
            return self._client.GetMode().mode.name


def make_handler(robot: Robot):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/status":
                try:
                    self._json(200, {"mode": robot.mode()})
                except Exception as e:
                    self._json(200, {"error": str(e)})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/stop":
                self._json(404, {"error": "not found"})
                return
            t = time.perf_counter()
            # Retry once: a single lost RPC must not leave the robot running.
            err = None
            for _ in range(2):
                try:
                    robot.stop_and_stand()
                    err = None
                    break
                except Exception as e:
                    err = str(e)
            ms = round((time.perf_counter() - t) * 1000)
            print(f"[estop] stop+prepare {'sent' if err is None else 'FAILED: ' + err} ({ms} ms)", flush=True)
            self._json(200, {"ok": err is None, "ms": ms, "error": err})

        def log_message(self, fmt, *args):  # keep the console for stop events only
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()
    robot = Robot(os.getenv("BOOSTER_NET_IFACE", ""), int(os.getenv("BOOSTER_DDS_DOMAIN", "0")))
    print(f"[estop] loco service {'reachable' if robot.ready else 'NOT reachable'}; "
          f"open http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(robot)).serve_forever()


if __name__ == "__main__":
    main()
