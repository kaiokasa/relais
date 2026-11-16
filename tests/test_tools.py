"""The tools of the agents: the locked SQL tool, the Python sandbox and the chart renderer (no LLM, no network)."""
import os

import duckdb
import pandas as pd
import pytest

from relais.tools import charts, sandbox
from relais.tools.sql import QueryFailed, QueryRejected, SqlTool, check_query

KNOWN = {"communes", "fixe_technologies", "vue_taux"}


@pytest.fixture(scope="module")
def tool(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "mini.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE communes AS SELECT * FROM (VALUES ('01053', 'Bourg', 41000), ('2A004', 'Ajaccio', "
                "72000)) t(code_insee, nom, population)")
    con.execute("CREATE TABLE fixe_technologies AS SELECT * FROM (VALUES ('2026_T2', '01053', 100, 90), "
                "('2026_T2', '2A004', 200, 150)) t(trimestre, code_insee, nb_locaux, locaux_eligibles_ftth)")
    con.execute("CREATE TABLE grande AS SELECT range AS x FROM range(300000)")
    con.execute("CREATE VIEW vue_taux AS SELECT code_insee, 100.0 * locaux_eligibles_ftth / nb_locaux AS taux "
                "FROM fixe_technologies")
    con.close()
    return SqlTool.open(path, timeout_s=0.5)


# ---------------------------------------------------------------------------------------------- SQL

def test_select_queries_run_and_are_kept(tool):
    table = tool.run("WITH t AS (SELECT * FROM fixe_technologies) "
                     "SELECT round(100.0 * sum(locaux_eligibles_ftth) / sum(nb_locaux), 1) AS taux FROM t")
    assert table.data.taux.iat[0] == 80.0
    assert tool.tables[table.id] is table and table.id.startswith("t")
    assert "1 lignes" in table.preview()
    view = tool.run("SELECT * FROM vue_taux ORDER BY code_insee")
    assert view.data.taux.tolist() == [90.0, 75.0]


@pytest.mark.parametrize("sql, reason", [
    ("SELECT 1; SELECT 2", "Une seule requête"),
    ("SELECT * FROM communes; DROP TABLE communes", "Une seule requête"),
    ("DROP TABLE communes", "lecture"),
    ("INSERT INTO communes VALUES ('x', 'y', 1)", "lecture"),
    ("CREATE TABLE z AS SELECT 1", "lecture"),
    ("COPY communes TO 'fuite.csv'", "lecture"),
    ("ATTACH 'autre.duckdb' AS x", "lecture"),
    ("PRAGMA version", "lecture"),
    ("SET enable_external_access = true", "lecture"),
    ("INSTALL httpfs", "lecture"),
    ("SELECT * FROM read_csv('secret.csv')", "Source interdite"),
    ("SELECT * FROM 'secret.parquet'", "Table inconnue"),
    ("SELECT * FROM glob('*')", "Source interdite"),
    ("SELECT * FROM autre_base.communes", "Table inconnue"),
    ("SELECT getenv('GEMINI_API_KEY')", "Fonction interdite"),
    ("SELECT * FROM table_absente", "Table inconnue"),
    ("SELECT FROM WHERE", "illisible"),
])
def test_forbidden_queries_are_rejected_before_running(sql, reason):
    with pytest.raises(QueryRejected, match=reason):
        check_query(sql, KNOWN)


def test_duckdb_itself_is_locked(tool):
    """Second lock: even a query that skipped the parser could not reach files or change the settings."""
    for sql in ("SELECT * FROM read_csv('pyproject.toml')", "COPY communes TO 'fuite.csv'",
                "SET enable_external_access = true", "SET lock_configuration = false", "ATTACH 'x.duckdb'"):
        with pytest.raises(duckdb.Error):
            tool.con.execute(sql)
    assert not os.path.exists("fuite.csv")


def test_execution_errors_and_timeout(tool):
    with pytest.raises(QueryFailed, match="Erreur DuckDB"):
        tool.run("SELECT colonne_absente FROM communes")
    with pytest.raises(QueryFailed, match="interrompue"):
        tool.run("SELECT count(*) FROM grande a, grande b WHERE (a.x * 31 + b.x) % 1000003 = 7")


def test_describe_lists_tables_and_columns(tool):
    text = tool.describe()
    assert "communes:" in text and "code_insee VARCHAR" in text and "vue_taux:" in text


# ---------------------------------------------------------------------------------------------- sandbox

DATA = {"t1": pd.DataFrame({"x": range(50), "y": [i % 7 for i in range(50)]})}


def run(code, **kwargs):
    return sandbox.run_python(code, DATA, timeout_s=kwargs.pop("timeout_s", 10), **kwargs)


def test_sandbox_runs_statistics_on_the_tables():
    result = run("r = stats.spearmanr(t1.x, t1.y)\nmodele = smf.ols('y ~ x', data=tables['t1']).fit()\n"
                 "print('n =', len(t1))\nresultat = {'rho': r.statistic, 'pente': modele.params['x'], "
                 "'resume': t1.describe()}")
    assert result.ok, result.erreur
    assert -1 <= result.resultat["rho"] <= 1 and "pente" in result.resultat
    assert result.resultat["resume"]["type"] == "tableau"
    assert "n = 50" in result.sortie
    assert "rho" in result.summary()


