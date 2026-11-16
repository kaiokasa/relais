"""The team, with a scripted fake LLM per role (no network, no API key) on a tiny database."""
import json

import duckdb
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from relais.team.graph import MAX_REVIEWS, MAX_TOOL_CALLS, Team, check_plan, parse_json
from relais.tools.sql import open_locked_copy


class ScriptedChatModel(GenericFakeChatModel):
    """Answers with the scripted messages, in order; tools are accepted but the script decides the calls."""

    def bind_tools(self, tools, **kwargs):
        return self


def scripted(*replies):
    messages = [r if isinstance(r, AIMessage) else
                AIMessage(content=r, usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})
                for r in replies]
    return ScriptedChatModel(messages=iter(messages))


def as_json(**content):
    return json.dumps(content, ensure_ascii=False)


def sql_call(query, call_id="1"):
    return AIMessage(content="", tool_calls=[{"name": "executer_sql", "args": {"requete": query}, "id": call_id}])


APPROVE = as_json(verdict="approuve")
PLAN_SQL = as_json(statut="a_traiter", etapes=[{"agent": "sql", "consigne": "Taux FttH en 2026_T2"}])
RATE_QUERY = ("SELECT round(100.0 * sum(locaux_eligibles_ftth) / sum(nb_locaux), 2) AS taux_ftth_pct "
              "FROM fixe_technologies WHERE trimestre = '2026_T2'")


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "mini.duckdb"
    db = duckdb.connect(str(path))
    db.execute("CREATE TABLE fixe_technologies AS SELECT * FROM (VALUES ('2026_T2', '01053', 100, 90), "
               "('2026_T2', '2A004', 200, 150), ('2026_T2', '75056', 300, 280)) "
               "t(trimestre, code_insee, nb_locaux, locaux_eligibles_ftth)")
    db.execute("CREATE TABLE communes AS SELECT * FROM (VALUES ('01053', 'Bourg', 41000, 25000), "
               "('2A004', 'Ajaccio', 72000, 23000), ('75056', 'Paris', 2100000, 30000)) "
               "t(code_insee, nom, population, niveau_vie_median)")
    db.close()
    return open_locked_copy(path)


def team_with(con, **roles):
    """A team whose roles not given would fail the test if they were called."""
    unused = scripted()  # an empty script: calling it raises
    return Team({role: roles.get(role, unused) for role in
                 ("planificateur", "interprete", "sql", "statistiques", "visualisation", "relecteur")}, con)


def test_sql_question_goes_planner_sql_planner_reviewer(con):
    team = team_with(
        con,
        planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Au 2e trimestre 2026, 86,67 % des "
                                                                             "locaux sont éligibles au FttH.")),
        sql=scripted(sql_call(RATE_QUERY), "Le tableau t1 contient le taux."),
        relecteur=scripted(APPROVE))
    answer = team.ask("Quelle part des locaux est éligible au FttH ?")
    assert answer.statut == "repondu" and answer.approuve
    assert answer.path == ["planificateur", "sql", "planificateur", "relecteur"]
    assert answer.tables["t1"].data.taux_ftth_pct.iat[0] == 86.67
    sql_events = [e for e in answer.events if e.action == "executer_sql"]
    assert sql_events[0].ok and sql_events[0].attributes["tableau"] == "t1"
    assert all(e.end is not None for e in answer.events)
    assert answer.tokens == (400, 40)  # 5 LLM calls; the scripted tool call declares no tokens
    assert answer.cout_usd is None  # the fake model has no price


def test_invented_number_is_rejected_without_asking_the_llm(con):
    team = team_with(
        con,
        planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Le taux est de 91,5 %."),
                               as_json(statut="repondu", reponse="Le taux est de 86,67 %.")),
        sql=scripted(sql_call(RATE_QUERY), "Le tableau t1 contient le taux."),
        relecteur=scripted(APPROVE))  # called once: the first draft is refused by the code
    answer = team.ask("Quel est le taux FttH ?")
    assert answer.approuve and "86,67" in answer.reponse
    assert answer.path == ["planificateur", "sql", "planificateur", "relecteur", "planificateur", "relecteur"]
    first_review = next(e for e in answer.events if e.action == "relire")
    assert first_review.attributes["verdict"] == "a_corriger"
    assert "91,5" in first_review.attributes["problemes"][0]


def test_ambiguous_question_gets_a_clarification(con):
    team = team_with(con, planificateur=scripted(as_json(statut="clarification", reponse="Fixe ou mobile ?")),
                     relecteur=scripted(APPROVE))
    answer = team.ask("Quelle région est la mieux connectée ?")
    assert answer.statut == "clarification" and answer.path == ["planificateur", "relecteur"]
    assert not answer.tables


def test_forbidden_query_is_refused_and_the_agent_can_fix_it(con):
    team = team_with(
        con,
        planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Le taux est de 86,67 %.")),
        sql=scripted(sql_call("DROP TABLE fixe_technologies", "1"), sql_call(RATE_QUERY, "2"), "Fini."),
        relecteur=scripted(APPROVE))
    answer = team.ask("Quel est le taux FttH ?")
    calls = [e for e in answer.events if e.action == "executer_sql"]
    assert [c.ok for c in calls] == [False, True]
    assert "lecture" in calls[0].attributes["erreur"]
    assert answer.approuve


