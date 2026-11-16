"""Train and evaluate the prediction of a wrong answer from the beginning of the trace.

Data: every saved run with its trace (variability experiment and benchmark runs). Evaluation: cross-validation
grouped by question, AUC and Brier score by prefix length. The best classifier is retrained on all the data and
saved in models/risque_echec.joblib for the live dashboard. Report: reports/prediction.md.

    python evaluation/train_failure_model.py
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evaluation.analyze_experiment import FIGURES, REPORTS, fr, table_md  # noqa: E402
from relais import prediction  # noqa: E402


def load_records():
    records = []
    for path in sorted((ROOT / "runs").glob("**/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("events") and "evaluation" in record and record.get("config"):
            records.append(record)
    return records


def main():
    records = load_records()
    data = prediction.prefix_dataset(records)
    whole = data[data.longueur == "complet"]
    print(f"{len(records)} runs, {len(data)} préfixes, {whole.echec.mean():.1%} de réponses fausses")
    scores, predictions = prediction.grouped_scores(data)
    best = scores[scores.longueur == 4].sort_values("auc", ascending=False).modele_prediction.iloc[0]
    prediction.train_final(data, best)

    import plotly.express as px
    from relais.tools.charts import PALETTE, style
    curve = scores[scores.longueur != "complet"].copy()
    curve["longueur"] = curve.longueur.astype(int)
    figure = px.line(curve, x="longueur", y="auc", color="modele_prediction", markers=True,
                     color_discrete_sequence=PALETTE)
    style(figure, {"titre": "Prédire l'échec à partir des k premiers événements terminés",
                   "titre_x": "événements observés (k)", "titre_y": "AUC (validation groupée par question)"}, 2)
    figure.add_hline(y=0.5, line_dash="dot", line_color="#898781")
    FIGURES.mkdir(parents=True, exist_ok=True)
    figure.write_image(FIGURES / "prediction_auc.png", width=1000, height=460)

    # calibration of the chosen model, all prefixes together
    chosen = predictions[predictions.modele_prediction == best].copy()
    chosen["tranche"] = pd.cut(chosen.proba, [0, 0.2, 0.4, 0.6, 0.8, 1.0], include_lowest=True)
    calibration = chosen.groupby("tranche", observed=True).agg(predictions=("proba", "size"),
                                                               risque_predit=("proba", "mean"),
                                                               echecs_observes=("echec", "mean")).reset_index()
    calibration["tranche"] = calibration.tranche.astype(str)
    table = scores.pivot(index="longueur", columns="modele_prediction", values="auc").reset_index()
    table["longueur"] = table.longueur.astype(str)
    importance = ""
    if best == "régression logistique":
        coefficients = prediction.make_classifiers()[best].fit(data[prediction.FEATURES], data.echec)[-1].coef_[0]
        top = pd.DataFrame({"caractéristique": prediction.FEATURES, "coefficient": coefficients})
        top = top.reindex(top.coefficient.abs().sort_values(ascending=False).index).head(10)
        importance = "\n## Caractéristiques les plus influentes (coefficients standardisés)\n\n" + table_md(top, 3)
    text = f"""# Prédire l'échec en cours de run

Généré par `evaluation/train_failure_model.py`. Données : {fr(len(records))} runs tracés (expérience de
variabilité et benchmarks), dont {fr(whole.echec.mean() * 100, 1)} % de réponses fausses. Pour chaque run, on ne
regarde que ses k premiers événements terminés (tours d'agents, appels au modèle, appels d'outils), comme un
observateur en direct.

Caractéristiques : configuration (modèle, température, prompt) et ce qui s'est déjà passé (tours de chaque agent,
appels et échecs d'outils, refus du relecteur, tokens, durée). La catégorie de la question n'est pas utilisée : elle
est inconnue pour une vraie question. Validation croisée en 5 groupes **par question** : le modèle est toujours
évalué sur des questions qu'il n'a pas vues.

![AUC selon la longueur du préfixe](figures/prediction_auc.png)

## AUC par longueur de préfixe

{table_md(table, 3)}

## Calibration ({best}, tous préfixes)

{table_md(calibration, 2)}
{importance}

## Lecture et limites

- Le modèle retenu pour le tableau de bord est « {best} », réentraîné sur toutes les données
  (`models/risque_echec.joblib`).
- Au tout début (k = 1, le seul plan du planificateur) le risque ne dépend presque que de la configuration ; il
  s'affine à mesure que les agents agissent, et les refus du relecteur sont un signal fort.
- Les runs d'une même question se ressemblent : sans validation groupée par question, l'AUC serait surestimée.
- Les données viennent d'un seul jeu de questions ; le risque prédit est un signal d'alerte, pas une probabilité
  garantie sur d'autres questions.
"""
    (REPORTS / "prediction.md").write_text(text, encoding="utf-8")
    print(table.round(3).to_string())
    print(f"modèle retenu : {best}")


if __name__ == "__main__":
    main()
