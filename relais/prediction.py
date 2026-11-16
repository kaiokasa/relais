"""Predicting, while a run goes on, the probability that its answer will be wrong, from the beginning of its trace.

The features only use what is known at that moment: the configuration (model, temperature, planner prompt) and
the events already finished (turns of each agent, tool calls and their failures, rejections by the reviewer,
tokens, elapsed time). The category of the question is NOT used: it is unknown for a real user question, and
the model must work live in the dashboard.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILE = ROOT / "models" / "risque_echec.joblib"
MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
TURNS = ["planificateur:planifier", "planificateur:rediger", "sql:etape", "statistiques:etape",
         "visualisation:etape", "relecteur:relire"]
TOOLS = ["executer_sql", "executer_python", "creer_graphique"]


def event_dict(event):
    """An event of the recorder, or the dict of a saved run, as a plain dict."""
    if isinstance(event, dict):
        return event
    return {"id": event.id, "parent": event.parent, "agent": event.agent, "action": event.action,
            "start": event.start, "end": event.end, "ok": event.ok, "attributes": event.attributes}


def finished_in_order(events):
    """The finished events, in the order they finished (what an observer has seen at each moment)."""
    done = [event_dict(e) for e in events if event_dict(e).get("end") is not None]
    return sorted(done, key=lambda e: e["end"])


def features(prefix, config):
    """The features of a prefix (the first finished events of a run) and of the configuration."""
    f = {f"modele={m}": float(config.get("modele") == m) for m in MODELS}
    f["temperature"] = float(config.get("temperature") or 0.0)
    f["prompt_concis"] = float(config.get("prompt") == "concis")
    f["evenements"] = float(len(prefix))
    f["duree_s"] = (prefix[-1]["end"] - prefix[0]["start"]) if prefix else 0.0
    for turn in TURNS:
        agent, action = turn.split(":")
        f[f"tours_{agent}_{action}"] = float(sum(1 for e in prefix if e["agent"] == agent and e["action"] == action))
    for tool in TOOLS:
        calls = [e for e in prefix if e["action"] == tool]
        f[f"appels_{tool}"] = float(len(calls))
        f[f"echecs_{tool}"] = float(sum(1 for e in calls if not e["ok"]))
    f["appels_llm"] = float(sum(1 for e in prefix if e["action"] == "appel_llm"))
    f["tokens_entree_k"] = sum(e["attributes"].get("input_tokens", 0) or 0 for e in prefix) / 1000
    f["tokens_sortie_k"] = sum(e["attributes"].get("output_tokens", 0) or 0 for e in prefix) / 1000
    reviews = [e for e in prefix if e["action"] == "relire"]
    f["refus_relecteur"] = float(sum(1 for e in reviews if e["attributes"].get("verdict") == "a_corriger"))
    f["refus_automatiques"] = float(sum(1 for e in reviews if (e["attributes"].get("controles_automatiques") or 0)))
    f["lignes_max_sql"] = float(max([e["attributes"].get("lignes", 0) or 0 for e in prefix
                                     if e["action"] == "executer_sql"], default=0))
    return f


FEATURES = list(features([], {}).keys())


def prefix_dataset(records, lengths=(1, 2, 4, 6, 8, 12, 16, 24, None)):
    """One row per (run, prefix length): features, label (1 = wrong answer), question, prefix length.
    A length of None is the whole run; a prefix longer than the run is the whole run too."""
    rows = []
    for record in records:
        ordered = finished_in_order(record["events"])
        for k in lengths:
            prefix = ordered if k is None else ordered[:k]
            rows.append({**features(prefix, record["config"]), "echec": int(not record["evaluation"]["juste"]),
                         "question_id": record["question_id"], "longueur": "complet" if k is None else k,
                         "fraction": len(prefix) / max(len(ordered), 1), "run": record.get("run_id")})
    return pd.DataFrame(rows)


def make_classifiers():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {"régression logistique": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5)),
            "gradient boosting": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=200,
                                                                l2_regularization=1.0)}


def grouped_scores(data, folds=5, seed=0):
    """AUC and Brier score of each classifier, by prefix length, with a cross-validation grouped by question
    (a question is never in the training and the test folds at the same time)."""
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.model_selection import GroupKFold
    predictions = []
    splitter = GroupKFold(n_splits=folds)
    for train, test in splitter.split(data, data.echec, data.question_id):
        for name, classifier in make_classifiers().items():
            classifier.fit(data.iloc[train][FEATURES], data.iloc[train].echec)
            part = data.iloc[test][["longueur", "echec", "question_id"]].copy()
            part["modele_prediction"] = name
            part["proba"] = classifier.predict_proba(data.iloc[test][FEATURES])[:, 1]
            predictions.append(part)
    predictions = pd.concat(predictions)
    rows = []
    for (name, length), group in predictions.groupby(["modele_prediction", "longueur"], sort=False):
        if group.echec.nunique() < 2:
            continue
        rows.append({"modele_prediction": name, "longueur": length, "auc": roc_auc_score(group.echec, group.proba),
                     "brier": brier_score_loss(group.echec, group.proba), "runs": len(group)})
    return pd.DataFrame(rows), predictions


def train_final(data, name):
    import joblib
    classifier = make_classifiers()[name]
    classifier.fit(data[FEATURES], data.echec)
    MODEL_FILE.parent.mkdir(exist_ok=True)
    joblib.dump({"classifier": classifier, "features": FEATURES, "nom": name}, MODEL_FILE)
    return classifier


class RiskModel:
    """The trained model, used live: risk(events so far, configuration) -> probability of a wrong answer."""

    def __init__(self, path=MODEL_FILE):
        import joblib
        bundle = joblib.load(path)
        self.classifier, self.features, self.name = bundle["classifier"], bundle["features"], bundle["nom"]

    def risk(self, events, config):
        row = features(finished_in_order(events), config)
        frame = pd.DataFrame([[row[f] for f in self.features]], columns=self.features)
        return float(self.classifier.predict_proba(frame)[0, 1])
