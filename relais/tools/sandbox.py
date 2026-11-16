"""The Python sandbox of the statistics agent.

The code runs in a separate Python process (isolated mode, empty working directory, an environment reduced to what
Python needs: no API key), with pandas, numpy, scipy and statsmodels already imported, the result tables of the SQL
agent as DataFrames (`tables['t1']` or simply `t1`), and an audit hook that blocks files, network, processes and
ctypes. The code puts what it wants to return in a variable named `resultat`.

The timeout counts from the start of the code (the import of the libraries, about 2 s, is not counted); the process
is killed when it is reached.

With Docker (docker-compose.yml), RELAIS_SANDBOX_URL points to the sandbox service: the same process runs there, in
a container with no Internet access, a read-only file system and no privileges (relais/tools/sandbox_server.py).
"""
import json
import os
import pickle
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RUNNER = Path(__file__).with_name("sandbox_runner.py")
DEFAULT_TIMEOUT_S = 15
STARTUP_TIMEOUT_S = 60
MAX_OUTPUT_CHARS = 8000
ENV_KEPT = ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")  # what Python and numpy need to start on Windows


@dataclass
class SandboxResult:
    ok: bool
    resultat: object = None
    sortie: str = ""
    erreur: str | None = None
    duree_ms: float = 0.0
    sortie_tronquee: bool = False
    timeout: bool = False

    def summary(self, limit=4000):
        """What the LLM sees."""
        if self.timeout:
            return "Échec : le code a dépassé le temps maximal et a été arrêté."
        parts = [] if self.ok else [f"Erreur :\n{self.erreur}"]
        if self.sortie.strip():
            parts.append(f"Sortie :\n{self.sortie.strip()}")
        if self.resultat is not None:
            parts.append(f"resultat = {json.dumps(self.resultat, ensure_ascii=False)}")
        if self.ok and not parts:
            parts.append("Le code s'est exécuté sans rien afficher ; mettre la réponse dans la variable resultat.")
        return "\n".join(parts)[:limit]


def child_environment():
    env = {name: os.environ[name] for name in ENV_KEPT if name in os.environ}
    env.update({"PYTHONIOENCODING": "utf-8", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
                "MKL_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1",
                # statsmodels imports polars, whose CPU check fails in a reduced environment
                "POLARS_SKIP_CPU_CHECK": "1"})
    return env


def _read_line(stream, lines):
    lines.put(stream.readline())


def run_python(code, tables=None, timeout_s=DEFAULT_TIMEOUT_S, max_output=MAX_OUTPUT_CHARS):
    """Run `code` in the sandbox with the given DataFrames; returns a SandboxResult. In the sandbox container when
    RELAIS_SANDBOX_URL is set, otherwise in a local process."""
    url = os.environ.get("RELAIS_SANDBOX_URL")
    if url:
        return run_remote(url, code, tables, timeout_s, max_output)
    return run_local(code, tables, timeout_s, max_output)


def run_remote(url, code, tables, timeout_s, max_output):
    job = pickle.dumps({"code": code, "tables": dict(tables or {}), "timeout_s": timeout_s,
                        "max_output": max_output})
    request = urllib.request.Request(url.rstrip("/") + "/run", data=job, method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s + STARTUP_TIMEOUT_S + 10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return SandboxResult(ok=False, erreur=f"Le bac à sable est injoignable : {error}")
    return SandboxResult(**payload)


def run_local(code, tables=None, timeout_s=DEFAULT_TIMEOUT_S, max_output=MAX_OUTPUT_CHARS):
    """Run `code` in a separate local Python process."""
    job = pickle.dumps({"code": code, "tables": dict(tables or {}), "max_output": max_output})
    with tempfile.TemporaryDirectory(prefix="relais-sandbox-") as workdir:
        process = subprocess.Popen([sys.executable, "-I", "-X", "utf8", str(RUNNER)], cwd=workdir,
                                   env=child_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            lines = queue.Queue()
            threading.Thread(target=_read_line, args=(process.stdout, lines), daemon=True).start()
            try:
                ready = lines.get(timeout=STARTUP_TIMEOUT_S)
            except queue.Empty:
                ready = b""
            if ready.strip() != b"READY":
                process.kill()
                _, err = process.communicate()
                return SandboxResult(ok=False, erreur="Le bac à sable n'a pas démarré : "
                                     + err.decode("utf-8", "replace")[-1500:])
            started = time.perf_counter()
            try:
                out, err = process.communicate(job, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                return SandboxResult(ok=False, timeout=True, erreur=f"Temps maximal dépassé ({timeout_s:g} s).",
                                     duree_ms=round((time.perf_counter() - started) * 1000, 1))
        finally:
            if process.poll() is None:
                process.kill()
    last = out.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
    try:
        payload = json.loads(last[0])
    except json.JSONDecodeError:
        return SandboxResult(ok=False, erreur="Le bac à sable s'est arrêté sans résultat : "
                             + err.decode("utf-8", "replace")[-1500:])
    return SandboxResult(**payload)
