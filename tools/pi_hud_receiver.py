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
from pathlib import Path
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


HUD_PAGE = Path(__file__).with_name("pi_hud.html")

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
        if self.path == "/" or self.path.startswith("/?"):
            return self.respond(200, HUD_PAGE.read_text(encoding="utf-8"), "text/html")
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
            lanes = payload.get("lanes", [])
            if not isinstance(lanes, list) or len(lanes) > 12:
                raise ValueError("invalid lanes")
            for lane in lanes:
                if not isinstance(lane, dict):
                    raise ValueError("invalid lane")
                if type(lane.get("index")) is not int:
                    raise ValueError("invalid lane index")
                if type(lane.get("angle")) is not int or not -360 <= lane["angle"] <= 360:
                    raise ValueError("invalid lane angle")
                if not isinstance(lane.get("recommended"), bool):
                    raise ValueError("invalid lane recommendation")
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
