"""Relais: an observability console for a team of LLM agents.

    streamlit run app/dashboard.py --server.port 8510
"""
import json
import queue
import sys
import threading
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from relais.console import AGENT_COLOURS, risk_curve, risk_gauge, waterfall_figure  # noqa: E402
from relais.llm import chat_model, gemini_api_key  # noqa: E402
from relais.mining import graph_figure  # noqa: E402
from relais.monitor import LiveMonitor, reference_transitions  # noqa: E402
from relais.prediction import MODEL_FILE, MODELS, RiskModel  # noqa: E402
from relais.pricing import cost_usd  # noqa: E402
from relais.team.graph import Team  # noqa: E402
from relais.team.prompts import PLANNER_PROMPTS  # noqa: E402
from relais.team.trace import Event  # noqa: E402
from relais.tools.charts import render, to_dark  # noqa: E402
from relais.tools.sql import DB_PATH, SqlTool, open_locked_copy  # noqa: E402

st.set_page_config(page_title="Relais", page_icon="📡", layout="wide")
st.markdown("""<style>
.block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
[data-testid="stMetricValue"] {font-size: 1.45rem;}
[data-testid="stMetricLabel"] p {font-size: 0.78rem; text-transform: uppercase; letter-spacing: .05em; opacity: .75;}
.relais-brand {font-size: 1.5rem; font-weight: 700; letter-spacing: .08em;}
.relais-alert {padding: .45rem .7rem; border-radius: 6px; margin-bottom: .35rem; font-size: .88rem;
               background: #222220; border-left: 3px solid #898781;}
.relais-alert.alerte {border-left-color: #e66767;}
.relais-alert.attention {border-left-color: #c98500;}
.relais-legend span {display: inline-block; margin-right: .9rem; font-size: .8rem;}
.relais-legend i {display: inline-block; width: .7rem; height: .7rem; border-radius: 2px; margin-right: .3rem;
                  vertical-align: middle;}
</style>""", unsafe_allow_html=True)

EXAMPLES = [
    "Montre l'évolution trimestrielle du taux national d'éligibilité à la fibre FttH depuis 2019.",
    "Les communes rurales sont-elles moins éligibles à la fibre FttH que les communes urbaines denses au 2e "
    "trimestre 2026 ?",
    "Trace l'évolution mensuelle du nombre de sites 5G de chacun des quatre opérateurs de métropole.",
    "Le taux d'éligibilité FttH des communes est-il corrélé à leur niveau de vie médian ? Donne la corrélation de "
    "Spearman.",
    "Quelle région est la mieux connectée ?",
    "Quel est le prix moyen d'un abonnement fibre en France ?",
]
STATUS_LABELS = {"repondu": "Réponse", "clarification": "Clarification", "impossible": "Hors des données",
                 "echec": "Échec", "erreur": "Erreur"}


@st.cache_resource
def database():
    return open_locked_copy()


@st.cache_resource
def reference():
    return reference_transitions()


@st.cache_resource
def risk_model():
    return RiskModel() if MODEL_FILE.exists() else None


# ------------------------------------------------------------------------------------------- layout

def console_layout():
    cards = st.columns(5)
    slots = {}
    for name, card in zip(("statut", "duree", "cout", "tokens", "risque"), cards):
        with card.container(border=True):
            slots[name] = st.empty()
    left, right = st.columns([3, 2], gap="medium")
    with left:
        with st.container(border=True):
            slots["waterfall"] = st.empty()
            st.markdown('<div class="relais-legend">' + "".join(
                f'<span><i style="background:{c}"></i>{a}</span>' for a, c in AGENT_COLOURS.items())
                + '<span><i style="background:#e66767"></i>échec</span></div>', unsafe_allow_html=True)
    with right:
        with st.container(border=True):
            st.markdown("**Risque de réponse fausse**")
            slots["gauge"], slots["curve"] = st.empty(), st.empty()
        with st.container(border=True):
            slots["graph"] = st.empty()
        with st.container(border=True):
            st.markdown("**Alertes**")
            slots["alerts"] = st.empty()
    return slots


def usage_of(events, model):
    tokens_in = sum(e.attributes.get("input_tokens", 0) or 0 for e in events if e.action == "appel_llm")
    tokens_out = sum(e.attributes.get("output_tokens", 0) or 0 for e in events if e.action == "appel_llm")
    return tokens_in, tokens_out, cost_usd(model, tokens_in, tokens_out)


