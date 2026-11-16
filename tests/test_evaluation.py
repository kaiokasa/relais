"""Scoring of an answer against its question (no LLM)."""
from relais.benchmark import Question
from relais.evaluation import evaluate, plain_text

PATH = ["planificateur", "sql", "planificateur", "relecteur"]


def question(**changes):
    base = dict(id="Q99", categorie="sql", question="?", statut_attendu="repondu", parcours_attendu=PATH,
                sql_reference=["SELECT 1"])
    return Question(**{**base, **changes})


def test_numbers_rates_and_names_are_found_with_tolerance():
    q = question()
    refs = {"taux_ftth_pct": 94.27, "taux_web": 0.8216, "departement": "Cantal", "locaux": 1644}
    text = "Le taux est de 94,3 %, l'accès web réussit dans 82,2 % des cas, le Cantal est dernier ; 1 644 locaux."
    result = evaluate(q, "repondu", text, None, PATH, refs)
    assert result["juste"] and result["parcours_conforme"]
    result = evaluate(q, "repondu", "Le taux est de 93 % dans le cantal, 82,2 %, 1 644 locaux.", None, PATH, refs)
    assert result["valeurs_manquantes"] == ["taux_ftth_pct"] and not result["juste"]


def test_sign_written_in_words_is_accepted():
    q = question(tolerance_abs=0.01)
    assert evaluate(q, "repondu", "Une corrélation négative de 0,96.", None, PATH, {"rho": -0.964})["juste"]


def test_status_mentions_forbidden_phrases_and_chart():
    q = question(statut_attendu="impossible", parcours_attendu=["planificateur", "relecteur"], sql_reference=[],
                 mentions=["prix|tarif"], interdits=["meilleur opérateur"])
    ok = evaluate(q, "impossible", "Aucun prix dans les données.", None, ["planificateur", "relecteur"], {})
    assert ok["juste"] and ok["parcours_conforme"]
    bad = evaluate(q, "repondu", "Le meilleur opérateur est X.", None, PATH, {})
    assert not bad["statut_ok"] and bad["mentions_manquantes"] == ["prix|tarif"]
    assert bad["interdits_trouves"] == ["meilleur opérateur"] and not bad["parcours_conforme"]
    chart_q = question(graphique={"type": "line", "series": 4}, parcours_attendu=PATH)
    assert not evaluate(chart_q, "repondu", "", None, PATH, {})["graphique_ok"]
    assert not evaluate(chart_q, "repondu", "", {"type": "line", "series": 3}, PATH, {})["graphique_ok"]
    assert evaluate(chart_q, "repondu", "", {"type": "line", "series": 4}, PATH, {})["graphique_ok"]


def test_mentions_ignore_accents_and_case():
    assert plain_text("Éligibilité") == "eligibilite"
    q = question(mentions=["éligib"])
    assert evaluate(q, "repondu", "Les locaux ELIGIBLES", None, PATH, {})["juste"]


def test_one_group_of_values_is_enough():
    q = question(valeurs_attendues=["moyenne"], valeurs_au_choix=[["bas", "haut"], ["demi"]])
    refs = {"moyenne": 89.58, "bas": 87.32, "haut": 91.85, "demi": 2.27}
    assert evaluate(q, "repondu", "89,58 Mbit/s, de 87,32 à 91,85.", None, PATH, refs)["juste"]
    assert evaluate(q, "repondu", "89,58 Mbit/s plus ou moins 2,27.", None, PATH, refs)["juste"]
    result = evaluate(q, "repondu", "89,58 Mbit/s, au moins 87,32.", None, PATH, refs)
    assert result["valeurs_manquantes"] == ["bas+haut ou demi"]


def test_counts_must_be_exact():
    q = question()
    assert not evaluate(q, "repondu", "29 526 communes", None, PATH, {"communes": 29512})["juste"]
    assert evaluate(q, "repondu", "29 512 communes", None, PATH, {"communes": 29512})["juste"]
    assert evaluate(q, "repondu", "1,2 million de locaux", None, PATH, {"locaux": 1204312})["juste"]
    assert evaluate(q, "repondu", "56 points", None, PATH, {"progression": 56.0})["juste"]
    assert evaluate(q, "repondu", "56,02 %", None, PATH, {"part": 56.0})["juste"]  # a round rate is not a count
