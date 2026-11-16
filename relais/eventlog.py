"""From OpenTelemetry spans (relais/telemetry.py) to event logs for process mining.

- XES, one case per run (one trace), at two levels:
  * "agents": one event per turn of an agent (planificateur, sql, ...): the level of the reference model;
  * "detail": every span (turns, LLM calls, tool calls), the activity being "agent:action".
  Case attributes: question, category, model, temperature, prompt, answer status, correctness.
- OCEL 2.0 (object-centric): the events are the spans, related to the objects they touch: the question, the run,
  the agent, the SQL query, the result table, the chart, the Python code.

A span keeps its parent, its start and end and typed attributes; what each format keeps or loses is measured by
`conversion_report` (reports/conversion.md).
"""
import json
from pathlib import Path

import pandas as pd

ROOT_NAME = "invoke_agent equipe"


def load_spans(path):
    """The spans of a JSON lines file, as a DataFrame (traces without a finished root span are left out)."""
    spans = pd.DataFrame([json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line])
    roots = spans[spans.name == ROOT_NAME]
    spans = spans[spans.trace_id.isin(roots.trace_id)].copy()
    spans["start"] = pd.to_datetime(spans.start_time_unix_nano, unit="ns", utc=True)
    spans["end"] = pd.to_datetime(spans.end_time_unix_nano, unit="ns", utc=True)
    spans["agent"] = spans.attributes.map(lambda a: a.get("gen_ai.agent.name"))
    spans["action"] = spans.attributes.map(lambda a: a.get("relais.action"))
    return spans.sort_values(["trace_id", "start_time_unix_nano"]).reset_index(drop=True)


def runs_table(spans, scores=None):
    """One line per run (the root span), with its configuration; `scores` (run_id -> dict) adds the scores."""
    roots = spans[spans.name == ROOT_NAME]
    rows = []
    for _, root in roots.iterrows():
        a = root.attributes
        rows.append({"trace_id": root.trace_id, "run_id": a.get("gen_ai.conversation.id"),
                     "question_id": a.get("relais.question_id"), "categorie": a.get("relais.categorie"),
                     "repetition": a.get("relais.repetition"), "modele": a.get("gen_ai.request.model"),
                     "temperature": a.get("gen_ai.request.temperature"), "prompt": a.get("relais.planner_prompt"),
                     "statut": a.get("relais.answer.status"), "approuve": a.get("relais.answer.approved"),
                     "input_tokens": a.get("gen_ai.usage.input_tokens"),
                     "output_tokens": a.get("gen_ai.usage.output_tokens"),
                     "debut": root.start, "fin": root.end})
    runs = pd.DataFrame(rows)
    if scores is not None and len(runs):
        extra = runs.run_id.map(lambda r: scores.get(r, {}))
        for key in ("juste", "parcours_conforme", "cout_usd"):
            runs[key] = extra.map(lambda d: d.get(key))
    return runs


def xes_frame(spans, runs, level="agents"):
    """The event log as a pm4py-ready DataFrame (case:concept:name, concept:name, time:timestamp...)."""
    root_ids = set(spans[spans.name == ROOT_NAME].span_id)
    if level == "agents":
        events = spans[spans.parent_span_id.isin(root_ids)].copy()
        events["activite"] = events.agent
    elif level == "detail":
        events = spans[spans.name != ROOT_NAME].copy()
        events["activite"] = events.agent + ":" + events.action
    else:
        raise ValueError(f"unknown level {level}")
    events = events.merge(runs, on="trace_id", how="left")
    frame = pd.DataFrame({
        "case:concept:name": events.trace_id,
        "concept:name": events.activite,
        "time:timestamp": events.end,
        "start_timestamp": events.start,
        "org:resource": events.agent,
        "lifecycle:transition": "complete",
        "ok": events.status == "OK",
    })
    for column in ("question_id", "categorie", "modele", "temperature", "prompt", "statut", "juste"):
        if column in events:
            frame[f"case:{column}"] = events[column].values
    return frame.sort_values(["case:concept:name", "time:timestamp"]).reset_index(drop=True)


def write_xes(frame, path):
    import pm4py
    log = frame.copy()
    for column in log.columns:  # XES attributes must be strings, numbers, booleans or dates
        if log[column].dtype == object:
            log[column] = log[column].map(lambda v: v if v is None or isinstance(v, (bool, float, int)) else str(v))
    pm4py.write_xes(log, str(path), case_id_key="case:concept:name")


# ------------------------------------------------------------------------------------------- OCEL 2.0

