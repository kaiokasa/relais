"""The sandbox as a service, for Docker: it runs in its own container, on a network with no Internet access, with a
read-only file system, no Linux capabilities and a non-root user (see docker-compose.yml).

The app sends a job (code, tables, limits) with POST /run; the server runs it with the same local sandbox as
outside Docker (a separate process with the audit hook) and answers with the result in JSON. The container is the
security boundary; the audit hook stays as a second line that catches the usual mistakes of an LLM.

    python -m relais.tools.sandbox_server          # listens on 0.0.0.0:8800
"""
import dataclasses
import json
import os
import pickle
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from relais.tools.sandbox import run_local

MAX_JOB_BYTES = 64 * 1024 * 1024
SLOTS = threading.BoundedSemaphore(int(os.environ.get("RELAIS_SANDBOX_SLOTS", "4")))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._answer(200, {"ok": True} if self.path == "/health" else {"ok": False})

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        if self.path != "/run" or not 0 < size <= MAX_JOB_BYTES:
            self._answer(400, {"ok": False, "erreur": "Requête refusée par le bac à sable."})
            return
        # the job comes from the app (trusted); what the untrusted code produces goes back as JSON only
        job = pickle.loads(self.rfile.read(size))
        with SLOTS:
            result = run_local(job["code"], job.get("tables"), job["timeout_s"], job["max_output"])
        self._answer(200, dataclasses.asdict(result))

    def _answer(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # one line per job is enough in the container logs
        pass


def serve(host="0.0.0.0", port=8800):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    print("Bac à sable à l'écoute sur le port 8800", flush=True)
    serve().serve_forever()
