"""Score again a saved run with the current evaluation (no LLM call): used when the scoring itself is fixed.

    python evaluation/rescore.py benchmark_flash-lite-3.1_t0
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evaluation.run_benchmark import RESULTS, RUNS, print_summary, summary_row  # noqa: E402
from relais.benchmark import LOTS, load_questions  # noqa: E402
from relais.evaluation import evaluate  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("nom", help="name of the run (folder in runs/)")
    parser.add_argument("--lot", choices=list(LOTS), default="dev")
    args = parser.parse_args()
    questions_file, references_file = LOTS[args.lot]
    questions = {q.id: q for q in load_questions(questions_file)}
    references = json.loads(references_file.read_text(encoding="utf-8"))["questions"]
    rows = []
    for path in sorted((RUNS / args.nom).glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        q = questions[record["question_id"]]
        if record["statut"] != "erreur":
            record["evaluation"] = evaluate(q, record["statut"], record["reponse"], record.get("graphique"),
                                            record.get("path", []), references[q.id]["valeurs"])
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        rows.append(summary_row(record))
    frame = pd.DataFrame(rows)
    frame.to_csv(RESULTS / f"{args.nom}.csv", index=False)
    print_summary(frame)


if __name__ == "__main__":
    main()