def draw(snapshot, slots, config, status="en cours"):
    events = snapshot["events"]
    now = time.time()
    started = snapshot["started_at"] or now
    ends = [e.end for e in events if e.end]
    last = now if not snapshot["finished"] else max(ends, default=started)
    tokens_in, tokens_out, cost = usage_of(events, config.get("modele"))
    risk = snapshot["risks"][-1][1] if snapshot["risks"] else None
    key = f"{now:.3f}"
    slots["statut"].metric("Statut", status)
    slots["duree"].metric("Durée", f"{max(last - started, 0):.1f} s")
    slots["cout"].metric("Coût", "-" if cost is None else f"{cost:.4f} $")
    slots["tokens"].metric("Tokens", f"{(tokens_in + tokens_out) / 1000:.1f} k")
    slots["risque"].metric("Risque d'échec", "-" if risk is None else f"{100 * risk:.0f} %")
    slots["waterfall"].plotly_chart(waterfall_figure(events, snapshot["running"], started, now),
                                    width="stretch", theme=None, key=f"w{key}")
    slots["gauge"].plotly_chart(risk_gauge(risk), width="stretch", theme=None, key=f"r{key}")
    if len(snapshot["risks"]) > 1:
        slots["curve"].plotly_chart(risk_curve(snapshot["risks"]), width="stretch", theme=None, key=f"c{key}")
    graph = to_dark(graph_figure(snapshot["edges"], "L'équipe", highlight=snapshot["active"], reference=reference()))
    graph.update_layout(height=300, margin={"l": 5, "r": 5, "t": 35, "b": 5})
    slots["graph"].plotly_chart(graph, width="stretch", theme=None, key=f"g{key}")
    alerts = snapshot["alerts"][-8:]
    html = "".join(f'<div class="relais-alert {a.niveau}">{a.message}</div>' for a in reversed(alerts))
    slots["alerts"].markdown(html or '<div class="relais-alert">Aucune alerte.</div>', unsafe_allow_html=True)


def status_text(statut, approuve):
    return STATUS_LABELS.get(statut, statut) + ("" if approuve else " ⚠")


def show_answer(statut, approuve, reponse, chart=None, tables=None, path=None):
    with st.container(border=True):
        label = STATUS_LABELS.get(statut, statut)
        st.markdown(f"#### {label}" + ("" if approuve else " · non approuvée par le relecteur"))
        st.markdown(reponse)
        if path:
            st.caption("Parcours : " + " → ".join(path))
    if chart is not None:
        with st.container(border=True):
            st.plotly_chart(to_dark(chart.figure), width="stretch", theme=None)
    if tables:
        with st.expander(f"Requêtes SQL et résultats ({len(tables)})"):
            for table in tables:
                st.code(table.sql, language="sql")
                st.dataframe(table.data.head(50), hide_index=True)


# ------------------------------------------------------------------------------------------- live run

def run_live(question, config):
    monitor = LiveMonitor(config, reference(), risk_model())
    team = Team(chat_model(config["modele"], config["temperature"]), database().cursor(),
                planner_prompt=config["prompt"])
    result = queue.Queue()

    def work():
        try:
            result.put(team.ask(question, listeners=[monitor]))
        except Exception as error:  # shown to the user below
            result.put(error)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    slots = console_layout()
    while thread.is_alive():
        draw(monitor.snapshot(), slots, config)
        time.sleep(0.35)
    answer = result.get()
    if isinstance(answer, Exception):
        draw(monitor.snapshot(), slots, config, "Erreur")
        st.error(f"L'équipe a échoué : {answer}")
        return
    monitor.finish(answer)
    snapshot = monitor.snapshot()
    status = status_text(answer.statut, answer.approuve)
    draw(snapshot, slots, config, status)
    shown = (answer.statut, answer.approuve, answer.reponse, answer.graphique, list(answer.tables.values()),
             answer.path)
    show_answer(*shown)
    st.session_state.history.insert(0, {"label": question, "config": config, "snapshot": snapshot,
                                        "status": status, "answer": shown})


# ------------------------------------------------------------------------------------------- replay

def saved_runs():
    paths = sorted((ROOT / "runs" / "experience").glob("*/*.json"))
    return {f"{p.stem} · {p.parent.name}": p for p in paths}


def redraw_chart(record):
    """The chart of a saved run, drawn again by running its SQL queries on the local database (no API call)."""
    if not DB_PATH.exists():
        return None, []
    tool = SqlTool(database().cursor())
    for query in record.get("requetes", []):
        try:
            tool.run(query["sql"])
        except Exception:
            return None, list(tool.tables.values())
    if not record.get("graphique"):
        return None, list(tool.tables.values())
    try:
        return render(record["graphique"]["spec"], tool.tables), list(tool.tables.values())
    except Exception:
        return None, list(tool.tables.values())


