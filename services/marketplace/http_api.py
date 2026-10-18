"""Local marketplace REST API for dynamic registration and RFQ discovery."""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from services.marketplace.models import AgentCard, TaskRequest


def serve(registry, market, host="127.0.0.1", port=8080):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            raw=json.dumps(payload, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            if self.path.startswith("/api/agents"):
                skill = self.path.split("skill=", 1)[1] if "skill=" in self.path else None
                self._send(200, {"agents": [c.as_dict() for c in registry.discover(skill)]})
            else: self._send(404, {"error":"not found"})
        def do_POST(self):
            try: body=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))
            except Exception: self._send(400,{"error":"invalid JSON"}); return
            try:
                if self.path == "/api/agents/register":
                    self._send(201, registry.register(AgentCard(body["agent_id"],body["name"],body["url"],body["skills"],body["wallet"],body.get("version","1.0.0"))).as_dict()); return
                if self.path == "/api/agents/unregister": registry.unregister(body["agent_id"]); self._send(200,{"status":"inactive"}); return
                if self.path == "/api/rfq":
                    task=TaskRequest(body["job_id"],body["task_id"],body.get("attempt",0),body["skill"],body.get("payload",{}),body["requirement_hash"])
                    quotes=market.request_quotes(task,payment_token=body["payment_token"],budget=int(body["budget"]))
                    self._send(200,{"quotes":[q.as_dict() for q in quotes]}); return
                self._send(404,{"error":"not found"})
            except Exception as exc: self._send(400,{"error":str(exc)})
        def log_message(self,*_): return
    ThreadingHTTPServer((host,port),Handler).serve_forever()
