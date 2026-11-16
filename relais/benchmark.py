"""The question set (benchmark/questions.yaml): loading and validation.

Each question carries its reference answer (computed by benchmark/build_references.py from the reference SQL) and
its expected path through the team, the reference model of the conformance checking.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_FILE = ROOT / "benchmark" / "questions.yaml"
REFERENCES_FILE = ROOT / "benchmark" / "references.json"

AGENTS = ("planificateur", "sql", "statistiques", "visualisation", "relecteur")
WORKERS = ("sql", "statistiques", "visualisation")
STATUSES = ("repondu", "clarification", "impossible")
CATEGORIES = {
    "sql": "SQL seul",
    "sql_stats": "SQL + statistiques",
    "sql_viz": "SQL + visualisation",
    "equipe": "Équipe complète",
    "multi_etapes": "Plusieurs étapes",
    "piege_ambigu": "Piège : question ambiguë",
    "piege_impossible": "Piège : réponse absente des données",
    "piege_causal": "Piège : question causale déguisée",
    "piege_operateurs": "Piège : comparaison d'opérateurs",
}
CHART_TYPES = ("line", "bar", "scatter", "box", "histogram")
DEFAULT_TOLERANCE_ABS = 0.06  # values are usually given with one or two decimals
DEFAULT_TOLERANCE_REL = 0.005


@dataclass
class Question:
    id: str
    categorie: str
    question: str
    statut_attendu: str
    parcours_attendu: list
    sql_reference: list = field(default_factory=list)
    calcul_reference: str = ""
    valeurs_attendues: list | None = None
    valeurs_au_choix: list = field(default_factory=list)
    mentions: list = field(default_factory=list)
    interdits: list = field(default_factory=list)
    graphique: dict | None = None
    tolerance_abs: float = DEFAULT_TOLERANCE_ABS
    tolerance_rel: float = DEFAULT_TOLERANCE_REL
    piege: str = ""


def check(question):
    """List of the problems of a question (empty when it is valid)."""
    q, problems = question, []
    if not re.fullmatch(r"[A-Z]\d{2}", q.id):
        problems.append("id must look like Q01")
    if q.categorie not in CATEGORIES:
        problems.append(f"unknown category {q.categorie}")
    if q.statut_attendu not in STATUSES:
        problems.append(f"unknown status {q.statut_attendu}")
    path = q.parcours_attendu
    if any(step not in AGENTS for step in path):
        problems.append(f"unknown agent in {path}")
    workers = [step for step in path if step in WORKERS]
    # the planner plans, the workers run the steps, the planner writes the answer, the reviewer checks it
    expected_shape = (["planificateur", *workers, "planificateur", "relecteur"] if workers
                      else ["planificateur", "relecteur"])
    if path != expected_shape:
        problems.append(f"the path must be {expected_shape}")
    if q.statut_attendu == "repondu" and not q.sql_reference:
        problems.append("an answered question needs a reference SQL")
    if q.statut_attendu != "repondu" and (q.sql_reference or "sql" in path):
        problems.append("a clarification or a refusal uses no SQL")
    if q.graphique is not None:
        if q.graphique.get("type") not in CHART_TYPES:
            problems.append(f"unknown chart type {q.graphique}")
        if "visualisation" not in path:
            problems.append("a chart needs the visualisation agent in the path")
    elif "visualisation" in path:
        problems.append("the visualisation agent is in the path but no chart is expected")
    if q.calcul_reference and "statistiques" not in path and not q.sql_reference:
        problems.append("a reference computation needs a reference SQL")
    for pattern in q.mentions + q.interdits:
        try:
            re.compile(pattern)
        except re.error as error:
            problems.append(f"bad regular expression {pattern!r}: {error}")
    return problems


def load_questions(path=QUESTIONS_FILE):
    """The questions, validated (ValueError listing every problem otherwise)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    questions, problems = [], []
    for item in raw:
        sql = item.get("sql_reference") or []
        item["sql_reference"] = [sql] if isinstance(sql, str) else list(sql)
        try:
            question = Question(**item)
        except TypeError as error:
            problems.append(f"{item.get('id')}: {error}")
            continue
        problems += [f"{question.id}: {p}" for p in check(question)]
        questions.append(question)
    ids = [q.id for q in questions]
    problems += [f"{i}: duplicated id" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if problems:
        raise ValueError("Invalid question set:\n  " + "\n  ".join(problems))
    return questions


def expected_values(question, values):
    """The reference values the answer must give: the listed ones, else every value that is not a yes/no flag."""
    if question.valeurs_attendues is not None:
        return {k: values[k] for k in question.valeurs_attendues}
    return {k: v for k, v in values.items() if not isinstance(v, bool)}
