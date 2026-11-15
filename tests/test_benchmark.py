"""The question set and its frozen reference answers (no database needed)."""
import json
from collections import Counter

import pandas as pd
import pytest

from benchmark import build_references
from relais import benchmark
from relais.benchmark import Question, check, expected_values, load_questions


@pytest.fixture(scope="module")
def questions():
    return load_questions()


@pytest.fixture(scope="module")
def references():
    return json.loads(benchmark.REFERENCES_FILE.read_text(encoding="utf-8"))


def test_the_question_set_is_valid_and_covers_every_category(questions):
    assert len(questions) == 50
    counts = Counter(q.categorie for q in questions)
    assert set(counts) == set(benchmark.CATEGORIES)
    traps = sum(n for c, n in counts.items() if c.startswith("piege"))
    assert traps >= 10


def test_every_question_has_a_frozen_reference(questions, references):
    assert references["version_donnees"]
    assert set(references["questions"]) == {q.id for q in questions}
    for q in questions:
        values = references["questions"][q.id]["valeurs"]
        if q.statut_attendu == "repondu":
            expected = expected_values(q, values)
            assert expected or q.graphique, f"{q.id}: nothing to check in the answer"
            assert all(v is not None for v in expected.values()), q.id
        else:
            assert values == {}, q.id


def test_paths_follow_the_categories(questions):
    for q in questions:
        path = q.parcours_attendu
        if q.categorie == "sql_stats":
            assert "statistiques" in path and "visualisation" not in path, q.id
        if q.categorie == "sql_viz":
            assert "visualisation" in path and "statistiques" not in path, q.id
        if q.categorie == "equipe":
            assert {"sql", "statistiques", "visualisation"} <= set(path), q.id
        if q.categorie in ("piege_ambigu", "piege_impossible"):
            assert path == ["planificateur", "relecteur"], q.id


def make(**changes):
    base = dict(id="Q99", categorie="sql", question="?", statut_attendu="repondu",
                parcours_attendu=["planificateur", "sql", "relecteur"], sql_reference=["SELECT 1"])
    return Question(**{**base, **changes})


def test_check_catches_inconsistent_questions():
    assert check(make()) == []
    assert check(make(parcours_attendu=["sql", "relecteur"]))
    assert check(make(parcours_attendu=["planificateur", "inconnu", "relecteur"]))
    assert check(make(statut_attendu="impossible"))  # a refusal uses no SQL
    assert check(make(graphique={"type": "bar"}))  # a chart without the visualisation agent
    assert check(make(graphique={"type": "camembert"}, parcours_attendu=["planificateur", "sql", "visualisation",
                                                                         "relecteur"]))
    assert check(make(mentions=["(non fermée"]))


def test_invalid_file_lists_every_problem(tmp_path):
    path = tmp_path / "q.yaml"
    path.write_text("- {id: Q01, categorie: inconnue, question: '?', statut_attendu: repondu,\n"
                    "   parcours_attendu: [planificateur, relecteur]}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown category") as error:
        load_questions(path)
    assert "reference SQL" in str(error.value)


def test_expected_values_leave_out_yes_no_flags():
    q = make()
    assert expected_values(q, {"rho": 0.13, "significatif": True}) == {"rho": 0.13}
    assert expected_values(make(valeurs_attendues=["rho"]), {"rho": 0.1, "n": 5}) == {"rho": 0.1}


def test_default_reference_values():
    one_row = pd.DataFrame({"taux": [94.27], "n": [3]})
    assert build_references.default_values([one_row]) == {"taux": 94.27, "n": 3}
    two_columns = pd.DataFrame({"zone": ["dense", "rurale"], "debit": [243.9, 41.2]})
    assert build_references.default_values([two_columns]) == {"dense": 243.9, "rurale": 41.2}
    with pytest.raises(ValueError):
        build_references.default_values([pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})])
