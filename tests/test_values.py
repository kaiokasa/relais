"""Harmonisation of the values written in queries with the values of the data (no LLM)."""
import duckdb
import pytest

from relais.tools.sql import SqlTool, check_query
from relais.tools.values import ValueIndex, normalise


@pytest.fixture(scope="module")
def tool(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "values.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sites AS SELECT * FROM (VALUES ('Free Mobile', 'Zones rurales', 'Indoor', 10), "
                "('Orange', 'Zones denses', 'Outdoor', 20), ('Bouygues Telecom', 'Zones rurales', 'Incar', 5)) "
                "t(operateur, zone, situation, n)")
    con.close()
    return SqlTool.open(path)


def test_synonyms_and_spelling_variants_are_corrected(tool):
    table = tool.run("SELECT sum(n) AS n FROM sites WHERE operateur IN ('Free', 'Bouygues') "
                     "AND zone = 'zones  RURALES' AND situation <> 'extérieur'")
    assert table.data.n.iat[0] == 15
    assert len(table.corrections) == 4 and any("'Free' remplacé par 'Free Mobile'" in c for c in table.corrections)
    assert "'Free Mobile'" in table.sql and "Valeur corrigée" in table.preview()


def test_exact_values_are_left_alone(tool):
    table = tool.run("SELECT n FROM sites WHERE operateur = 'Orange'")
    assert table.corrections == [] and table.unknown_values == [] and table.sql.endswith("'Orange'")


def test_unknown_values_are_reported_with_close_ones(tool):
    table = tool.run("SELECT count(*) AS n FROM sites WHERE operateur = 'Fre Mobil'")
    assert table.data.n.iat[0] == 0
    assert "n'existe pas" in table.unknown_values[0] and "'Free Mobile'" in table.unknown_values[0]
    assert "Corrige la requête" in table.preview()


def test_index_resolution():
    index = ValueIndex({"operateur": {"Free Mobile", "Orange"}, "zone": {"Zones rurales"}})
    assert index.resolve("operateur", "free") == ("Free Mobile", "corrected")
    assert index.resolve("operateur", "Orange") == ("Orange", "exact")
    assert index.resolve("operateur", "Lebara") == ("Lebara", "unknown")
    assert index.resolve("autre_colonne", "x") == ("x", "exact")  # columns that are not indexed are not touched
    tree = check_query("SELECT 1 FROM t WHERE zone = 'rural' AND operateur = 'Free'", {"t"})
    corrections, unknown = index.harmonise(tree)
    assert len(corrections) == 2 and unknown == []
    assert normalise(" Zones  Intermédiaires ") == "zones intermediaires"