def ocel_tables(spans, runs):
    """Events, objects and event-to-object relations of the object-centric log."""
    events = spans[spans.name != ROOT_NAME].copy()
    events["ocel:eid"] = events.span_id
    events["ocel:activity"] = events.agent + ":" + events.action
    events["ocel:timestamp"] = events.end.dt.tz_localize(None)
    run_of = runs.set_index("trace_id")
    objects, relations, o2o = {}, [], []

    def obj(oid, kind, **attributes):
        objects.setdefault(oid, {"ocel:oid": oid, "ocel:type": kind, **attributes})
        return oid

    def relate(event, oid, qualifier):
        relations.append({"ocel:eid": event["ocel:eid"], "ocel:activity": event["ocel:activity"],
                          "ocel:timestamp": event["ocel:timestamp"], "ocel:oid": oid,
                          "ocel:type": objects[oid]["ocel:type"], "ocel:qualifier": qualifier})

    for _, e in events.iterrows():
        run = run_of.loc[e.trace_id]
        a = e.attributes
        run_oid = obj(f"run:{e.trace_id[:12]}", "run", modele=run.modele, temperature=run.temperature,
                      prompt=run.prompt)
        question_oid = obj(f"question:{run.question_id}", "question", categorie=run.categorie)
        if (run_oid, question_oid, "repond a") not in o2o:
            o2o.append((run_oid, question_oid, "repond a"))
        relate(e, run_oid, "dans")
        relate(e, question_oid, "pour")
        relate(e, obj(f"agent:{e.agent}", "agent"), "par")
        prefix = e.trace_id[:12]
        if e.action == "executer_sql":
            query = obj(f"requete:{e.span_id}", "requete", texte=a.get("relais.sql.query", "")[:2000])
            relate(e, query, "execute")
            if a.get("relais.table.id"):
                table = obj(f"tableau:{prefix}:{a['relais.table.id']}", "tableau", lignes=a.get("relais.table.rows"))
                relate(e, table, "produit")
                o2o.append((query, table, "produit"))
        elif e.action == "executer_python":
            relate(e, obj(f"code:{e.span_id}", "code", texte=a.get("relais.python.code", "")[:2000]), "execute")
            for table in [o for o in objects if o.startswith(f"tableau:{prefix}:")]:
                relate(e, table, "lit")
        elif e.action == "creer_graphique":
            spec = json.loads(a.get("relais.chart.specification", "{}") or "{}")
            table = f"tableau:{prefix}:{spec.get('tableau')}"
            if table in objects:
                relate(e, table, "lit")
            if e.status == "OK":
                chart = obj(f"graphique:{e.span_id}", "graphique", type=a.get("relais.chart.type"))
                relate(e, chart, "produit")
                if table in objects:
                    o2o.append((chart, table, "dessine"))
    event_frame = events[["ocel:eid", "ocel:activity", "ocel:timestamp"]].reset_index(drop=True)
    object_frame = pd.DataFrame(objects.values())
    relation_frame = pd.DataFrame(relations)
    o2o_frame = pd.DataFrame(o2o, columns=["ocel:oid", "ocel:oid_2", "ocel:qualifier"]).drop_duplicates()
    return event_frame, object_frame, relation_frame, o2o_frame


def make_ocel(spans, runs):
    from pm4py.objects.ocel.obj import OCEL
    events, objects, relations, o2o = ocel_tables(spans, runs)
    return OCEL(events=events, objects=objects, relations=relations, o2o=o2o)


def write_ocel(ocel, path):
    import pm4py
    pm4py.write_ocel2_json(ocel, str(path))


# ------------------------------------------------------------------------------------------- what is kept or lost

def conversion_report(spans, runs, ocel=None):
    """Numbers for the study of the conversion: what each format keeps, what it loses."""
    agents = xes_frame(spans, runs, "agents")
    detail = xes_frame(spans, runs, "detail")
    non_root = spans[spans.name != ROOT_NAME]
    attribute_keys = sorted({k for a in non_root.attributes for k in a})
    depth = {}
    parents = dict(zip(spans.span_id, spans.parent_span_id))
    for sid in non_root.span_id:
        d, p = 0, parents.get(sid)
        while p is not None and p in parents:
            d, p = d + 1, parents.get(p)
        depth[sid] = d
    # two events of one case overlapping in time: a tool call inside a turn of an agent
    overlap = 0
    for _, group in detail.groupby("case:concept:name"):
        starts, ends = group.start_timestamp.values, group["time:timestamp"].values
        overlap += sum(1 for i in range(1, len(group)) if starts[i] < ends[i - 1])
    report = {
        "runs": int(runs.shape[0]),
        "spans": int(len(spans)),
        "spans_hors_racine": int(len(non_root)),
        "profondeur_max": int(max(depth.values(), default=0)),
        "cles_attributs": len(attribute_keys),
        "evenements_xes_agents": int(len(agents)),
        "evenements_xes_detail": int(len(detail)),
        "chevauchements_detail": int(overlap),
        "variantes_agents": int(agents.groupby("case:concept:name")["concept:name"].agg(tuple).nunique()),
        "variantes_detail": int(detail.groupby("case:concept:name")["concept:name"].agg(tuple).nunique()),
    }
    if ocel is not None:
        import pm4py
        report["objets_ocel"] = {str(k): int(v) for k, v in ocel.objects["ocel:type"].value_counts().items()}
        report["relations_ocel"] = int(len(ocel.relations))
        flattened = {}
        for kind in ("question", "tableau", "agent"):
            flat = pm4py.ocel_flattening(ocel, kind)
            flattened[kind] = {"evenements": int(len(flat)), "cas": int(flat["case:concept:name"].nunique())}
        report["aplatissement"] = flattened
    return report
