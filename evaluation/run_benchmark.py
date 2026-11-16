"""Run the team on the question set and score every answer.

Each run is saved in full in runs/<name>/ (answer, checks, trace, tokens; ignored by git) and summarised in
evaluation/results/<name>.csv (kept in git).

    python evaluation/run_benchmark.py --ids Q01 S01 P01           # a few questions
    python evaluation/run_benchmark.py                             # the 50 questions
    python evaluation/run_benchmark.py --modele gemini-3.1-flash-lite --temperature 0.7 --repetitions 3
"""
import argparse
import dataclasses
import datetime as dt
import json
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchmark.build_references import data_version  # noqa: E402
from relais.benchmark import CATEGORIES, LOTS, load_questions  # noqa: E402
from relais.evaluation import evaluate  # noqa: E402
from relais.llm import chat_model, model_name  # noqa: E402
from relais.team.graph import Team  # noqa: E402
from relais.telemetry import make_tracer  # noqa: E402
from relais.tools.sql import open_locked_copy  # noqa: E402

RUNS = ROOT / "runs"
RESULTS = ROOT / "evaluation" / "results"


def event_record(event):
    return {**dataclasses.asdict(event), "duration_ms": event.duration_ms}


def chart_record(chart):
    return None if chart is None else {"type": chart.type, "series": chart.series, "points": chart.points,
                                       "spec": chart.spec}


def run_one(team, question, references, config, repetition, tracer=None):
    started = time.time()
    record = {"question_id": question.id, "categorie": question.categorie, "question": question.question,
              "repetition": repetition, "config": config, "date": dt.datetime.now().isoformat(timespec="seconds")}
    try:
        answer = team.ask(question.question, tracer=tracer, run_attributes={
            "relais.question_id": question.id, "relais.categorie": question.categorie,
            "relais.repetition": repetition, "relais.lot": config.get("lot", "dev")})
    except Exception as error:  # an API error must not stop the benchmark: the run is recorded as failed
        record.update(statut="erreur", reponse="", erreur=f"{type(error).__name__}: {error}",
                      trace_python=traceback.format_exc()[-3000:], latence_s=time.time() - started, path=[],
                      evaluation={"juste": False, "statut_ok": False, "parcours_conforme": False})
        return record
    chart = chart_record(answer.graphique)
    tokens_in, tokens_out = answer.tokens
    record.update(
        run_id=answer.run_id, statut=answer.statut, reponse=answer.reponse, approuve=answer.approuve, problemes=answer.problemes,
        path=answer.path, etapes=answer.etapes, graphique=chart, latence_s=round(answer.latence_s, 2),
        input_tokens=tokens_in, output_tokens=tokens_out, cout_usd=answer.cout_usd, usage=answer.usage,
        requetes=[{"id": t.id, "sql": t.sql, "lignes": len(t.data)} for t in answer.tables.values()],
        statistiques=answer.statistiques, events=[event_record(e) for e in answer.events],
        evaluation=evaluate(question, answer.statut, answer.reponse, chart, answer.path,
                            references["questions"][question.id]["valeurs"]))
    return record


def summary_row(record):
    ev = record["evaluation"]
    return {"question_id": record["question_id"], "categorie": record["categorie"], "repetition": record["repetition"],
            "statut": record["statut"], "juste": ev["juste"], "statut_ok": ev["statut_ok"],
            "parcours_conforme": ev["parcours_conforme"], "valeurs_manquantes": ";".join(ev.get("valeurs_manquantes", [])),
            "mentions_manquantes": ";".join(ev.get("mentions_manquantes", [])),
            "interdits_trouves": ";".join(ev.get("interdits_trouves", [])), "graphique_ok": ev.get("graphique_ok"),
            "approuve": record.get("approuve"), "parcours": " > ".join(record.get("path", [])),
            "latence_s": record.get("latence_s"), "input_tokens": record.get("input_tokens"),
            "output_tokens": record.get("output_tokens"), "cout_usd": record.get("cout_usd"),
            "erreur": record.get("erreur", "")}


def print_summary(frame):
    by_category = frame.groupby("categorie").agg(questions=("question_id", "count"), justes=("juste", "sum"),
                                                 parcours_conformes=("parcours_conforme", "sum"),
                                                 cout_moyen=("cout_usd", "mean"), latence_moyenne=("latence_s", "mean"))
    by_category.index = [CATEGORIES.get(c, c) for c in by_category.index]
    print(by_category.round(4).to_string())
    print(f"\nJustesse : {frame.juste.sum()}/{len(frame)} ({frame.juste.mean():.0%}) ; parcours conformes : "
          f"{frame.parcours_conforme.sum()}/{len(frame)} ; coût total {frame.cout_usd.sum():.4f} $ ; "
          f"latence médiane {frame.latence_s.median():.1f} s")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lot", choices=list(LOTS), default="dev", help="dev (50 questions) or test (24)")
    parser.add_argument("--ids", nargs="+", help="questions to run (all by default)")
    parser.add_argument("--modele", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--prompt", default="standard", help="variant of the planner prompt")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--nom", default=None, help="name of the run (folder and CSV file)")
    args = parser.parse_args()

    questions_file, references_file = LOTS[args.lot]
    questions = load_questions(questions_file)
    if args.ids:
        unknown = set(args.ids) - {q.id for q in questions}
        if unknown:
            sys.exit(f"Unknown questions: {sorted(unknown)}")
        questions = [q for q in questions if q.id in args.ids]
    references = json.loads(references_file.read_text(encoding="utf-8"))
    con = open_locked_copy()
    if references["version_donnees"] != data_version(con):
        sys.exit("The references were computed on other data: run benchmark/build_references.py first.")

    model = model_name(args.modele)
    config = {"modele": model, "temperature": args.temperature, "prompt": args.prompt, "lot": args.lot}
    name = args.nom or f"{dt.datetime.now():%Y%m%d_%H%M%S}_{args.lot}_{model}_t{args.temperature:g}_{args.prompt}"
    folder = RUNS / name
    folder.mkdir(parents=True, exist_ok=True)
    team = Team(chat_model(args.modele, args.temperature), con, planner_prompt=args.prompt)
    tracer = make_tracer(path=folder / "traces.jsonl")  # OpenTelemetry spans, one JSON line each

    rows = []
    for repetition in range(1, args.repetitions + 1):
        for question in questions:
            record = run_one(team, question, references, config, repetition, tracer)
            (folder / f"{question.id}_{repetition}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            rows.append(summary_row(record))
            ev = record["evaluation"]
            print(f"{question.id} r{repetition} [{record['statut']}] {'JUSTE' if ev['juste'] else 'faux '} "
                  f"{'conforme' if ev['parcours_conforme'] else 'déviant '} {record.get('latence_s', 0):5.1f} s "
                  f"{(record.get('cout_usd') or 0):.4f} $ {' > '.join(record.get('path', []))}", flush=True)
    frame = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    frame.to_csv(RESULTS / f"{name}.csv", index=False)
    print()
    print_summary(frame)
    print(f"\nDétail : {folder.relative_to(ROOT)} ; résumé : {(RESULTS / f'{name}.csv').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
