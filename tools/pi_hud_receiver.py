#!/usr/bin/env python3
"""Local Pi receiver for DiPlay's structured CarPlay guidance.

Run: python3 pi_hud_receiver.py
Open http://192.168.8.136:8765/ for a live text view,
or GET /navigation for machine-readable JSON.
No dependencies beyond Python's standard library.
"""
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8765
STALE_SECONDS = 9.0
LOCK = threading.Lock()
latest = {}
last_received = 0.0


def current():
    with LOCK:
        data = latest.copy()
        age = time.monotonic() - last_received if last_received else None
    if age is None or age > STALE_SECONDS:
        data = {"connected": False, "navigationActive": False, "maneuverType": None,
                "road": "", "distanceMeters": None,
                "remainingMeters": None, "remainingSeconds": None,
                "arrivalEpochSeconds": None}
    data["stale"] = age is None or age > STALE_SECONDS
    data["ageSeconds"] = round(age, 1) if age is not None else None
    return data


PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DiPlay Pi navigation</title><style>
body{background:#080c12;color:#e6ffed;font:20px system-ui,sans-serif;margin:40px}
.big{font-size:clamp(36px,10vw,100px);font-weight:800}
small{color:#a0bdaf}code{font-size:15px;overflow-wrap:anywhere}
</style></head><body>
<h2>CarPlay → Pi navigation</h2><div id="status">Waiting for DiPlay</div>
<div class="big" id="turn">–</div><div class="big" id="distance"></div>
<div id="road"></div><p><small id="eta"></small></p>
<details><summary>Raw navigation data</summary><pre id="raw"></pre></details>
<script>
async function refresh(){
 try{
  const r=await fetch('/navigation',{cache:'no-store'});const d=await r.json();
  document.querySelector('#status').textContent=d.stale?'No fresh data':d.navigationActive?'Guidance active':d.connected?'CarPlay connected; no route':'CarPlay disconnected';
  document.querySelector('#turn').textContent=d.maneuverType==null?'–':'Maneuver '+d.maneuverType;
  document.querySelector('#distance').textContent=d.distanceMeters==null?'':d.distanceMeters+' m to turn';
  document.querySelector('#road').textContent=d.road||'';
  document.querySelector('#eta').textContent=d.remainingSeconds==null?'':'Remaining '+Math.round(d.remainingSeconds/60)+' minutes';
  document.querySelector('#raw').textContent=JSON.stringify(d,null,2)
 }catch(e){document.querySelector('#status').textContent='Pi receiver offline'}
}
refresh();setInterval(refresh,2000)
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data, content_type="application/json"):
        output = data if isinstance(data, bytes) else data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(output)))
        self.end_headers()
        self.wfile.write(output)

    def do_GET(self):
        if self.path == "/":
            return self.respond(200, PAGE, "text/html")
        if self.path == "/health":
            return self.respond(200, json.dumps({"ok": True, "received": bool(last_received)}))
        if self.path == "/navigation":
            return self.respond(200, json.dumps(current()))
        self.respond(404, '{"error":"not_found"}')

    def do_POST(self):
        global latest, last_received
        if self.path != "/navigation":
            return self.respond(404, '{"error":"not_found"}')
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 4096:
                return self.respond(413, '{"error":"bad_length"}')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("not an object")
            if not isinstance(payload.get("connected"), bool):
                raise ValueError("invalid connected field")
            if not isinstance(payload.get("navigationActive"), bool):
                raise ValueError("invalid navigationActive field")
            maneuver = payload.get("maneuverType")
            if maneuver is not None and (type(maneuver) is not int or not 0 <= maneuver <= 255):
                raise ValueError("invalid maneuver")
            road = payload.get("road", "")
            if not isinstance(road, str) or len(road) > 256:
                raise ValueError("invalid road")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return self.respond(400, '{"error":"bad_payload"}')
        with LOCK:
            latest = payload
            last_received = time.monotonic()
        self.respond(200, '{"ok":true}')

    def log_message(self, fmt, *args):
        if self.command == "POST" and args and str(args[1]) == "200":
            return  # avoid a log line every 2 seconds
        logging.info("%s - %s", self.client_address[0], fmt % args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"DiPlay HUD receiver listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
