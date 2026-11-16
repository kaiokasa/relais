"""Runs the Python code of the statistics agent in a separate process (started by sandbox.py with `python -I`).

Protocol: imports the allowed libraries, installs the audit hook, prints READY, reads the job from stdin (pickled by
the parent process, which is trusted), runs the code and prints ONE JSON line (JSON, not pickle, so the parent never
deserialises anything produced by the untrusted code).

The audit hook blocks, for the code that runs after it: writing or reading files outside the Python installation,
network access, new processes, ctypes and the os functions that touch the file system. It guards against the
mistakes of an LLM; it is not a security boundary against a determined attacker (that would need an OS sandbox).
"""
import collections  # noqa: F401  (made available to the code)
import contextlib
import datetime  # noqa: F401
import io
import itertools  # noqa: F401
import json
import math
import os
import pickle
import re  # noqa: F401
import statistics  # noqa: F401
import sys
import time
import traceback
import warnings

import numpy as np
import pandas as pd
import scipy
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats

# warm up the lazy imports the usual analyses need, before the hook forbids loading new modules from disk
stats.spearmanr([1, 2, 3], [1, 3, 2]), stats.kruskal([1, 2], [3, 4]), stats.chi2_contingency([[5, 6], [7, 8]])
smf.ols("y ~ x", data=pd.DataFrame({"x": [1.0, 2, 3, 4], "y": [1.0, 3, 2, 5]})).fit().summary()
pd.DataFrame({"a": [1]}).to_string(), pd.DataFrame({"a": [1]}).describe()

PYTHON_HOME = tuple(os.path.normcase(os.path.realpath(p)) + os.sep for p in {sys.prefix, sys.base_prefix})
BLOCKED_EVENTS = ("os.", "subprocess.", "socket.", "shutil.", "ctypes.", "_winapi.", "winreg.", "urllib.", "http.",
                  "ftplib.", "smtplib.", "webbrowser.", "pty.", "mmap.", "msvcrt.", "sqlite3.", "glob.", "tempfile.",
                  "sys._current_frames", "sys.settrace", "sys.setprofile", "code.", "cpython.")
BLOCKED_MODULES = {"ctypes", "_ctypes", "socket", "_socket", "subprocess", "multiprocessing", "urllib", "http",
                   "requests", "shutil", "pathlib", "importlib", "duckdb"}


class Blocked(PermissionError):
    pass


def audit(event, args):
    if event == "open":
        path = args[0]
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        # open() gives a mode string; os.open() gives no mode but flags
        writing = (any(m in mode for m in "wax+") if isinstance(mode, str)
                   else bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)))
        if isinstance(path, int) or writing:
            raise Blocked("Accès aux fichiers interdit dans le bac à sable.")
        real = os.path.normcase(os.path.realpath(os.fsdecode(path)))
        if not real.startswith(PYTHON_HOME):  # Python can still load its own library files
            raise Blocked("Accès aux fichiers interdit dans le bac à sable.")
    elif event == "import" and args[0].split(".")[0] in BLOCKED_MODULES:
        raise Blocked(f"Module interdit dans le bac à sable : {args[0]}.")
    elif event.startswith(BLOCKED_EVENTS):
        raise Blocked(f"Opération interdite dans le bac à sable : {event}.")


def jsonable(value, depth=0, max_rows=200):
    """The result as JSON values: tables are cut to their first rows."""
    if depth > 6:
        return repr(value)[:200]
    if isinstance(value, pd.DataFrame):
        head = value.head(max_rows)
        return {"type": "tableau", "colonnes": [str(c) for c in head.columns], "n_lignes": len(value),
                "lignes": [[jsonable(v, depth + 1) for v in row] for row in head.itertuples(index=False)]}
    if isinstance(value, pd.Series):
        return {str(k): jsonable(v, depth + 1) for k, v in value.head(max_rows).items()}
    if isinstance(value, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in itertools.islice(value.items(), max_rows)}
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [jsonable(v, depth + 1) for v in list(value)[:max_rows]]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(value) or math.isinf(value) else float(value)
    if value is None or isinstance(value, str):
        return value
    if hasattr(value, "_asdict"):  # scipy results (SignificanceResult, ...)
        return jsonable(value._asdict(), depth + 1)
    if hasattr(value, "statistic") and hasattr(value, "pvalue"):
        return {"statistic": jsonable(value.statistic, depth + 1), "pvalue": jsonable(value.pvalue, depth + 1)}
    return repr(value)[:2000]


def main():
    out = sys.stdout
    out.write("READY\n")
    out.flush()
    job = pickle.load(sys.stdin.buffer)  # written by the trusted parent process
    sys.addaudithook(audit)
    scope = {"pd": pd, "np": np, "stats": stats, "scipy": scipy, "sm": sm, "smf": smf, "math": math,
             "tables": job["tables"], **job["tables"]}
    captured = io.StringIO()
    started = time.perf_counter()
    ok, error = True, None
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            exec(compile(job["code"], "<code de l'agent>", "exec"), scope)
    except BaseException as exc:  # the code of the agent can raise anything, SystemExit included
        ok = False
        lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        error = "".join(line for line in lines if "sandbox_runner" not in line)[-3000:]
    printed = captured.getvalue()
    result = {"ok": ok, "erreur": error, "sortie": printed[-job["max_output"]:],
              "sortie_tronquee": len(printed) > job["max_output"],
              "resultat": jsonable(scope.get("resultat")),
              "duree_ms": round((time.perf_counter() - started) * 1000, 1)}
    out.write(json.dumps(result, ensure_ascii=False) + "\n")
    out.flush()


if __name__ == "__main__":
    main()
