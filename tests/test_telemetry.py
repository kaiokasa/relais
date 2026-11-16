"""OpenTelemetry spans of a run (GenAI semantic conventions), with a scripted fake LLM."""
import json

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from relais.telemetry import JsonLinesExporter, make_tracer, span_attributes, span_name
from tests.test_team import APPROVE, PLAN_SQL, RATE_QUERY, as_json, con, scripted, sql_call, team_with  # noqa: F401


def run_traced(con, exporter):
    team = team_with(con, planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Le taux est de 86,67 %.")),
                     sql=scripted(sql_call(RATE_QUERY), "Fini."), relecteur=scripted(APPROVE))
    return team.ask("Quel est le taux FttH ?", tracer=make_tracer(exporter),
                    run_attributes={"relais.question_id": "Q01"})


def test_a_run_is_one_trace_with_genai_spans(con):
    exporter = InMemorySpanExporter()
    answer = run_traced(con, exporter)
    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert len({s.context.trace_id for s in spans}) == 1
    root = next(s for s in spans if s.name == "invoke_agent equipe")
    assert root.parent is None and root.attributes["gen_ai.conversation.id"] == answer.run_id
    assert root.attributes["relais.question_id"] == "Q01" and root.attributes["relais.answer.status"] == "repondu"
    assert names.count("invoke_agent planificateur") == 2 and "invoke_agent sql" in names
    tool = next(s for s in spans if s.name == "execute_tool executer_sql")
    assert tool.attributes["gen_ai.operation.name"] == "execute_tool"
    assert tool.attributes["relais.table.id"] == "t1" and "SELECT" in tool.attributes["relais.sql.query"]
    chats = [s for s in spans if s.attributes["gen_ai.operation.name"] == "chat"]
    assert chats and all(s.attributes["gen_ai.usage.input_tokens"] >= 0 for s in chats)
    by_id = {s.context.span_id: s for s in spans}
    assert by_id[tool.parent.span_id].name == "invoke_agent sql"  # the tool call belongs to the SQL agent turn
    assert all(s.end_time >= s.start_time for s in spans)


def test_failed_tool_calls_are_errors(con):
    exporter = InMemorySpanExporter()
    team = team_with(con, planificateur=scripted(PLAN_SQL, as_json(statut="repondu", reponse="Le taux est de 86,67 %.")),
                     sql=scripted(sql_call("DROP TABLE communes", "1"), sql_call(RATE_QUERY, "2"), "Fini."),
                     relecteur=scripted(APPROVE))
    team.ask("Quel est le taux FttH ?", tracer=make_tracer(exporter))
    tools = [s for s in exporter.get_finished_spans() if s.name == "execute_tool executer_sql"]
    assert [s.status.status_code.name for s in tools] == ["ERROR", "OK"]
    assert "lecture" in tools[0].attributes["error.message"]


def test_listeners_follow_the_run_live(con):
    seen = []
    team = team_with(con, planificateur=scripted(as_json(statut="clarification", reponse="Fixe ou mobile ?")),
                     relecteur=scripted(APPROVE))
    team.ask("Quelle région est la mieux connectée ?", listeners=[lambda moment, e: seen.append((moment, e.agent))])
    assert seen[0] == ("debut", "planificateur") and seen[-1] == ("fin", "relecteur")


def test_json_lines_export(con, tmp_path):
    path = tmp_path / "traces.jsonl"
    run_traced(con, JsonLinesExporter(path))
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert {"trace_id", "span_id", "parent_span_id", "name", "attributes"} <= set(lines[0])
    assert any(line["name"] == "invoke_agent equipe" and line["parent_span_id"] is None for line in lines)


def test_span_naming_and_attributes():
    assert span_name("sql", "appel_llm", {"modele": "m"}) == "chat m"
    assert span_name("sql", "executer_sql", {}) == "execute_tool executer_sql"
    assert span_name("relecteur", "relire", {}) == "invoke_agent relecteur"
    attributes = span_attributes("relecteur", "relire", {"problemes": ["a", "b"], "autre": {"x": 1}})
    assert attributes["relais.review.problems"] == ["a", "b"] and attributes["relais.autre"] == '{"x": 1}'
