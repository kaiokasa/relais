"""Traces -> event logs -> process mining, the live monitor and the failure prediction (no LLM, no network)."""
import json

import pandas as pd
import pytest

from relais import eventlog, mining, prediction
from relais.benchmark import Question
from relais.monitor import LiveMonitor
from relais.team.trace import Event
from relais.telemetry import JsonLinesExporter, make_tracer
from tests.test_team import APPROVE, PLAN_SQL, RATE_QUERY, as_json, con, scripted, sql_call, team_with  # noqa: F401

PATH_SQL = ["planificateur", "sql", "planificateur", "relecteur"]


@pytest.fixture(scope="module")
def traces(con, tmp_path_factory):
    """Three traced runs: one as expected, one with a refused draft (reviewer loop), one clarification."""
    path = tmp_path_factory.mktemp("traces") / "traces.jsonl"
    tracer = make_tracer(JsonLinesExporter(path))
    good = as_json(statut="repondu", reponse="Le taux est de 86,67 %.")
    runs = [
        (dict(planificateur=scripted(PLAN_SQL, good), sql=scripted(sql_call(RATE_QUERY), "Fini."),
              relecteur=scripted(APPROVE)), "Q01"),
        (dict(planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Le taux est de 91 %."), good),
              sql=scripted(sql_call(RATE_QUERY), "Fini."), relecteur=scripted(APPROVE)), "Q01"),
        (dict(planificateur=scripted(as_json(statut="clarification", reponse="Fixe ou mobile ?")),
              relecteur=scripted(APPROVE)), "P01"),
    ]
    answers = []
    for roles, qid in runs:
        answers.append(team_with(con, **roles).ask("Question ?", tracer=tracer, run_attributes={
            "relais.question_id": qid, "relais.categorie": "sql" if qid == "Q01" else "piege_ambigu"}))
    scores = {a.run_id: {"juste": i != 1} for i, a in enumerate(answers)}
    spans = eventlog.load_spans(path)
    return spans, eventlog.runs_table(spans, scores), answers


def test_event_logs_at_two_levels(traces, tmp_path):
    spans, runs, answers = traces
    assert len(runs) == 3 and set(runs.question_id) == {"Q01", "P01"}
    agents = eventlog.xes_frame(spans, runs, "agents")
    seqs = mining.sequences(agents)
    assert sorted(len(s) for s in seqs.values()) == [2, 4, 6]
    assert list(seqs[runs[runs.run_id == answers[0].run_id].trace_id.iat[0]]) == PATH_SQL
    detail = eventlog.xes_frame(spans, runs, "detail")
    assert "sql:executer_sql" in set(detail["concept:name"]) and len(detail) > len(agents)
    eventlog.write_xes(agents, tmp_path / "agents.xes")
    assert (tmp_path / "agents.xes").stat().st_size > 0


def test_ocel_relates_events_to_their_objects(traces, tmp_path):
    spans, runs, _ = traces
    events, objects, relations, o2o = eventlog.ocel_tables(spans, runs)
    kinds = set(objects["ocel:type"])
    assert {"question", "run", "agent", "requete", "tableau"} <= kinds
    sql_event = events[events["ocel:activity"] == "sql:executer_sql"]["ocel:eid"].iat[0]
    qualifiers = set(relations[relations["ocel:eid"] == sql_event]["ocel:qualifier"])
    assert {"execute", "produit", "par", "pour", "dans"} <= qualifiers
    assert "produit" in set(o2o["ocel:qualifier"])
    ocel = eventlog.make_ocel(spans, runs)
    eventlog.write_ocel(ocel, tmp_path / "log.jsonocel")
    report = eventlog.conversion_report(spans, runs, ocel)
    assert report["runs"] == 3 and report["profondeur_max"] >= 2 and report["variantes_agents"] == 3


def test_conformance_variants_and_handover(traces):
    spans, runs, _ = traces
    agents = eventlog.xes_frame(spans, runs, "agents")
    questions = [Question(id="Q01", categorie="sql", question="?", statut_attendu="repondu",
                          parcours_attendu=PATH_SQL, sql_reference=["SELECT 1"]),
                 Question(id="P01", categorie="piege_ambigu", question="?", statut_attendu="clarification",
                          parcours_attendu=["planificateur", "relecteur"])]
    net = mining.discover_net(mining.reference_frame(questions))
    fitness = mining.alignment_fitness(agents, net)
    assert sorted(round(f, 2) for f in fitness.values())[-1] == 1.0 and min(fitness.values()) < 1.0
    assert len(mining.variants(agents)) == 3
    handover = mining.handover(agents)
    assert ("relecteur", "planificateur") in set(zip(handover.de, handover.vers))
    figure = mining.graph_figure(mining.directly_follows(agents), "test",
                                 reference=set(mining.directly_follows(mining.reference_frame(questions))))
    assert figure.data


def test_sequence_measures():
    assert mining.edit_distance(("a", "b", "c"), ("a", "c")) == 1
    assert mining.edit_distance((), ("a",)) == 1
    assert mining.entropy(["x", "x", "x"]) == 0.0 and mining.entropy(["x", "y"]) == 1.0
    assert mining.mean_pairwise_distance([("a",), ("a",), ("b",)]) == pytest.approx(2 / 3)