@pytest.mark.parametrize("code", [
    "open('fuite.txt', 'w').write('x')",
    f"open({os.path.abspath('pytest.ini')!r}).read()",
    f"pd.read_csv({os.path.abspath('pytest.ini')!r})",
    "import os; os.listdir('.')",
    "import os; os.system('echo intrus')",
    "import os; os.remove('pytest.ini')",
    "import subprocess; subprocess.run([sys.executable, '--version'])",
    "import socket; socket.create_connection(('example.com', 80), timeout=2)",
    "import ctypes; ctypes.CDLL(None if sys.platform != 'win32' else 'msvcrt')",
    "import shutil; shutil.copy('pytest.ini', 'copie.ini')",
])
def test_sandbox_blocks_files_network_processes_and_ctypes(code):
    result = run("import sys\n" + code)
    assert not result.ok
    assert "interdit" in result.erreur or "Blocked" in result.erreur
    assert not os.path.exists("fuite.txt") and not os.path.exists("copie.ini")


def test_sandbox_does_not_see_the_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "cle-secrete-de-test")
    result = run("import os\nresultat = [k for k in os.environ if 'KEY' in k or 'TOKEN' in k]")
    assert result.ok and result.resultat == []


def test_sandbox_timeout_and_errors():
    result = run("while True:\n    pass", timeout_s=1)
    assert result.timeout and not result.ok
    result = run("1 / 0")
    assert not result.ok and "ZeroDivisionError" in result.erreur and "sandbox_runner" not in result.erreur
    result = run("raise SystemExit(3)")
    assert not result.ok


def test_sandbox_output_is_truncated():
    result = run("print('x' * 50000)", max_output=1000)
    assert result.ok and result.sortie_tronquee and len(result.sortie) == 1000


# ---------------------------------------------------------------------------------------------- charts

TABLES = {
    "t1": pd.DataFrame({"trimestre": ["2025_T4", "2026_T1", "2026_T2"], "taux": [93.2, 93.8, 94.3]}),
    "t2": pd.DataFrame({"mois": [1, 2, 1, 2], "operateur": ["A", "A", "B", "B"], "sites": [10, 12, 5, 9]}),
    "t3": pd.DataFrame({"zone": ["dense"] * 3 + ["rurale"] * 3, "debit": [200, 250, 230, 40, 35, 50]}),
}


def test_valid_specifications_are_drawn():
    line = charts.render({"type": "line", "tableau": "t1", "x": "trimestre", "y": "taux", "titre": "FttH"}, TABLES)
    assert line.type == "line" and line.series == 1 and line.points == 3
    assert not line.figure.layout.showlegend
    multi = charts.render({"type": "line", "tableau": "t2", "x": "mois", "y": "sites", "couleur": "operateur"},
                          TABLES)
    assert multi.series == 2 and multi.figure.layout.showlegend
    assert [a.text for a in multi.figure.layout.annotations] == ["A", "B"]  # direct labels
    assert multi.figure.data[0].line.color == charts.PALETTE[0]  # fixed colour order
    box = charts.render({"type": "box", "tableau": "t3", "x": "zone", "y": "debit"}, TABLES)
    assert box.series == 2
    bar = charts.render({"type": "bar", "tableau": "t1", "x": "trimestre", "y": "taux", "orientation": "h"}, TABLES)
    assert bar.figure.data[0].orientation == "h"


@pytest.mark.parametrize("spec, message", [
    ({"type": "camembert", "tableau": "t1", "x": "trimestre", "y": "taux"}, "Type inconnu"),
    ({"type": "line", "tableau": "t9", "x": "trimestre", "y": "taux"}, "Tableau inconnu"),
    ({"type": "line", "tableau": "t1", "x": "trimestre", "y": "absente"}, "Colonnes absentes"),
    ({"type": "line", "tableau": "t1", "x": "trimestre", "y": "trimestre"}, "numérique"),
    ({"type": "bar", "tableau": "t1", "x": "trimestre"}, "demande x et y"),
    ({"type": "line", "tableau": "t1", "x": "trimestre", "y": "taux", "axe_secondaire": "x"}, "Clé inconnue"),
])
def test_invalid_specifications_explain_what_to_fix(spec, message):
    with pytest.raises(charts.ChartError, match=message):
        charts.render(spec, TABLES)


def test_label_spreading_keeps_order_and_gap():
    placed = charts.spread_labels([10.0, 10.2, 30.0], min_gap=1.0)
    assert placed[1] - placed[0] >= 1.0 - 1e-9 and placed[2] > placed[1]


def test_sandbox_service_runs_the_job_like_the_local_sandbox(monkeypatch):
    """The Docker mode: the app sends the job to the sandbox service over HTTP and gets the same result."""
    import threading

    from relais.tools.sandbox_server import serve
    server = serve("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("RELAIS_SANDBOX_URL", f"http://127.0.0.1:{server.server_address[1]}")
        result = sandbox.run_python("resultat = int(t1['x'].sum())", {"t1": pd.DataFrame({"x": [1, 2, 3]})})
        assert result.ok and result.resultat == 6
        blocked = sandbox.run_python("open('x.txt', 'w')")
        assert not blocked.ok and "interdit" in blocked.erreur
    finally:
        server.shutdown()


def test_sandbox_service_unreachable(monkeypatch):
    monkeypatch.setenv("RELAIS_SANDBOX_URL", "http://127.0.0.1:9")
    result = sandbox.run_python("resultat = 1", timeout_s=1)
    assert not result.ok and "injoignable" in result.erreur


def test_preview_shows_where_a_long_table_ends():
    from relais.tools.sql import head_and_tail
    frame = pd.DataFrame({"trimestre": [f"20{19 + i // 4}_T{i % 4 + 1}" for i in range(28)], "taux": range(28)})
    text = head_and_tail(frame, 3, 30)
    assert "2019_T1" in text and "2025_T4" in text and "23 lignes non montrées" in text
    assert head_and_tail(frame.head(5), 3, 30).count("\n") == 5  # short table: shown whole
