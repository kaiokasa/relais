"""Relais: a conversation with a team of LLM agents, and the observability of every run.

    streamlit run app/dashboard.py --server.port 8510
"""
import html
import json
import sys
import time
import uuid
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from relais.console import agent_colours, risk_curve, risk_gauge, theme, waterfall_figure  # noqa: E402
from relais.jobs import DONE, FAILED, RUNNING, WAITING, JobManager, QueueFull  # noqa: E402
from relais.llm import chat_model, gemini_api_key  # noqa: E402
from relais.mining import graph_figure  # noqa: E402
from relais.monitor import LiveMonitor, reference_transitions  # noqa: E402
from relais.prediction import MODEL_FILE, MODELS, RiskModel  # noqa: E402
from relais.pricing import cost_usd  # noqa: E402
from relais.sources import sources_of  # noqa: E402
from relais.team.graph import Team  # noqa: E402
from relais.team.prompts import PLANNER_PROMPTS  # noqa: E402
from relais.team.trace import Event  # noqa: E402
from relais.tools.charts import apply_theme, render  # noqa: E402
from relais.tools.sql import DB_PATH, SqlTool, open_locked_copy  # noqa: E402

st.set_page_config(page_title="Relais", page_icon=":material/hub:", layout="wide")
UI_COLOURS = {
    "dark": {"text": "#f5f4ef", "text2": "#c2c0b6", "muted": "#9b9890", "surface2": "#30302e", "border": "#3e3e38",
             "accent": "#d97757", "note": "#e0b46a", "alert": "#e66767", "warn": "#c98500"},
    "light": {"text": "#1f1e1d", "text2": "#4a4740", "muted": "#68635a", "surface2": "#ebe4d6", "border": "#d6ccb9",
              "accent": "#b65532", "note": "#8a5a00", "alert": "#c43c3b", "warn": "#a86b00"},
}


def ui_mode():
    """'light' or 'dark', as chosen in the Streamlit menu (or the system setting)."""
    try:
        return "light" if st.context.theme.type == "light" else "dark"
    except Exception:
        return "dark"


MODE = ui_mode()
_c = UI_COLOURS[MODE]
st.markdown(f"""<style>
:root {{--r-text: {_c["text"]}; --r-text2: {_c["text2"]}; --r-muted: {_c["muted"]}; --r-surface2: {_c["surface2"]};
        --r-border: {_c["border"]}; --r-accent: {_c["accent"]}; --r-note: {_c["note"]}; --r-alert: {_c["alert"]};
        --r-warn: {_c["warn"]};}}
.block-container {{padding-top: 2rem; padding-bottom: 7rem;}}
.relais-brand {{font-family: 'Source Serif 4', Georgia, serif; font-size: 1.45rem; font-weight: 600;}}
.relais-greeting {{font-family: 'Source Serif 4', Georgia, serif; font-size: 2.15rem; font-weight: 400;
                  text-align: center; margin: 14vh 0 .4rem; color: var(--r-text);}}
.relais-sub {{text-align: center; color: var(--r-muted); margin-bottom: 1.6rem;}}
.relais-answer {{font-family: 'Source Serif 4', Georgia, serif; font-size: 1.06rem; line-height: 1.72;
                color: var(--r-text); white-space: pre-wrap;}}
.relais-status {{color: var(--r-muted); font-size: .9rem; margin: .1rem 0 .5rem;}}
.relais-steps {{margin: .2rem 0 .6rem;}}
.relais-step {{display: inline-block; padding: .12rem .6rem; border-radius: 999px; border: 1px solid var(--r-border);
              margin: 0 .3rem .3rem 0; font-size: .8rem; color: var(--r-text2);}}
.relais-step.active {{border-color: var(--r-accent); color: var(--r-text); font-weight: 600;}}
.relais-note {{font-size: .86rem; color: var(--r-note); margin-top: .4rem;}}
.relais-sources {{margin: .9rem 0 .4rem; padding-top: .6rem; border-top: 1px solid var(--r-border);}}
.relais-sources-title {{font-size: .75rem; letter-spacing: .04em; text-transform: uppercase; color: var(--r-muted);
                       margin-bottom: .35rem;}}
.relais-source {{font-size: .86rem; color: var(--r-text2); margin-bottom: .3rem; line-height: 1.45;}}
.relais-source b {{color: var(--r-text); font-weight: 600;}}
.relais-source .num {{display: inline-block; min-width: 1.3rem; color: var(--r-muted);}}
.relais-source .meta {{color: var(--r-muted); font-size: .8rem;}}
.relais-recent {{font-size: .85rem; color: var(--r-text2); margin-bottom: .35rem;}}
.relais-alert {{padding: .45rem .7rem; border-radius: 8px; margin-bottom: .35rem; font-size: .86rem;
               background: var(--r-surface2); border-left: 3px solid var(--r-muted); color: var(--r-text);}}
.relais-alert.alerte {{border-left-color: var(--r-alert);}}
.relais-alert.attention {{border-left-color: var(--r-warn);}}
.relais-legend span {{display: inline-block; margin-right: .9rem; font-size: .78rem; color: var(--r-text2);}}
.relais-legend i {{display: inline-block; width: .65rem; height: .65rem; border-radius: 2px; margin-right: .3rem;
                  vertical-align: middle;}}
[data-testid="stMetricValue"] {{font-size: 1.3rem;}}
[data-testid="stMetricLabel"] p {{font-size: .75rem; letter-spacing: .04em; text-transform: uppercase; opacity: .75;}}
@media (prefers-reduced-motion: reduce) {{* {{transition: none !important; animation: none !important;}}}}
</style>""", unsafe_allow_html=True)

