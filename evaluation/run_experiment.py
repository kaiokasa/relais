"""Variability experiment: the same questions replayed while the model, the temperature and the planner prompt
change (full factorial design, several repetitions of each cell).

Every run is traced with OpenTelemetry (runs/experience/traces.jsonl) and saved with its scores
(runs/experience/<config>/<question>_<repetition>.json); runs already done are skipped, so an interrupted
experiment can be resumed. The summary goes to evaluation/results/experience.csv.

    python evaluation/run_experiment.py --pilote        # one repetition of 4 questions per cell: cost check
    python evaluation/run_experiment.py                 # the whole experiment
"""
import argparse
import itertools
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from benchmark.build_references import data_version  # noqa: E402
from evaluation.run_benchmark import RESULTS, RUNS, run_one, summary_row  # noqa: E402
from relais.benchmark import LOTS, load_questions  # noqa: E402
from relais.llm import chat_model  # noqa: E402
from relais.team.graph import Team  # noqa: E402
from relais.telemetry import make_tracer  # noqa: E402
from relais.tools.sql import open_locked_copy  # noqa: E402

MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
TEMPERATURES = [0.0, 1.0]
PROMPTS = ["standard", "concis"]
REPETITIONS = 3
# 20 questions of the development set, from every category, easy and hard ones
QUESTIONS = ["Q01", "Q03", "Q05", "Q09", "Q12", "S01", "S02", "S06", "S08", "V01", "V02", "V04", "E01", "E04",
             "M02", "M04", "P01", "P04", "P08", "P10"]
PILOT = ["Q01", "S01", "E01", "P01"]
FOLDER = RUNS / "experience"


def config_name(model, temperature, prompt):
    return f"{model}_t{temperature:g}_{prompt}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pilote", action="store_true")
    parser.add_argument("--fils", type=int, default=6, help="runs in parallel")
    args = parser.parse_args()

    questions_file, references_file = LOTS["dev"]
    by_id = {q.id: q for q in load_questions(questions_file)}
    references = json.loads(references_file.read_text(encoding="utf-8"))
    base = open_locked_copy()
    if references["version_donnees"] != data_version(base):
        sys.exit("The references were computed on other data: run benchmark/build_references.py first.")
    ids = PILOT if args.pilote else QUESTIONS
    repetitions = 1 if args.pilote else REPETITIONS
    tracer = make_tracer(path=FOLDER / "traces.jsonl")

    jobs = []
    for model, temperature, prompt in itertools.product(MODELS, TEMPERATURES, PROMPTS):
        folder = FOLDER / config_name(model, temperature, prompt)
        folder.mkdir(parents=True, exist_ok=True)
        for repetition in range(1, repetitions + 1):
            for qid in ids:
                path = folder / f"{qid}_{repetition}.json"
                if not path.exists():
                    jobs.append((model, temperature, prompt, by_id[qid], repetition, path))
    print(f"{len(jobs)} runs to do", flush=True)

    local = threading.local()

    def team_for(model, temperature, prompt):
        """One DuckDB connection per thread (cursor of the shared locked copy), one team per configuration."""
        if not hasattr(local, "teams"):
            local.teams, local.con = {}, base.cursor()
        key = (model, temperature, prompt)
        if key not in local.teams:
            local.teams[key] = Team(chat_model(model, temperature), local.con, planner_prompt=prompt)
        return local.teams[key]

    def work(job):
        model, temperature, prompt, question, repetition, path = job
        config = {"modele": model, "temperature": temperature, "prompt": prompt, "lot": "dev"}
        record = run_one(team_for(model, temperature, prompt), question, references, config, repetition, tracer)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return record

    done = 0
    with ThreadPoolExecutor(max_workers=args.fils) as pool:
        for future in as_completed([pool.submit(work, job) for job in jobs]):
            record = future.result()
            done += 1
            config = record["config"]
            print(f"{done}/{len(jobs)} {config_name(config['modele'], config['temperature'], config['prompt'])} "
                  f"{record['question_id']} r{record['repetition']} {'JUSTE' if record['evaluation']['juste'] else 'faux '}"
                  f" {record.get('latence_s', 0):.0f} s {(record.get('cout_usd') or 0):.4f} $", flush=True)

    rows = []
    for path in sorted(FOLDER.glob("*/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        rows.append({**summary_row(record), **{k: record["config"][k] for k in ("modele", "temperature", "prompt")},
                     "run_id": record.get("run_id")})
    frame = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    frame.to_csv(RESULTS / ("experience_pilote.csv" if args.pilote else "experience.csv"), index=False)
    summary = frame.groupby(["modele", "temperature", "prompt"]).agg(
        runs=("juste", "size"), justesse=("juste", "mean"), conformes=("parcours_conforme", "mean"),
        cout=("cout_usd", "sum"), latence=("latence_s", "median"))
    print(summary.round(3).to_string())
    print(f"Coût total : {frame.cout_usd.sum():.2f} $")


if __name__ == "__main__":
    main()
