"""From the traces of the variability experiment to the three reports:

- reports/conversion.md        what the XES and OCEL 2.0 event logs keep and lose (logs in reports/eventlogs/)
- reports/process_mining.md    discovery, conformance with the reference model, variants, handover of work
- reports/experience.md        effect of the model, the temperature and the planner prompt on behaviour,
                               correctness, cost and latency, with statistical tests

    python evaluation/analyze_experiment.py
"""
import gzip
import json
import shutil
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from relais import eventlog, mining  # noqa: E402
from relais.benchmark import CATEGORIES, load_questions  # noqa: E402

EXPERIMENT = ROOT / "runs" / "experience"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"
LOGS = REPORTS / "eventlogs"
warnings.filterwarnings("ignore")


def fr(x, digits=2):
    """French number formatting for the reports."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    if isinstance(x, (int, np.integer)):
        return f"{x:,}".replace(",", " ")
    return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


def table_md(frame, digits=2):
    cells = frame.copy()
    for column in cells.columns:
        cells[column] = cells[column].map(lambda v: fr(v, digits) if isinstance(v, (float, int, np.number))
                                          and not isinstance(v, bool) else str(v))
    rows = [list(cells.columns), ["---"] * len(cells.columns)] + cells.values.tolist()
    return "\n".join("| " + " | ".join(map(str, r)) + " |" for r in rows)


def load():
    spans = eventlog.load_spans(EXPERIMENT / "traces.jsonl")
    scores = {}
    for path in EXPERIMENT.glob("*/*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("run_id"):
            scores[record["run_id"]] = {"juste": record["evaluation"]["juste"],
                                        "parcours_conforme": record["evaluation"]["parcours_conforme"],
                                        "cout_usd": record.get("cout_usd"), "latence_s": record.get("latence_s")}
    runs = eventlog.runs_table(spans, scores)
    runs = runs[runs.juste.notna()].copy()  # runs without a saved record (interrupted) are left out
    runs["latence_s"] = runs.run_id.map(lambda r: scores[r]["latence_s"])
    spans = spans[spans.trace_id.isin(runs.trace_id)]
    return spans, runs


def save_figure(figure, name):
    FIGURES.mkdir(parents=True, exist_ok=True)
    figure.write_image(FIGURES / f"{name}.png", width=1000, height=460, scale=1)
    return f"figures/{name}.png"


# ------------------------------------------------------------------------------------------- 6. conversion

def conversion(spans, runs):
    LOGS.mkdir(parents=True, exist_ok=True)
    agents = eventlog.xes_frame(spans, runs, "agents")
    detail = eventlog.xes_frame(spans, runs, "detail")
    for name, frame in (("agents", agents), ("detail", detail)):
        path = LOGS / f"experience_{name}.xes"
        eventlog.write_xes(frame, path)
        with open(path, "rb") as source, gzip.open(f"{path}.gz", "wb") as target:
            shutil.copyfileobj(source, target)
        path.unlink()
    ocel = eventlog.make_ocel(spans, runs)
    ocel_path = LOGS / "experience.jsonocel"
    eventlog.write_ocel(ocel, ocel_path)
    with open(ocel_path, "rb") as source, gzip.open(f"{ocel_path}.gz", "wb") as target:
        shutil.copyfileobj(source, target)
    ocel_path.unlink()
    r = eventlog.conversion_report(spans, runs, ocel)
    flat = r["aplatissement"]
    events = r["spans_hors_racine"]
    text = f"""# Des traces OpenTelemetry aux journaux d'événements

Généré par `evaluation/analyze_experiment.py` à partir des {fr(r['runs'])} runs de l'expérience de variabilité
({fr(r['spans'])} spans OpenTelemetry). Journaux produits dans [`eventlogs/`](eventlogs/) : XES au niveau des agents
et au niveau détaillé, OCEL 2.0 (JSON), compressés.

## Ce qui se convertit bien

- **L'ordre et la durée des actions** : chaque span a un début et une fin ; XES garde la fin (`time:timestamp`) et
  le début (`start_timestamp`), OCEL l'horodatage de l'événement.