def run_replay(path, speed):
    record = json.loads(path.read_text(encoding="utf-8"))
    config = record["config"]
    monitor = LiveMonitor(config, reference(), risk_model())
    events = [Event(e["id"], e["parent"], e["agent"], e["action"], e["start"], e["end"], e["ok"], e["attributes"])
              for e in record["events"]]
    moments = sorted([(e.start, 0, "debut", e) for e in events] + [(e.end, 1, "fin", e) for e in events],
                     key=lambda m: (m[0], m[1]))
    st.markdown(f"**{record['question_id']}** · {record['question']}")
    slots = console_layout()
    previous = moments[0][0]
    for at, _, moment, event in moments:
        time.sleep(min((at - previous) / speed, 1.2))
        previous = at
        monitor(moment, event)
        draw(monitor.snapshot(), slots, config)
    monitor.finish()
    snapshot = monitor.snapshot()
    verdict = "juste" if record["evaluation"]["juste"] else "fausse"
    status = f"{STATUS_LABELS.get(record['statut'], record['statut'])} ({verdict})"
    draw(snapshot, slots, config, status)
    chart, tables = redraw_chart(record)
    shown = (record["statut"], record.get("approuve", True), record["reponse"], chart, tables, record.get("path"))
    show_answer(*shown)
    st.session_state.history.insert(0, {"label": f"[rejeu] {record['question']}", "config": config,
                                        "snapshot": snapshot, "status": status, "answer": shown})


def show_history(item):
    st.markdown(f"**{item['label']}**")
    slots = console_layout()
    draw(item["snapshot"], slots, item["config"], item["status"])
    show_answer(*item["answer"])


# ------------------------------------------------------------------------------------------- pages

def console_page():
    if "history" not in st.session_state:
        st.session_state.history = []
    with st.sidebar:
        st.markdown('<div class="relais-brand">📡 RELAIS</div>', unsafe_allow_html=True)
        st.caption("Console d'observabilité d'une équipe d'agents LLM")
        mode = st.segmented_control("Mode", ["Nouvelle question", "Rejouer un run"], default="Nouvelle question",
                                    label_visibility="collapsed")
        launch = replay = None
        if mode == "Rejouer un run":
            runs = saved_runs()
            if runs:
                choice = st.selectbox("Run enregistré", list(runs))
                speed = st.select_slider("Vitesse", [1, 2, 5, 10, 20], value=5)
                if st.button("Rejouer", type="primary", width="stretch"):
                    replay = (runs[choice], speed)
            else:
                st.caption("Aucun run enregistré (lancer `python evaluation/run_experiment.py`).")
        else:
            ready = bool(DB_PATH.exists() and gemini_api_key())
            model = st.selectbox("Modèle", MODELS)
            c1, c2 = st.columns(2)
            temperature = c1.selectbox("Température", [0.0, 0.5, 1.0])
            prompt = c2.selectbox("Prompt", list(PLANNER_PROMPTS))
            example = st.selectbox("Exemples", [""] + EXAMPLES,
                                   format_func=lambda e: (e[:58] + "…" if len(e) > 58 else e) or "Choisir un exemple")
            question = st.text_area("Question", value=example, height=110)
            if st.button("Lancer l'équipe", type="primary", width="stretch",
                         disabled=not (ready and question.strip())):
                launch = (question.strip(), {"modele": model, "temperature": temperature, "prompt": prompt})
            if not ready:
                st.caption("Base ou clé Gemini absente : le mode « Rejouer un run » fonctionne sans.")
        selected = None
        if st.session_state.history:
            st.divider()
            st.caption("RUNS DE LA SESSION")
            selected = st.radio("Historique", list(range(len(st.session_state.history))),
                                label_visibility="collapsed",
                                format_func=lambda i: f"{st.session_state.history[i]['status']} · "
                                                      f"{st.session_state.history[i]['label'][:40]}")
    if launch:
        st.markdown(f"**{launch[0]}**")
        run_live(*launch)
    elif replay:
        run_replay(*replay)
    elif selected is not None:
        show_history(st.session_state.history[selected])
    else:
        st.markdown("### Observer une équipe d'agents en direct")
        st.markdown("Pose une question dans la barre latérale, ou rejoue un run enregistré. Pendant le run : la "
                    "cascade des spans (chaque tour d'agent, appel au modèle et appel d'outil), le graphe de "
                    "l'équipe avec les écarts au modèle de référence, les alertes et le risque de réponse fausse "
                    "mis à jour à chaque événement.")


def report(name):
    path = ROOT / "reports" / f"{name}.md"
    if not path.exists():
        st.caption(f"Rapport absent : {path.relative_to(ROOT)}.")
        return
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        if block.startswith("![") and "](" in block:
            image = ROOT / "reports" / block.split("](")[1].rstrip(")")
            if image.exists():
                st.image(str(image))
        else:
            st.markdown(block)


def analyses_page():
    st.markdown("### Analyses")
    tabs = st.tabs(["Expérience de variabilité", "Process mining", "Prédiction d'échec", "Conversion des traces"])
    for tab, name in zip(tabs, ("experience", "process_mining", "prediction", "conversion")):
        with tab:
            report(name)


navigation = st.navigation([st.Page(console_page, title="Console", icon="📡", default=True),
                            st.Page(analyses_page, title="Analyses", icon="📊")])
navigation.run()