def event(i, parent, agent, action, start, end, ok=True, **attributes):
    return Event(i, parent, agent, action, start, end, ok, attributes)


def test_live_monitor_alerts_on_deviations_failures_and_rejections():
    monitor = LiveMonitor({"modele": "m"}, reference={("début", "planificateur"), ("planificateur", "sql"),
                                                      ("sql", "planificateur"), ("planificateur", "relecteur"),
                                                      ("relecteur", "fin")})
    plan = event(1, None, "planificateur", "planifier", 0, 1)
    turn = event(2, None, "sql", "etape", 1, 3)
    failed = event(3, 2, "sql", "executer_sql", 1.5, 2, ok=False, erreur="Table inconnue")
    review = event(4, None, "relecteur", "relire", 3, 4, verdict="a_corriger", problemes=["Mauvaise période."])
    for moment, e in [("debut", plan), ("fin", plan), ("debut", turn), ("debut", failed), ("fin", failed),
                      ("fin", turn), ("debut", review), ("fin", review)]:
        monitor(moment, e)
    monitor.finish()
    snap = monitor.snapshot()
    messages = " ".join(a.message for a in snap["alerts"])
    assert snap["path"] == ["planificateur", "sql", "relecteur"]
    assert "sql → relecteur" in messages and "Table inconnue" in messages and "Mauvaise période" in messages
    assert snap["edges"][("sql", "relecteur")] == 1 and snap["finished"]


def test_prefix_features_use_only_finished_events():
    events = [{"id": 1, "parent": None, "agent": "planificateur", "action": "planifier", "start": 0, "end": 1,
               "ok": True, "attributes": {}},
              {"id": 2, "parent": 1, "agent": "planificateur", "action": "appel_llm", "start": 0, "end": 0.9,
               "ok": True, "attributes": {"input_tokens": 2000, "output_tokens": 100}},
              {"id": 3, "parent": None, "agent": "relecteur", "action": "relire", "start": 1, "end": None,
               "ok": True, "attributes": {"verdict": "a_corriger"}}]
    prefix = prediction.finished_in_order(events)
    assert [e["id"] for e in prefix] == [2, 1]
    f = prediction.features(prefix, {"modele": "gemini-3.5-flash-lite", "temperature": 1.0, "prompt": "concis"})
    assert f["modele=gemini-3.5-flash-lite"] == 1.0 and f["prompt_concis"] == 1.0 and f["tokens_entree_k"] == 2.0
    assert f["refus_relecteur"] == 0.0  # the review is not finished yet
    assert set(f) == set(prediction.FEATURES)


def test_prefix_dataset_and_grouped_validation():
    def record(qid, wrong, rejections):
        events = [{"id": i, "parent": None, "agent": "relecteur", "action": "relire", "start": i, "end": i + 0.5,
                   "ok": True, "attributes": {"verdict": "a_corriger" if i < rejections else "approuve"}}
                  for i in range(3)]
        return {"question_id": qid, "config": {"modele": "gemini-3.1-flash-lite"}, "events": events,
                "evaluation": {"juste": not wrong}, "run_id": f"{qid}{wrong}{rejections}"}
    records = [record(f"Q{i}", wrong=i % 2 == 0, rejections=2 if i % 2 == 0 else 0) for i in range(10)]
    data = prediction.prefix_dataset(records, lengths=(1, None))
    assert len(data) == 20 and set(data.longueur) == {1, "complet"}
    scores, _ = prediction.grouped_scores(data, folds=5)
    assert scores.auc.max() > 0.9  # rejections by the reviewer separate the wrong answers


def test_dashboard_starts_without_data_or_key(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = AppTest.from_file("app/dashboard.py", default_timeout=60).run()
    assert not app.exception
    assert any("RELAIS" in m.value for m in app.sidebar.markdown)
    assert any("Observer une équipe d'agents" in m.value for m in app.markdown)


def test_console_figures():
    from relais.console import risk_curve, risk_gauge, waterfall_figure
    plan = Event(1, None, "planificateur", "planifier", 0.0, 1.0)
    call = Event(2, 1, "planificateur", "appel_llm", 0.1, 0.9)
    failed = Event(3, None, "sql", "executer_sql", 1.0, 1.5, ok=False, attributes={"erreur": "Table inconnue"})
    running = Event(4, None, "relecteur", "relire", 1.6, None)
    figure = waterfall_figure([plan, call, failed], [running], started_at=0.0, now=2.0)
    bar = figure.data[0]
    assert list(bar.base) == [0.0, 0.1, 1.0, 1.6] and bar.x[3] == pytest.approx(0.4)
    assert bar.marker.color[2] == "#e66767" and bar.text[2].startswith("✗")
    names = [a.text for a in figure.layout.annotations]
    assert names[0] == "<b>planificateur</b>" and "appel au modèle" in names[1]
    assert waterfall_figure([], []).data == ()
    assert risk_gauge(0.72).data[0].value == 72 and risk_gauge(None).data[0].value is None
    assert len(risk_curve([(1, 0.2), (2, 0.1)]).data[0].x) == 2