EXAMPLES = {
    "Évolution de la fibre": "Montre l'évolution trimestrielle du taux national d'éligibilité à la fibre FttH depuis "
                             "2019.",
    "Rural contre urbain": "Les communes rurales sont-elles moins éligibles à la fibre FttH que les communes urbaines "
                           "denses au 2e trimestre 2026 ?",
    "Sites 5G par opérateur": "Trace l'évolution mensuelle du nombre de sites 5G de chacun des quatre opérateurs de "
                              "métropole.",
    "Fibre et niveau de vie": "Le taux d'éligibilité FttH des communes est-il corrélé à leur niveau de vie médian ? "
                              "Donne la corrélation de Spearman.",
    "Une question ambiguë": "Quelle région est la mieux connectée ?",
}
STATUS_LABELS = {"repondu": "Réponse", "clarification": "Demande de précision", "impossible": "Hors des données",
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


@st.cache_resource
def job_manager():
    """One queue for every person using the app: at most 4 runs at once, the others wait."""
    def team_factory(config):
        return Team(chat_model(config["modele"], config["temperature"]), database().cursor(),
                    planner_prompt=config["prompt"])

    def monitor_factory(config):
        return LiveMonitor(config, reference(), risk_model())

    return JobManager(team_factory, monitor_factory, max_workers=4, max_waiting_per_owner=3, max_queue=20)


def init_session():
    st.session_state.setdefault("owner", uuid.uuid4().hex[:8])
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("modele", MODELS[0])


def ready():
    return bool(DB_PATH.exists() and gemini_api_key())


# ------------------------------------------------------------------------------------------- shared pieces

def usage_of(events, model):
    tokens_in = sum(e.attributes.get("input_tokens", 0) or 0 for e in events if e.action == "appel_llm")
    tokens_out = sum(e.attributes.get("output_tokens", 0) or 0 for e in events if e.action == "appel_llm")
    return tokens_in, tokens_out, cost_usd(model, tokens_in, tokens_out)


def status_text(statut, approuve):
    return STATUS_LABELS.get(statut, statut) + ("" if approuve else " (non approuvée)")


def job_status(job):
    if job.status == DONE and job.answer is not None:
        return status_text(job.answer.statut, job.answer.approuve)
    return {WAITING: "en attente", RUNNING: "en cours", FAILED: "Échec"}.get(job.status, job.status)


def legend():
    st.markdown('<div class="relais-legend">' + "".join(
        f'<span><i style="background:{c}"></i>{a}</span>' for a, c in agent_colours(MODE).items())
        + f'<span><i style="background:{theme(MODE)["failure"]}"></i>échec (✗)</span></div>', unsafe_allow_html=True)


def alerts_html(alerts):
    rows = "".join(f'<div class="relais-alert {a.niveau}">{html.escape(a.message)}</div>' for a in reversed(alerts[-8:]))
    return rows or '<div class="relais-alert">Aucune alerte.</div>'


def observability_panel(snapshot, config, key, finished):
    """Metrics, span waterfall, team graph, risk and alerts of one run (live or finished)."""
    events = snapshot["events"]
    now = time.time()
    started = snapshot["started_at"] or now
    last = max([e.end for e in events if e.end], default=started) if finished else now
    tokens_in, tokens_out, cost = usage_of(events, config.get("modele"))
    risk = snapshot["risks"][-1][1] if snapshot["risks"] else None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Durée", f"{max(last - started, 0):.1f} s")
    c2.metric("Coût", "-" if cost is None else f"{cost:.4f} $")
    c3.metric("Tokens", f"{(tokens_in + tokens_out) / 1000:.1f} k")
    c4.metric("Risque d'échec", "-" if risk is None else f"{100 * risk:.0f} %")
    st.plotly_chart(waterfall_figure(events, snapshot["running"], started, now, mode=MODE), width="stretch",
                    theme=None,
                    key=f"wf{key}")
    legend()
    left, right = st.columns([3, 2])
    with left:
        graph = apply_theme(graph_figure(snapshot["edges"], "L'équipe", highlight=snapshot["active"],
                                         reference=reference()), MODE)
        graph.update_layout(height=300, margin={"l": 5, "r": 5, "t": 35, "b": 5})
        st.plotly_chart(graph, width="stretch", theme=None, key=f"gr{key}")
    with right:
        st.markdown("**Risque de réponse fausse**")
        st.plotly_chart(risk_gauge(risk, MODE), width="stretch", theme=None, key=f"rg{key}")
        if len(snapshot["risks"]) > 1:
            st.plotly_chart(risk_curve(snapshot["risks"], MODE), width="stretch", theme=None, key=f"rc{key}")
    st.markdown("**Alertes**")
    st.markdown(alerts_html(snapshot["alerts"]), unsafe_allow_html=True)


def sources_block(tables):
    """The open datasets behind the answer, derived from the SQL queries that ran (never written by the LLM)."""
    sources = sources_of([t.sql for t in tables], database().cursor() if DB_PATH.exists() else None)
    if not sources:
        return
    rows = []
    for i, source in enumerate(sources, 1):
        day = source.get("telecharge_le")
        meta = " · ".join([source["licence"]] + ([f"téléchargé le {day:%d/%m/%Y}"] if day else [])
                          + [f"tables : {', '.join(source['tables'])}"])
        rows.append(f'<div class="relais-source"><span class="num">{i}.</span><b>{html.escape(source["producteur"])}'
                    f'</b> · <a href="{html.escape(source["url"])}" target="_blank" rel="noopener">'
                    f'{html.escape(source["nom"])}</a><br><span class="num"></span>'
                    f'<span class="meta">{html.escape(meta)}</span></div>')
    st.markdown('<div class="relais-sources"><div class="relais-sources-title">Sources</div>' + "".join(rows)
                + "</div>", unsafe_allow_html=True)


def sql_details(tables):
    if not tables:
        return
    with st.expander(f"Requêtes SQL ({len(tables)})", icon=":material/database:"):
        for table in tables:
            st.code(table.sql, language="sql")
            st.dataframe(table.data.head(50), hide_index=True)


# ------------------------------------------------------------------------------------------- conversation page

def submit(question):
    question = question.strip()[:1000]
    config = {"modele": st.session_state.modele, "temperature": 0.0, "prompt": "standard"}
    st.session_state.messages.append({"role": "user", "text": question})
    try:
        job = job_manager().submit(question, config, st.session_state.owner)
        st.session_state.messages.append({"role": "assistant", "job": job.id})
    except QueueFull as error:
        st.session_state.messages.append({"role": "assistant", "text": str(error)})


def steps_html(path, active):
    chips = "".join(f'<span class="relais-step{" active" if i == len(path) - 1 and agent == active else ""}">'
                    f'{agent}</span>' for i, agent in enumerate(path))
    return f'<div class="relais-steps">{chips}</div>'


def assistant_message(message):
    if message.get("job") is None:
        st.markdown(message["text"])
        return
    job = job_manager().get(message["job"])
    if job is None:
        st.caption("Ce run n'est plus en mémoire.")
        return
    snapshot = job.monitor.snapshot()
    if job.status == WAITING:
        waiting = [j for j in job_manager().list() if j.status == WAITING and j.id <= job.id]
        st.markdown(f'<div class="relais-status">En attente d\'une place libre (position {len(waiting)} dans la '
                    f'file)…</div>', unsafe_allow_html=True)
        return
    if job.status == RUNNING:
        st.markdown(f'<div class="relais-status">L\'équipe travaille… {job.duration():.0f} s</div>'
                    + steps_html(snapshot["path"], snapshot["active"]), unsafe_allow_html=True)
        with st.expander("Déroulé en direct", icon=":material/timeline:"):
            observability_panel(snapshot, job.config, job.id, finished=False)
        return
    if job.status == FAILED:
        st.error(f"L'équipe n'a pas pu répondre : {job.error}")
        return
    answer = job.answer
    if answer.statut != "repondu":
        st.caption(STATUS_LABELS.get(answer.statut, answer.statut))
    st.markdown(f'<div class="relais-answer">{html.escape(answer.reponse)}</div>', unsafe_allow_html=True)
    if not answer.approuve:
        st.markdown('<div class="relais-note">⚠ Réponse donnée sans l\'approbation du relecteur : à vérifier.</div>',
                    unsafe_allow_html=True)
    if answer.graphique is not None:
        st.plotly_chart(apply_theme(answer.graphique.figure, MODE), width="stretch", theme=None, key=f"ch{job.id}")
    sources_block(list(answer.tables.values()))
    with st.expander(f"Comment l'équipe a travaillé · {' → '.join(answer.path)}", icon=":material/account_tree:"):
        observability_panel(snapshot, job.config, job.id, finished=True)
    sql_details(list(answer.tables.values()))


@st.fragment(run_every=1.0)
def conversation():
    for message in st.session_state.messages:
        if message["role"] == "user":
            with st.chat_message("user", avatar=":material/person:"):
                st.markdown(message["text"])
        else:
            with st.chat_message("assistant", avatar=":material/hub:"):
                assistant_message(message)


def sidebar_common():
    st.markdown('<div class="relais-brand">Relais</div>', unsafe_allow_html=True)
    st.caption("Une équipe d'agents qui analyse la connectivité en France")


def chat_page():
    init_session()
    with st.sidebar:
        sidebar_common()
        if st.button("Nouvelle conversation", icon=":material/add:", width="stretch"):
            st.session_state.messages = []
            st.rerun()
        with st.expander("Réglages", icon=":material/tune:"):
            st.selectbox("Modèle", MODELS, key="modele")
            st.caption("Les réglages d'expérience (température, instructions du planificateur) sont dans "
                       "Supervision.")
        questions = [m["text"] for m in st.session_state.messages if m["role"] == "user"]
        if questions:
            st.caption("DANS CETTE CONVERSATION")
            for question in reversed(questions[-8:]):
                st.markdown(f"<div class='relais-recent'>{html.escape(question[:70])}</div>",
                            unsafe_allow_html=True)
        counts = job_manager().counts()
        st.caption(f"{counts[RUNNING]} run(s) en cours et {counts[WAITING]} en attente dans l'application.")

    _, column, _ = st.columns([1, 3, 1])
    with column:
        if not st.session_state.messages:
            st.markdown('<div class="relais-greeting">Que veux-tu savoir sur la connectivité en France ?</div>'
                        '<div class="relais-sub">Fibre, débits, sites mobiles et 5G, pannes, revenus des communes : '
                        'données ouvertes de l\'ARCEP et de l\'INSEE.</div>', unsafe_allow_html=True)
            rows = [list(EXAMPLES)[:3], list(EXAMPLES)[3:]]
            for row in rows:
                for col, label in zip(st.columns(len(row)), row):
                    col.button(label, key=f"example-{label}", width="stretch", disabled=not ready(),
                               on_click=submit, args=(EXAMPLES[label],))
            if not ready():
                st.caption("Base ou clé Gemini absente : la page Supervision permet de rejouer des runs enregistrés.")
        else:
            conversation()
    # the question is sent by the callback, once per submission, before the page is drawn again
    st.chat_input("Pose une question sur la connectivité en France", key="chat", disabled=not ready(),
                  on_submit=lambda: submit(st.session_state.chat) if st.session_state.chat else None)


# ------------------------------------------------------------------------------------------- supervision page

def job_label(job, owner):
    """A label that does not change while the run goes on (the selection must survive the refreshes)."""
    who = "toi" if job.owner == owner else "autre"
    return f"#{job.id} · {who} · {job.question[:60]}"


def jobs_table(jobs, owner):
    now = time.time()
    rows = []
    for job in jobs[:15]:
        risks = job.monitor.risks
        _, _, cost = usage_of(job.monitor.events, job.config.get("modele"))
        rows.append({"run": f"#{job.id}", "état": job_status(job), "par": "toi" if job.owner == owner else "autre",
                     "question": job.question[:70], "durée (s)": round(job.duration(now), 1),
                     "risque": "-" if not risks else f"{100 * risks[-1][1]:.0f} %",
                     "coût ($)": "-" if cost is None else f"{cost:.4f}"})
    return rows


@st.fragment(run_every=1.0)
def supervision():
    jobs = job_manager().list()
    counts = job_manager().counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("En cours", counts[RUNNING])
    c2.metric("En attente", counts[WAITING])
    c3.metric("Terminés", counts[DONE])
    c4.metric("Échecs", counts[FAILED])
    if not jobs:
        st.caption("Aucun run pour l'instant : les questions posées dans Conversation, par toutes les personnes qui "
                   "utilisent l'application, apparaissent ici en direct.")
        return
    owner = st.session_state.owner
    st.dataframe(jobs_table(jobs, owner), hide_index=True, width="stretch")
    ids = [j.id for j in jobs]
    wanted = st.session_state.get("selected_job")
    chosen = st.selectbox("Run suivi", ids, index=ids.index(wanted) if wanted in ids else 0,
                          format_func=lambda i: job_label(job_manager().get(i), owner))
    st.session_state.selected_job = chosen
    job = job_manager().get(chosen)
    st.markdown(f"**{html.escape(job.question)}**")
    if job.status == WAITING:
        st.caption("En attente d'une place libre.")
        return
    observability_panel(job.monitor.snapshot(), job.config, f"s{job.id}", finished=not job.active)
    if job.status == DONE:
        st.markdown(f'<div class="relais-answer">{html.escape(job.answer.reponse)}</div>', unsafe_allow_html=True)
        sources_block(list(job.answer.tables.values()))
    elif job.status == FAILED:
        st.error(f"L'équipe n'a pas pu répondre : {job.error}")


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


def replay(path, speed):
    record = json.loads(path.read_text(encoding="utf-8"))
    config = record["config"]
    monitor = LiveMonitor(config, reference(), risk_model())
    events = [Event(e["id"], e["parent"], e["agent"], e["action"], e["start"], e["end"], e["ok"], e["attributes"])
              for e in record["events"]]
    moments = sorted([(e.start, 0, "debut", e) for e in events] + [(e.end, 1, "fin", e) for e in events],
                     key=lambda m: (m[0], m[1]))
    st.markdown(f"**{record['question_id']}** · {html.escape(record['question'])}")
    area = st.empty()
    previous = moments[0][0]
    for step, (at, _, moment, event) in enumerate(moments):
        time.sleep(min((at - previous) / speed, 1.2))
        previous = at
        monitor(moment, event)
        with area.container():
            observability_panel(monitor.snapshot(), config, f"rp{step}", finished=False)
    monitor.finish()
    with area.container():
        observability_panel(monitor.snapshot(), config, "rpfin", finished=True)
    verdict = "juste" if record["evaluation"]["juste"] else "fausse"
    st.caption(f"{STATUS_LABELS.get(record['statut'], record['statut'])} · notée {verdict}")
    st.markdown(f'<div class="relais-answer">{html.escape(record["reponse"])}</div>', unsafe_allow_html=True)
    chart, tables = redraw_chart(record)
    if chart is not None:
        st.plotly_chart(apply_theme(chart.figure, MODE), width="stretch", theme=None)
    sources_block(tables)
    sql_details(tables)


PROMPT_LABELS = {"standard": "Instructions complètes : règles de fond et guide de décision (celles de la "
                             "conversation)",
                 "concis": "Instructions minimales : rôle et données seulement, sans règles (pour l'expérience)"}


def experimental_run():
    """Launch a run with the factors of the variability experiment; it joins the shared queue."""
    st.caption("Pour reproduire l'expérience de variabilité : la même question avec un autre modèle, une autre "
               "température ou d'autres instructions pour le planificateur. Le run rejoint la file commune et se "
               "suit dans l'onglet « Runs de l'application ».")
    with st.form("experimental", border=True):
        question = st.text_area("Question", height=90)
        c1, c2 = st.columns(2)
        model = c1.selectbox("Modèle", MODELS)
        temperature = c2.select_slider("Température", [0.0, 0.5, 1.0], value=0.0,
                                       help="0 : l'équipe suit presque toujours le même parcours ; 1 : les "
                                            "parcours varient d'un run à l'autre.")
        prompt = st.radio("Instructions du planificateur", list(PLANNER_PROMPTS),
                          format_func=lambda p: PROMPT_LABELS.get(p, p))
        sent = st.form_submit_button("Lancer le run", type="primary", icon=":material/science:",
                                     disabled=not ready())
    if sent and question.strip():
        try:
            job = job_manager().submit(question.strip()[:1000], {"modele": model, "temperature": temperature,
                                                                 "prompt": prompt}, st.session_state.owner)
            st.session_state.selected_job = job.id
            st.success(f"Run #{job.id} lancé : il apparaît dans l'onglet « Runs de l'application ».")
        except QueueFull as error:
            st.error(str(error))


def supervision_page():
    init_session()
    with st.sidebar:
        sidebar_common()
    st.markdown("## Supervision")
    live, experiment, past = st.tabs(["Runs de l'application", "Run expérimental", "Rejouer un run enregistré"])
    with live:
        supervision()
    with experiment:
        experimental_run()
    with past:
        runs = saved_runs()
        if not runs:
            st.caption("Aucun run enregistré (lancer `python evaluation/run_experiment.py`).")
        else:
            c1, c2, c3 = st.columns([3, 1, 1])
            choice = c1.selectbox("Run enregistré", list(runs))
            speed = c2.select_slider("Vitesse", [1, 2, 5, 10, 20], value=5)
            c3.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
            if c3.button("Rejouer", type="primary", icon=":material/play_arrow:", width="stretch"):
                replay(runs[choice], speed)


# ------------------------------------------------------------------------------------------- analyses page

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
    init_session()
    with st.sidebar:
        sidebar_common()
    st.markdown("## Analyses")
    tabs = st.tabs(["Expérience de variabilité", "Process mining", "Prédiction d'échec", "Conversion des traces"])
    for tab, name in zip(tabs, ("experience", "process_mining", "prediction", "conversion")):
        with tab:
            report(name)


navigation = st.navigation([st.Page(chat_page, title="Conversation", icon=":material/chat:", default=True),
                            st.Page(supervision_page, title="Supervision", icon=":material/monitoring:"),
                            st.Page(analyses_page, title="Analyses", icon=":material/insights:")])
navigation.run()