- **Le cas** : une trace OpenTelemetry (un run) devient une trace XES ; ses attributs de configuration (question,
  modèle, température, prompt, statut, justesse) deviennent des attributs de cas.
- **La ressource** : `gen_ai.agent.name` devient `org:resource`, ce qui donne directement le réseau de passation.
- **Les erreurs** : le statut du span (OK, ERROR) devient un attribut d'événement.

## Ce qui se perd ou se déforme

| Aspect | Traces | XES | OCEL 2.0 |
|---|---|---|---|
| Hiérarchie des spans (profondeur jusqu'à {r['profondeur_max']}) | gardée (parent) | **perdue** : il faut choisir un niveau ({fr(r['evenements_xes_agents'])} événements « agents » ou {fr(r['evenements_xes_detail'])} « détail ») | **perdue** comme hiérarchie, remplacée par des relations aux objets |
| Imbrication dans le temps | gardée | {fr(r['chevauchements_detail'])} événements du niveau détaillé chevauchent le précédent (un appel d'outil pendant le tour de son agent) : une suite ordonnée les présente comme successifs | idem |
| Attributs ({r['cles_attributs']} clés distinctes) | typés, sur chaque span | gardés mais aplatis en chaînes (liste des problèmes du relecteur, texte SQL) | gardés sur les objets (texte de la requête, nombre de lignes du tableau) |
| Objets partagés (tableau lu par plusieurs agents) | implicites (identifiant en attribut) | **invisibles** : un événement n'appartient qu'à un cas | **explicites** : {fr(r['relations_ocel'])} relations événement-objet |
| Variabilité | - | {r['variantes_agents']} variantes au niveau agents, {r['variantes_detail']} au niveau détaillé | - |

Objets du journal OCEL : {', '.join(f"{k} {fr(v)}" for k, v in r['objets_ocel'].items())}.

## Aplatir l'OCEL : convergence et divergence

Revenir à un journal classique impose de choisir un type d'objet comme cas :

| Type d'objet choisi comme cas | Cas | Événements après aplatissement | Effet |
|---|---|---|---|
| question | {fr(flat['question']['cas'])} | {fr(flat['question']['evenements'])} | les {fr(r['runs'])} runs d'une même question sont fusionnés en un seul cas : les répétitions s'entremêlent (divergence) |
| tableau | {fr(flat['tableau']['cas'])} | {fr(flat['tableau']['evenements'])} | seuls les événements qui touchent un tableau restent (sur {fr(events)}) ; un événement qui lit plusieurs tableaux est dupliqué (convergence) |
| agent | {fr(flat['agent']['cas'])} | {fr(flat['agent']['evenements'])} | un cas par agent, qui mélange tous les runs : utile pour la charge d'un agent, pas pour un parcours |

**Conclusion.** Pour analyser les parcours de l'équipe (variantes, conformité), XES au niveau des agents avec un cas
par run suffit et c'est ce qu'utilise la suite. Pour suivre les artefacts (quelle requête a produit le tableau lu
par l'agent statistiques puis dessiné), seul l'OCEL garde l'information ; XES la perd et l'aplatissement de l'OCEL
la déforme dès qu'on choisit un autre objet que le run.
"""
    (REPORTS / "conversion.md").write_text(text, encoding="utf-8")
    return agents


# ------------------------------------------------------------------------------------------- 7. process mining

def process_mining(agents, runs, questions):
    expected = {q.id: tuple(q.parcours_attendu) for q in questions}
    reference = mining.reference_frame(questions)
    net = mining.discover_net(reference)
    fitness = mining.alignment_fitness(agents, net)
    seqs = mining.sequences(agents)
    runs = runs.copy()
    runs["fitness"] = runs.trace_id.map(fitness)
    runs["sequence"] = runs.trace_id.map(seqs)
    runs["distance_attendu"] = [mining.edit_distance(seqs.get(t, ()), expected[q])
                                for t, q in zip(runs.trace_id, runs.question_id)]
    runs["longueur"] = runs.sequence.map(len)
    runs["reprises_relecteur"] = runs.sequence.map(lambda s: max(0, s.count("relecteur") - 1))

    discovered = mining.discover_net(agents)
    fitness_discovered = mining.alignment_fitness(agents, discovered)
    variants = mining.variants(agents)
    handover = mining.handover(agents)
    reference_edges = set(mining.directly_follows(reference))
    observed = mining.directly_follows(agents)
    deviations = {e: n for e, n in observed.items() if e not in reference_edges}
    total = sum(observed.values())
    fig_obs = save_figure(mining.graph_figure(observed, "Parcours observés (en orange : absents du modèle de "
                                                         "référence)", reference=reference_edges), "dfg_observe")
    fig_ref = save_figure(mining.graph_figure(mining.directly_follows(reference), "Modèle de référence (parcours "
                                                                                   "attendus des 50 questions)"),
                          "dfg_reference")
    by_outcome = runs.groupby("juste").agg(runs=("trace_id", "size"), fitness=("fitness", "mean"),
                                           distance=("distance_attendu", "mean"), longueur=("longueur", "mean"),
                                           reprises=("reprises_relecteur", "mean")).reset_index()
    by_outcome["juste"] = by_outcome.juste.map({True: "réponse juste", False: "réponse fausse"})
    top = variants.head(10).copy()
    top["part"] = top.cas / top.cas.sum() * 100
    text = f"""# Process mining des parcours de l'équipe

Généré par `evaluation/analyze_experiment.py` sur {fr(len(runs))} runs (journal XES au niveau des agents).

## Modèle de référence et découverte

Le modèle de référence est découvert (Inductive Miner) à partir des parcours attendus des 50 questions : c'est le
comportement pour lequel l'équipe est conçue.

![Modèle de référence]({fig_ref})

![Parcours observés]({fig_obs})

- **Conformité au modèle de référence** (fitness des alignements) : {fr(np.nanmean(list(fitness.values())), 3)} en
  moyenne ; {fr(int(np.sum(np.array(list(fitness.values())) >= 0.999)))} runs sur {fr(len(fitness))} sont
  parfaitement conformes.
- **Modèle découvert sur les runs** (Inductive Miner, sans filtre de bruit) : fitness
  {fr(np.nanmean(list(fitness_discovered.values())), 3)} sur ses propres traces, un modèle plus permissif qui
  autorise les reprises.
- **Écarts au modèle de référence** : {fr(sum(deviations.values()))} transitions sur {fr(total)}
  ({fr(100 * sum(deviations.values()) / total, 1)} %) n'existent pas dans le modèle de référence :
  {', '.join(f"{a} → {b} ({fr(n)})" for (a, b), n in sorted(deviations.items(), key=lambda x: -x[1])[:6])}.

## Variantes

{fr(len(variants))} variantes pour {fr(len(runs))} runs ; les 10 plus fréquentes :

{table_md(top[["variante", "cas", "part"]], 1)}

## Réseau de passation entre agents

{table_md(handover.head(12))}

## Parcours et justesse

{table_md(by_outcome)}

Les réponses fausses suivent des parcours plus longs, plus éloignés du parcours attendu et avec plus de reprises
demandées par le relecteur : la forme du parcours renseigne sur le risque d'échec (exploité par la prédiction en
cours de run).
"""
    (REPORTS / "process_mining.md").write_text(text, encoding="utf-8")
    return runs


# ------------------------------------------------------------------------------------------- 8. variability

def anova_table(model):
    table = sm.stats.anova_lm(model, typ=2)
    table = table.drop(index=[i for i in table.index if i.startswith("C(question_id)") or i == "Residual"],
                       errors="ignore")
    return pd.DataFrame({"facteur": [i.replace("C(", "").replace(")", "") for i in table.index],
                         "F": table["F"].values, "p-valeur": table["PR(>F)"].values})


def experiment(runs):
    runs = runs.copy()
    runs["temperature"] = runs.temperature.astype(float)
    runs["juste_int"] = runs.juste.astype(int)
    runs["config"] = runs.modele + " / t=" + runs.temperature.map(lambda t: f"{t:g}") + " / " + runs.prompt
    cells = runs.groupby(["modele", "temperature", "prompt", "question_id", "categorie"]).agg(
        repetitions=("trace_id", "size"), variantes=("sequence", lambda s: len(set(s))),
        entropie=("sequence", lambda s: mining.entropy(list(s))),
        distance_paires=("sequence", lambda s: mining.mean_pairwise_distance(list(s))),
        justesse=("juste_int", "mean"), fitness=("fitness", "mean"), cout=("cout_usd", "mean"),
        latence=("latence_s", "mean")).reset_index()
    configs = runs.groupby(["modele", "temperature", "prompt"]).agg(
        runs=("trace_id", "size"), justesse=("juste_int", "mean"), fitness=("fitness", "mean"),
        cout_par_run=("cout_usd", "mean"), latence_mediane=("latence_s", "median")).reset_index()
    variability = cells.groupby(["modele", "temperature", "prompt"]).agg(
        variantes_par_question=("variantes", "mean"), entropie=("entropie", "mean"),
        distance_entre_repetitions=("distance_paires", "mean")).reset_index()
    configs = configs.merge(variability, on=["modele", "temperature", "prompt"])
    configs["justesse"] *= 100

    # statistical models: question as a fixed effect (each question is asked in every cell)
    gee = smf.gee("juste_int ~ C(modele) + C(temperature) + C(prompt)", groups="question_id", data=runs,
                  family=sm.families.Binomial(), cov_struct=sm.cov_struct.Exchangeable()).fit()
    odds = pd.DataFrame({"terme": gee.params.index, "rapport_de_cotes": np.exp(gee.params.values),
                         "p-valeur": gee.pvalues.values}).iloc[1:]
    odds["terme"] = odds.terme.str.replace(r"C\((\w+)\)\[T\.(.+)\]", r"\1 = \2", regex=True)
    entropy_model = smf.ols("entropie ~ C(modele) + C(temperature) + C(prompt) + C(question_id)", data=cells).fit()
    distance_model = smf.ols("distance_paires ~ C(modele) + C(temperature) + C(prompt) + C(question_id)",
                             data=cells).fit()
    fitness_model = smf.ols("fitness ~ C(modele) + C(temperature) + C(prompt) + C(question_id)", data=runs).fit()
    cost_model = smf.ols("np.log(cout_usd) ~ C(modele) + C(temperature) + C(prompt) + C(question_id)",
                         data=runs[runs.cout_usd > 0]).fit()
    latency_model = smf.ols("np.log(latence_s) ~ C(modele) + C(temperature) + C(prompt) + C(question_id)",
                            data=runs).fit()
    by_category = runs.groupby("categorie").agg(runs=("trace_id", "size"), justesse=("juste_int", "mean")).join(
        cells.groupby("categorie").agg(entropie=("entropie", "mean"), variantes=("variantes", "mean"))).reset_index()
    by_category["justesse"] *= 100
    by_category["categorie"] = by_category.categorie.map(CATEGORIES)

    import plotly.express as px
    from relais.tools.charts import PALETTE, style
    order = configs.sort_values("justesse")
    figure = px.bar(order, x="justesse", y=order.modele + " / t=" + order.temperature.map(lambda t: f"{t:g}")
                    + " / " + order.prompt, orientation="h", color_discrete_sequence=PALETTE)
    style(figure, {"titre": "Justesse par configuration (%)", "titre_x": "% de réponses justes", "titre_y": ""}, 1)
    fig_accuracy = save_figure(figure, "experience_justesse")
    figure = px.scatter(configs, x="cout_par_run", y="justesse", color="modele", symbol="prompt",
                        text=configs.temperature.map(lambda t: f"t={t:g}"), color_discrete_sequence=PALETTE)
    figure.update_traces(textposition="middle right", textfont={"size": 11})
    style(figure, {"titre": "Coût et justesse des 8 configurations", "titre_x": "coût moyen par run ($)",
                   "titre_y": "% de réponses justes"}, 3)
    fig_cost = save_figure(figure, "experience_cout_justesse")
    figure = px.bar(variability.assign(temperature=variability.temperature.map(lambda t: f"t={t:g}")),
                    x="modele", y="variantes_par_question", color="temperature", barmode="group",
                    facet_col="prompt", color_discrete_sequence=PALETTE)
    style(figure, {"titre": "Variantes distinctes par question (sur 3 répétitions)", "titre_y": "variantes"}, 2)
    fig_variability = save_figure(figure, "experience_variabilite")

    def pv(table, factor):
        row = table[table.facteur == factor]
        return fr(float(row["p-valeur"].iloc[0]), 3) if len(row) else "-"

    entropy_anova, distance_anova = anova_table(entropy_model), anova_table(distance_model)
    fitness_anova, cost_anova, latency_anova = (anova_table(fitness_model), anova_table(cost_model),
                                                anova_table(latency_model))
    effects = pd.DataFrame({"facteur": ["modele", "temperature", "prompt"]})
    for name, table in (("entropie des parcours", entropy_anova), ("distance entre répétitions", distance_anova),
                        ("fitness", fitness_anova), ("coût (log)", cost_anova), ("latence (log)", latency_anova)):
        effects[name] = [pv(table, f) for f in effects.facteur]
    effects["justesse (GEE)"] = [", ".join(fr(p, 3) for p in odds[odds.terme.str.startswith(f)]["p-valeur"])
                                 for f in effects.facteur]
    total_cost = runs.cout_usd.sum()
    text = f"""# Expérience de variabilité

Généré par `evaluation/analyze_experiment.py`. Plan factoriel complet : 2 modèles Gemini (gemini-3.1-flash-lite et gemini-3.5-flash-lite) × 2 températures (0 et 1)
× 2 prompts du planificateur (« standard » avec règles et guide de décision, « concis » sans) × 20 questions du lot
de développement (toutes les catégories) × 3 répétitions = {fr(len(runs))} runs, {fr(total_cost)} $ au total.

Mesures : pour chaque cellule (configuration × question), le nombre de variantes de parcours distinctes sur les
3 répétitions, leur entropie (0 si les 3 parcours sont identiques), la distance d'édition moyenne entre
répétitions ; pour chaque run, la justesse, la fitness au modèle de référence, le coût et la latence.

## Les 8 configurations

{table_md(configs.assign(cout_pour_1000_runs=configs.cout_par_run * 1000).drop(columns="cout_par_run")
           .rename(columns={"temperature": "température", "cout_pour_1000_runs": "coût pour 1 000 runs ($)"}), 2)}

![Justesse par configuration]({fig_accuracy})

![Coût et justesse]({fig_cost})

![Variabilité des parcours]({fig_variability})

## Quels facteurs comptent ? (p-valeurs)

Modèles linéaires avec la question en effet fixe (ANOVA de type II) pour l'entropie, la distance, la fitness, le
coût et la latence ; régression logistique GEE (corrélation intra-question échangeable) pour la justesse.

{table_md(effects)}

Rapports de cotes de la justesse (référence : gemini-3.1-flash-lite, température 0, prompt concis) :

{table_md(odds, 3)}

## Par type de question

{table_md(by_category, 2)}

## Limites

- 20 questions et 3 répétitions par cellule : les effets faibles ne sont pas détectables ; l'entropie sur 3
  répétitions ne prend que quelques valeurs.
- Les modèles ont des latences très différentes : la latence mesure aussi la charge de l'API au moment du run.
- Les questions sont celles du lot de développement (celles qui ont servi à concevoir l'équipe).
"""
    (REPORTS / "experience.md").write_text(text, encoding="utf-8")
    runs.drop(columns=["sequence"]).to_csv(ROOT / "evaluation" / "results" / "experience_runs.csv", index=False)
    return configs


def main():
    spans, runs = load()
    print(f"{len(runs)} runs, {len(spans)} spans")
    questions = load_questions()
    agents = conversion(spans, runs)
    print("conversion : reports/conversion.md")
    runs = process_mining(agents, runs, questions)
    print("process mining : reports/process_mining.md")
    configs = experiment(runs)
    print("expérience : reports/experience.md")
    print(configs.round(3).to_string())


if __name__ == "__main__":
    main()
