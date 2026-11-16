"""Relais: watching a team of LLM agents live.

    streamlit run app/dashboard.py
"""
import json
import queue
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from relais.llm import chat_model, gemini_api_key  # noqa: E402
from relais.mining import graph_figure  # noqa: E402
from relais.monitor import LiveMonitor, reference_transitions  # noqa: E402
from relais.prediction import MODEL_FILE, MODELS, RiskModel  # noqa: E402
from relais.team.graph import Team  # noqa: E402
from relais.team.prompts import PLANNER_PROMPTS  # noqa: E402
from relais.team.trace import Event  # noqa: E402
from relais.tools.charts import INK_2, PALETTE, SURFACE  # noqa: E402
from relais.tools.sql import DB_PATH, open_locked_copy  # noqa: E402

st.set_page_config(page_title="Relais", layout="wide")
LEVEL_ICONS = {"alerte": "⛔", "attention": "⚠️", "info": "ℹ️"}
EXAMPLES = [
    "Quelle part des locaux est éligible à la fibre FttH au 2e trimestre 2026 ?",
    "Montre l'évolution trimestrielle du taux national d'éligibilité à la fibre FttH depuis 2019.",
    "Le taux d'éligibilité FttH des communes est-il corrélé à leur niveau de vie médian ? Donne la corrélation de "
    "Spearman.",
    "Quelle région est la mieux connectée ?",
    "Quel est le prix moyen d'un abonnement fibre en France ?",
]


@st.cache_resource
def database():
    return open_locked_copy()


@st.cache_resource
def reference():
    return reference_transitions()


@st.cache_resource
def risk_model():
    return RiskModel() if MODEL_FILE.exists() else None


# ------------------------------------------------------------------------------------------- drawing

def risk_gauge(risk):
    figure = go.Figure(go.Indicator(
        mode="gauge+number", value=None if risk is None else round(100 * risk),
        number={"suffix": " %", "font": {"size": 34}},
        gauge={"axis": {"range": [0, 100], "tickcolor": INK_2}, "bar": {"color": PALETTE[0]},
               "steps": [{"range": [0, 30], "color": "#e1e0d9"}, {"range": [30, 60], "color": "#f5d9a8"},
                         {"range": [60, 100], "color": "#f2b8a6"}]},
        title={"text": "Risque de réponse fausse", "font": {"size": 15}}))
    figure.update_layout(height=230, margin={"l": 20, "r": 20, "t": 50, "b": 0}, paper_bgcolor=SURFACE)
    return figure


def risk_curve(risks):
    figure = go.Figure(go.Scatter(x=[k for k, _ in risks], y=[100 * r for _, r in risks], mode="lines",
                                  line={"color": PALETTE[0], "width": 2}))
    figure.update_layout(height=170, margin={"l": 40, "r": 10, "t": 10, "b": 30}, paper_bgcolor=SURFACE,
                         plot_bgcolor=SURFACE, yaxis={"range": [0, 100], "title": "%", "gridcolor": "#e1e0d9"},
                         xaxis={"title": "événements terminés"})
    return figure


def events_table(events):
    rows = [{"agent": e.agent, "action": e.action, "durée (s)": round((e.duration_ms or 0) / 1000, 2),
             "ok": "oui" if e.ok else "non",
             "détail": str(e.attributes.get("requete") or e.attributes.get("verdict") or e.attributes.get("erreur")
                           or e.attributes.get("modele") or "")[:120]} for e in events if e.action != "appel_llm"]
    return pd.DataFrame(rows[-14:])


def draw(snapshot, slots, show_risk):
    slots["graph"].plotly_chart(graph_figure(snapshot["edges"], "L'équipe en action", highlight=snapshot["active"],
                                             reference=reference()), width="stretch",
                                key=f"g{time.time()}")
    if show_risk:
        risk = snapshot["risks"][-1][1] if snapshot["risks"] else None
        slots["gauge"].plotly_chart(risk_gauge(risk), width="stretch", key=f"r{time.time()}")
        if len(snapshot["risks"]) > 1:
            slots["curve"].plotly_chart(risk_curve(snapshot["risks"]), width="stretch",
                                        key=f"c{time.time()}")
    with slots["alerts"].container():
        st.markdown("**Alertes**")
        if not snapshot["alerts"]:
            st.caption("Aucune pour l'instant.")
        for alert in snapshot["alerts"][-8:]:
            st.markdown(f"{LEVEL_ICONS[alert.niveau]} {alert.message}")
    table = events_table(snapshot["events"])
    if len(table):
        slots["events"].dataframe(table, hide_index=True, width="stretch")


def layout():
    left, right = st.columns([3, 2])
    slots = {"graph": left.empty(), "events": left.empty()}
    with right:
        slots["gauge"], slots["curve"], slots["alerts"] = st.empty(), st.empty(), st.empty()
    return slots


