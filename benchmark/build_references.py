"""Compute the reference answers of the question set from the database and freeze them in
benchmark/references.json.

Every reference SQL runs on a read-only connection; the optional reference computation (trusted code written with
the questions, not by an LLM) turns the results into named values. The file also records which version of the data
was used (a digest of the sha256 of the source files), so a later run on other data is detected.

    python benchmark/build_references.py
"""
import datetime as dt
import hashlib
import json
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from relais.benchmark import REFERENCES_FILE, expected_values, load_questions  # noqa: E402

DB_PATH = ROOT / "data" / "connectivite.duckdb"


def plain(value):
    """numpy / pandas scalars -> JSON values."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value if value is None or isinstance(value, str) else str(value)


def default_values(results):
    """Without a reference computation: one row -> its columns; two columns -> {label: value}."""
    df = results[0]
    if len(df) == 1:
        return df.iloc[0].to_dict()
    if df.shape[1] == 2:
        return dict(zip(df.iloc[:, 0].astype(str), df.iloc[:, 1]))
    raise ValueError(f"{df.shape[0]} rows and {df.shape[1]} columns: a reference computation is needed")


def data_version(con):
    digests = "".join(row[0] for row in con.execute("SELECT sha256 FROM sources ORDER BY fichier").fetchall())
    return hashlib.sha256(digests.encode()).hexdigest()[:16]


def compute(question, con):
    results = [con.execute(sql).df() for sql in question.sql_reference]
    if question.calcul_reference:
        scope = {"resultats": results, "stats": stats, "np": np, "pd": pd, "valeurs": {}}
        exec(question.calcul_reference, scope)  # trusted code from questions.yaml
        values = scope["valeurs"]
    else:
        values = default_values(results)
    values = {str(k): plain(v) for k, v in values.items()}
    missing = [k for k in expected_values(question, values) if values.get(k) is None]
    if missing:
        raise ValueError(f"empty reference values: {missing}")
    return values, [len(r) for r in results]


def main():
    questions = load_questions()
    con = duckdb.connect(str(DB_PATH), read_only=True)
    references, failures = {}, []
    for q in questions:
        if not q.sql_reference:
            references[q.id] = {"valeurs": {}}
            continue
        try:
            values, rows = compute(q, con)
        except Exception as error:  # report every failing question, not only the first one
            failures.append(f"{q.id}: {type(error).__name__}: {error}")
            continue
        references[q.id] = {"valeurs": values, "lignes_resultats": rows}
        shown = ", ".join(f"{k}={v}" for k, v in values.items())
        print(f"{q.id} [{q.categorie}] {shown}"[:180])
    if failures:
        sys.exit("Reference computation failed:\n  " + "\n  ".join(failures))
    payload = {"version_donnees": data_version(con), "calcule_le": dt.date.today().isoformat(),
               "questions": references}
    REFERENCES_FILE.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(questions)} questions, references written to {REFERENCES_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
