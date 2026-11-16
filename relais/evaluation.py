"""Scoring an answer of the team against its question of the benchmark (no LLM judge: every check is code).

An answer is right ("juste") when its status is the expected one (answered, clarification, impossible), every
expected value appears in the text (a number within the tolerance, a rate in 0-1 or in %, a name as text), every
required mention is there, no forbidden phrase is, and the expected chart was drawn.
The path through the team is compared with the expected path, but kept apart from the correctness.
"""
import re
import unicodedata

from relais.benchmark import expected_values
from relais.team.numbers import numbers_in_text


def plain_text(text):
    """Lower case, without accents: 'Éligibilité' -> 'eligibilite'."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def matches(pattern, text):
    return bool(re.search(pattern, text, re.IGNORECASE) or re.search(plain_text(pattern), plain_text(text)))


def number_found(reference, written, question):
    """True when a number of the answer is the reference value. Also accepted: the rate in the other scale (0.82 or
    82 %), and the absolute value, since the sign is often written in words ("une corrélation négative de 0,96").
    A count (a reference stored as an integer, such as a number of communes) must be exact, up to the rounding of
    the written form ("1,2 million" is accepted for 1 204 312); a rate that happens to be round (56.0) is not a
    count."""
    exact = isinstance(reference, int) and not isinstance(reference, bool) and abs(reference) >= 20
    reference = float(reference)
    for _, value, written_tolerance in written:
        for candidate in (reference, reference * 100, reference / 100):
            tolerance = (written_tolerance if exact else
                         max(question.tolerance_abs, question.tolerance_rel * abs(candidate), written_tolerance))
            if abs(abs(value) - abs(candidate)) <= tolerance:
                return True
    return False


def evaluate(question, statut, reponse, graphique, path, reference_values):
    """The checks of one answer, as a dictionary (all the details are kept for the report)."""
    written = numbers_in_text(reponse)

    def found(value):
        if isinstance(value, str):
            return plain_text(value) in plain_text(reponse)
        return number_found(value, written, question)

    missing = [name for name, value in expected_values(question, reference_values).items() if not found(value)]
    groups = question.valeurs_au_choix
    if groups and not any(all(found(reference_values[k]) for k in group) for group in groups):
        missing.append(" ou ".join("+".join(group) for group in groups))
    missing_mentions = [p for p in question.mentions if not matches(p, reponse)]
    forbidden = [p for p in question.interdits if matches(p, reponse)]
    chart_ok = True
    if question.graphique:
        chart_ok = graphique is not None and graphique.get("type") == question.graphique["type"]
        if chart_ok and "series" in question.graphique:
            chart_ok = graphique.get("series") == question.graphique["series"]
    statut_ok = statut == question.statut_attendu
    return {
        "statut_ok": statut_ok,
        "valeurs_manquantes": missing,
        "mentions_manquantes": missing_mentions,
        "interdits_trouves": forbidden,
        "graphique_ok": chart_ok,
        "juste": statut_ok and not missing and not missing_mentions and not forbidden and chart_ok,
        "parcours_conforme": list(path) == list(question.parcours_attendu),
    }