def test_the_reviewer_gives_up_after_the_maximum_of_reviews(con):
    refuse = as_json(verdict="a_corriger", problemes=["Granularité : moyenne des taux communaux."])
    team = team_with(
        con,
        planificateur=scripted(PLAN_SQL, *[as_json(statut="repondu", reponse="Le taux est de 86,67 %.")] * 3),
        sql=scripted(sql_call(RATE_QUERY), "Fini."),
        relecteur=scripted(*[refuse] * 3))
    answer = team.ask("Quel est le taux FttH ?")
    assert not answer.approuve and answer.problemes == ["Granularité : moyenne des taux communaux."]
    assert answer.path.count("relecteur") == MAX_REVIEWS


def test_unreadable_planner_answers_end_in_a_failure(con):
    team = team_with(con, planificateur=scripted("pas du JSON", "toujours pas"), relecteur=scripted())
    answer = team.ask("Question ?")
    assert answer.statut == "echec" and not answer.approuve


def test_full_team_sql_statistics_chart(con):
    detail = "SELECT c.niveau_vie_median, 100.0 * f.locaux_eligibles_ftth / f.nb_locaux AS taux_ftth_pct " \
             "FROM fixe_technologies f JOIN communes c USING (code_insee)"
    plan = as_json(statut="a_traiter", etapes=[{"agent": "sql", "consigne": "Données par commune"},
                                               {"agent": "statistiques", "consigne": "Spearman"},
                                               {"agent": "visualisation", "consigne": "Nuage de points"}])
    python = AIMessage(content="", tool_calls=[{"name": "executer_python", "id": "p1", "args": {
        "code": "r = stats.spearmanr(t1.niveau_vie_median, t1.taux_ftth_pct)\nresultat = {'rho': round(r.statistic, 3)}"}}])
    chart = AIMessage(content="", tool_calls=[{"name": "creer_graphique", "id": "g1", "args": {
        "type": "scatter", "tableau": "t1", "x": "niveau_vie_median", "y": "taux_ftth_pct", "titre": "Relation"}}])
    team = team_with(
        con,
        planificateur=scripted(plan, as_json(statut="repondu", reponse="La corrélation de Spearman vaut 1 "
                                                                       "sur 3 communes ; elle ne prouve pas de cause.")),
        sql=scripted(sql_call(detail), "t1 contient les données."),
        statistiques=scripted(python, "rho = 1"),
        visualisation=scripted(chart, "Nuage de points créé."),
        relecteur=scripted(APPROVE))
    answer = team.ask("Le revenu est-il lié à la fibre ? Montre la relation.")
    assert answer.path == ["planificateur", "sql", "statistiques", "visualisation", "planificateur", "relecteur"]
    assert answer.statistiques[0]["resultat"] == {"rho": 1.0}
    assert answer.graphique.type == "scatter" and answer.approuve


def test_a_requested_chart_that_was_not_drawn_is_refused(con):
    team = team_with(
        con,
        planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Le taux est de 86,67 %."),
                               as_json(statut="repondu", reponse="Le taux est de 86,67 %.")),
        sql=scripted(sql_call(RATE_QUERY), "Fini."),
        relecteur=scripted())
    answer = team.ask("Montre le taux FttH dans un graphique.")
    assert not answer.approuve and "graphique" in answer.problemes[0]


def test_a_worker_cannot_call_its_tool_forever(con):
    endless = [sql_call(f"SELECT {i} AS n FROM communes", str(i)) for i in range(MAX_TOOL_CALLS["sql"] + 5)]
    team = team_with(
        con,
        planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Rien de concluant.")),
        sql=scripted(*endless),
        relecteur=scripted(APPROVE))
    answer = team.ask("Question ?")
    assert len([e for e in answer.events if e.action == "executer_sql"]) == MAX_TOOL_CALLS["sql"]
    assert answer.statut == "repondu"


def test_parse_json_and_check_plan():
    assert parse_json('```json\n{"statut": "repondu", "reponse": "ok"}\n```')["statut"] == "repondu"
    assert parse_json('Voici : {"a": 1} fin')["a"] == 1
    with pytest.raises(ValueError):
        parse_json("rien")
    with pytest.raises(ValueError, match="agents possibles"):
        check_plan({"statut": "a_traiter", "etapes": [{"agent": "pirate", "consigne": "x"}]})
    with pytest.raises(ValueError):
        check_plan({"statut": "a_traiter", "etapes": []})
    with pytest.raises(ValueError):
        check_plan({"statut": "clarification", "reponse": " "})
    assert check_plan({"statut": "impossible", "reponse": "Pas de prix."})["statut"] == "impossible"


def test_every_role_needs_a_model(con):
    with pytest.raises(ValueError, match="no model"):
        Team({"planificateur": scripted()}, con)
    single = scripted()
    assert all(m is single for m in Team(single, con).llms.values())



def test_more_steps_beyond_the_limit_end_cleanly(con, monkeypatch):
    from relais.team import graph
    monkeypatch.setattr(graph, "MAX_STEPS", 1)
    more = as_json(statut="a_traiter", etapes=[{"agent": "sql", "consigne": "Encore"}])
    team = team_with(con, planificateur=scripted(PLAN_SQL, more, more), sql=scripted(sql_call(RATE_QUERY), "Fini."),
                     relecteur=scripted())
    answer = team.ask("Question ?")
    assert answer.statut == "echec" and not answer.approuve