def show_answer(answer):
    st.subheader("Réponse")
    status = {"repondu": "Réponse", "clarification": "Demande de clarification", "impossible": "Hors des données",
              "echec": "Échec"}.get(answer.statut, answer.statut)
    st.markdown(f"**{status}{'' if answer.approuve else ' (non approuvée par le relecteur)'}** : {answer.reponse}")
    if answer.graphique:
        st.plotly_chart(answer.graphique.figure, width="stretch")
        table = answer.tables.get(answer.graphique.spec.get("tableau"))
        if table is not None:
            with st.expander("Données du graphique"):
                st.dataframe(table.data, hide_index=True)
    tokens_in, tokens_out = answer.tokens
    cost = answer.cout_usd
    st.caption(f"Parcours : {' > '.join(answer.path)} · {answer.latence_s:.1f} s · {tokens_in} + {tokens_out} tokens"
               + (f" · {cost:.4f} $" if cost is not None else ""))
    if answer.tables:
        with st.expander("Requêtes SQL et résultats"):
            for table in answer.tables.values():
                st.code(table.sql, language="sql")
                st.dataframe(table.data.head(50), hide_index=True)


# ------------------------------------------------------------------------------------------- live run

def live_tab():
    if not DB_PATH.exists():
        st.info("La base n'est pas construite : `python scripts/download_data.py` puis "
                "`python scripts/build_database.py`.")
        return
    if not gemini_api_key():
        st.info("Ajoute une clé Gemini (GEMINI_API_KEY ou .streamlit/secrets.toml) pour poser des questions ; "
                "l'onglet « Rejouer un run » fonctionne sans clé.")
        return
    c1, c2, c3 = st.columns(3)
    model = c1.selectbox("Modèle", MODELS, index=0)
    temperature = c2.select_slider("Température", [0.0, 0.5, 1.0], value=0.0)
    prompt = c3.selectbox("Prompt du planificateur", list(PLANNER_PROMPTS))
    example = st.selectbox("Exemples", [""] + EXAMPLES)
    question = st.text_input("Question", value=example)
    if not st.button("Lancer l'équipe", type="primary", disabled=not question.strip()):
        return
    config = {"modele": model, "temperature": temperature, "prompt": prompt}
    monitor = LiveMonitor(config, reference(), risk_model())
    team = Team(chat_model(model, temperature), database().cursor(), planner_prompt=prompt)
    result = queue.Queue()

    def work():
        try:
            result.put(team.ask(question, listeners=[monitor]))
        except Exception as error:  # shown to the user below
            result.put(error)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    slots = layout()
    while thread.is_alive():
        draw(monitor.snapshot(), slots, monitor.risk_model is not None)
        time.sleep(0.4)
    answer = result.get()
    if isinstance(answer, Exception):
        st.error(f"L'équipe a échoué : {answer}")
        return
    monitor.finish(answer)
    draw(monitor.snapshot(), slots, monitor.risk_model is not None)
    show_answer(answer)


# ------------------------------------------------------------------------------------------- replay

def saved_runs():
    paths = sorted((ROOT / "runs" / "experience").glob("*/*.json"))
    return {f"{p.parent.name} · {p.stem}": p for p in paths}


def replay_tab():
    runs = saved_runs()
    if not runs:
        st.info("Aucun run enregistré : lance `python evaluation/run_experiment.py` pour en produire.")
        return
    choice = st.selectbox("Run enregistré", list(runs))
    speed = st.select_slider("Vitesse", [1, 2, 5, 10, 20], value=5)
    record = json.loads(runs[choice].read_text(encoding="utf-8"))
    st.markdown(f"**Question** ({record['question_id']}) : {record['question']}")
    if not st.button("Rejouer", type="primary"):
        return
    monitor = LiveMonitor(record["config"], reference(), risk_model())
    events = [Event(e["id"], e["parent"], e["agent"], e["action"], e["start"], e["end"], e["ok"], e["attributes"])
              for e in record["events"]]
    moments = sorted([(e.start, 0, "debut", e) for e in events] + [(e.end, 1, "fin", e) for e in events],
                     key=lambda m: (m[0], m[1]))
    slots = layout()
    previous = moments[0][0]
    for at, _, moment, event in moments:
        time.sleep(min((at - previous) / speed, 1.5))
        previous = at
        monitor(moment, event)
        draw(monitor.snapshot(), slots, monitor.risk_model is not None)
    monitor.finish()
    draw(monitor.snapshot(), slots, monitor.risk_model is not None)
    verdict = "juste" if record["evaluation"]["juste"] else "fausse"
    st.markdown(f"**Réponse ({record['statut']}, notée {verdict})** : {record['reponse']}")


# ------------------------------------------------------------------------------------------- results

def report_tab(name):
    path = ROOT / "reports" / f"{name}.md"
    if not path.exists():
        st.info(f"Rapport absent : {path.relative_to(ROOT)}.")
        return
    text = path.read_text(encoding="utf-8")
    # images of the report are shown with Streamlit (relative paths)
    for block in text.split("\n\n"):
        if block.startswith("![") and "](" in block:
            image = ROOT / "reports" / block.split("](")[1].rstrip(")")
            if image.exists():
                st.image(str(image))
        else:
            st.markdown(block)


st.title("Relais")
st.caption("Une équipe d'agents LLM analyse la connectivité en France ; on observe son comportement en direct.")
tabs = st.tabs(["En direct", "Rejouer un run", "Expérience", "Process mining", "Prédiction d'échec",
                "Conversion des traces"])
with tabs[0]:
    live_tab()
with tabs[1]:
    replay_tab()
with tabs[2]:
    report_tab("experience")
with tabs[3]:
    report_tab("process_mining")
with tabs[4]:
    report_tab("prediction")
with tabs[5]:
    report_tab("conversion")
