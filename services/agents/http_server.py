"""Run one Agent Card and A2A task endpoint using only the Python stdlib."""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from .base import BaseAgent
from services.marketplace.models import TaskRequest


def serve(agent: BaseAgent, host: str = "127.0.0.1", port: int | None = None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/.well-known/agent.json": self._send(200, agent.card.as_dict())
            elif path == "/health": self._send(200, {"status": "ok", "agent_id": agent.agent_id})
            else: self._send(404, {"error": "not found"})
        def do_POST(self):
            if urlparse(self.path).path not in ("/tasks/send", "/a2a/tasks"):
                self._send(404, {"error": "not found"}); return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                task = TaskRequest(body["job_id"], int(body["task_id"]), int(body["attempt"]), body["skill"], body.get("payload", {}), body["requirement_hash"], int(body.get("deadline", 0)))
                self._send(200, {"task_id": task.task_id, "status": "completed", "artifact": agent.handle(task).as_dict()})
            except TimeoutError as exc: self._send(504, {"status": "failed", "error": str(exc)})
            except Exception as exc: self._send(400, {"status": "failed", "error": str(exc)})
        def log_message(self, *_): return
    httpd = ThreadingHTTPServer((host, port or agent.port or 0), Handler)
    agent.card.url = f"http://{host}:{httpd.server_port}"
    print(json.dumps({"event": "agent_started", "agent": agent.card.as_dict()}, ensure_ascii=False), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    from .business import BusinessAgent
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--id", default="cleaner-a"); p.add_argument("--skill", default="data_cleaning"); p.add_argument("--port", type=int, default=8001); a = p.parse_args()
    serve(BusinessAgent(a.id, a.id, [a.skill], port=a.port), port=a.port)
