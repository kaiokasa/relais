"""The interpreter: called only for ambiguous terms, its readings are followed by the other agents (no LLM)."""
from langchain_core.messages import AIMessage

from relais import mining
from relais.benchmark import Question
from relais.team.interprete import ambiguous_terms, as_text, check_interpretation
from tests.test_team import (APPROVE, PLAN_SQL, RATE_QUERY, ScriptedChatModel, as_json, con, scripted,  # noqa: F401
                             sql_call, team_with)

READING = as_json(interpretations=[{"terme": "rurales", "choix": "classe Rural de la densité en 3 classes",
                                    "colonnes": "communes.densite = 3"}])
ANSWER = as_json(statut="repondu", reponse="Le taux est de 86,67 %.")


class RecordingModel(ScriptedChatModel):
    """A scripted model that keeps the messages it receives."""
    seen: list = []

    def _generate(self, messages, *args, **kwargs):
        RecordingModel.seen.append("\n".join(str(m.content) for m in messages))
        return super()._generate(messages, *args, **kwargs)


def test_ambiguous_terms_are_found_by_code():
    found = dict(ambiguous_terms("Les communes rurales sont-elles moins bien couvertes par l'opérateur historique ?"))
    assert "rurales" in found and "opérateur historique" in found and "couvertes" not in " ".join(found)
    assert ambiguous_terms("Quelle part des locaux est éligible au FttH au 2e trimestre 2026 ?") == []


def test_interpreter_runs_for_an_ambiguous_question_and_the_agents_follow_it(con):
    RecordingModel.seen = []
    sql_model = RecordingModel(messages=iter([sql_call(RATE_QUERY), AIMessage(content="Fini.")]))
    team = team_with(con, planificateur=scripted(PLAN_SQL, ANSWER), interprete=scripted(READING), sql=sql_model,
                     relecteur=scripted(APPROVE))
    answer = team.ask("Quel est le taux FttH dans les communes rurales ?")
    assert answer.path == ["planificateur", "interprete", "sql", "planificateur", "relecteur"]
    assert any("Interprétations retenues" in text and "communes.densite = 3" in text for text in RecordingModel.seen)
    event = next(e for e in answer.events if e.agent == "interprete" and e.parent is None)
    assert event.attributes["termes"] == ["rurales"] and "Rural" in event.attributes["interpretations"][0]


def test_no_interpreter_for_a_clear_question_or_a_clarification(con):
    team = team_with(con, planificateur=scripted(PLAN_SQL, ANSWER), sql=scripted(sql_call(RATE_QUERY), "Fini."),
                     relecteur=scripted(APPROVE))
    assert "interprete" not in team.ask("Quel est le taux FttH ?").path
    team = team_with(con, planificateur=scripted(as_json(statut="clarification", reponse="Fixe ou mobile ?")),
                     relecteur=scripted(APPROVE))
    assert team.ask("Les zones rurales sont-elles mieux connectées ?").path == ["planificateur", "relecteur"]


def test_a_failed_interpretation_does_not_stop_the_team(con):
    team = team_with(con, planificateur=scripted(PLAN_SQL, ANSWER), interprete=scripted("rien", "toujours rien"),
                     sql=scripted(sql_call(RATE_QUERY), "Fini."), relecteur=scripted(APPROVE))
    answer = team.ask("Le taux FttH a-t-il progressé récemment ?")
    assert answer.statut == "repondu" and answer.path[1] == "interprete"
    assert not next(e for e in answer.events if e.agent == "interprete").ok


def test_the_reference_model_accepts_the_optional_interpreter():
    q = Question(id="Q01", categorie="sql", question="?", statut_attendu="repondu",
                 parcours_attendu=["planificateur", "sql", "planificateur", "relecteur"], sql_reference=["SELECT 1"])
    edges = set(mining.directly_follows(mining.reference_frame([q])))
    assert ("planificateur", "interprete") in edges and ("interprete", "sql") in edges
    assert ("planificateur", "sql") in edges


def test_interpretation_format():
    readings = check_interpretation({"interpretations": [{"terme": "rural", "choix": "densité 3"}]})
    assert as_text(readings) == "- « rural » : densité 3"
