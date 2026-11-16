"""OpenTelemetry tracing of the team, with the GenAI semantic conventions (gen_ai.*).

Every event of the recorder (relais/team/trace.py) is also an OpenTelemetry span:

- the run:              "invoke_agent equipe"      gen_ai.operation.name = invoke_agent, gen_ai.conversation.id
- a turn of an agent:   "invoke_agent <agent>"     gen_ai.agent.name, relais.action (planifier, rediger, etape...)
- an LLM call:          "chat <model>"             gen_ai.provider.name, gen_ai.request.model, gen_ai.usage.*
- a tool call:          "execute_tool <tool>"      gen_ai.tool.name, relais.sql.query, relais.table.id, ...

The conventions cover agents, models, tokens and tools; what they do not cover (the SQL text, the table produced,
the verdict of the reviewer...) goes into relais.* attributes. Spans are written as JSON lines (one span per line),
the input of the event log conversion (relais/eventlog.py).
"""
import json
import threading
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

SERVICE = "relais"
TOOLS = {"executer_sql", "executer_python", "creer_graphique"}
# relais attributes of the recorder -> attribute names on the span
ATTRIBUTE_NAMES = {
    "modele": "gen_ai.request.model", "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens", "requete": "relais.sql.query", "tableau": "relais.table.id",
    "lignes": "relais.table.rows", "duree_sql_ms": "relais.sql.duration_ms", "code": "relais.python.code",
    "duree_python_ms": "relais.python.duration_ms", "timeout": "relais.python.timeout",
    "specification": "relais.chart.specification", "type": "relais.chart.type", "series": "relais.chart.series",
    "points": "relais.chart.points", "consigne": "relais.step.instruction", "statut": "relais.answer.status",
    "verdict": "relais.review.verdict", "problemes": "relais.review.problems",
    "controles_automatiques": "relais.review.automatic_problems", "erreur": "error.message",
}


def span_name(agent, action, attributes):
    if action == "appel_llm":
        return f"chat {attributes.get('modele', 'llm')}"
    if action in TOOLS:
        return f"execute_tool {action}"
    return f"invoke_agent {agent}"


def span_attributes(agent, action, attributes):
    """The attributes of a span: GenAI conventions first, relais.* for the rest (primitive values only)."""
    if action == "appel_llm":
        base = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": "gcp.gemini"}
    elif action in TOOLS:
        base = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": action, "gen_ai.tool.type": "function"}
    else:
        base = {"gen_ai.operation.name": "invoke_agent"}
    base.update({"gen_ai.agent.name": agent, "relais.action": action})
    for key, value in attributes.items():
        name = ATTRIBUTE_NAMES.get(key, f"relais.{key}")
        base[name] = clean(value)
    return {k: v for k, v in base.items() if v is not None}


def clean(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return list(value)
    return json.dumps(value, ensure_ascii=False, default=str)


class JsonLinesExporter(SpanExporter):
    """Writes every finished span as one JSON line (trace and span ids in hex, times in nanoseconds)."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def export(self, spans):
        lines = [json.dumps(span_record(span), ensure_ascii=False) for span in spans]
        with self._lock, open(self.path, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def span_record(span):
    context = span.get_span_context()
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
        "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
        "name": span.name,
        "start_time_unix_nano": span.start_time,
        "end_time_unix_nano": span.end_time,
        "status": span.status.status_code.name,
        "attributes": dict(span.attributes or {}),
        "resource": dict(span.resource.attributes),
    }


def make_tracer(exporter=None, path=None):
    """A tracer that sends its spans to `exporter` (or to a JSON lines file at `path`)."""
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
    provider.add_span_processor(SimpleSpanProcessor(exporter or JsonLinesExporter(path)))
    return provider.get_tracer("relais.team")


class SpanBridge:
    """Opens and closes the OpenTelemetry span of each recorder event (used by relais.team.trace.Recorder)."""

    def __init__(self, tracer, run_attributes):
        self.tracer = tracer
        self.root = tracer.start_span("invoke_agent equipe", attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "equipe",
            **{k: clean(v) for k, v in run_attributes.items() if v is not None}})
        self.spans = {}

    def start(self, event, parent_event):
        parent = self.spans.get(parent_event.id) if parent_event else self.root
        span = self.tracer.start_span(span_name(event.agent, event.action, event.attributes),
                                      context=trace.set_span_in_context(parent),
                                      start_time=int(event.start * 1e9))
        self.spans[event.id] = span

    def end(self, event):
        span = self.spans.pop(event.id)
        span.set_attributes(span_attributes(event.agent, event.action, event.attributes))
        span.set_status(Status(StatusCode.OK if event.ok else StatusCode.ERROR))
        span.end(end_time=int(event.end * 1e9))

    def finish(self, **attributes):
        self.root.set_attributes({k: clean(v) for k, v in attributes.items() if v is not None})
        self.root.end()
