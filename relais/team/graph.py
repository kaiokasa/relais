"""The multi-agent team, as a LangGraph graph.

    planificateur -> (sql | statistiques | visualisation)* -> planificateur -> relecteur -> fin
                                                                  ^               |
                                                                  +--- à corriger +

- The planner has no tool: it splits the question into steps for the agents, or asks for a clarification, or
  explains that the data cannot answer; once the steps are done, it writes the answer from their results.
- Each worker only has ITS tool: the SQL agent the locked SQL tool, the statistics agent the Python sandbox, the
  visualisation agent the chart renderer.
- The reviewer first checks with code that every number of the answer comes from a result, then asks the LLM about
  the reasoning (granularity, causality, neutrality). A rejected answer goes back to the planner, twice at most.

Every turn, LLM call and tool call is recorded (relais/team/trace.py).
"""
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph

from relais.llm import text_of, token_usage
from relais.pricing import cost_usd
from relais.schema import dictionary_rows
from relais.team import interprete, prompts
from relais.team.numbers import AllowedNumbers, unsupported_numbers
from relais.team.trace import Recorder
from relais.telemetry import SpanBridge
from relais.tools.charts import ChartError, render
from relais.tools.sandbox import run_python
from relais.tools.sql import QueryFailed, QueryRejected, SqlTool
from relais.tools.values import ValueIndex

WORKERS = ("sql", "statistiques", "visualisation")
ROLES = ("planificateur", "interprete", *WORKERS, "relecteur")
MAX_ROUNDS = 3       # the first plan, then at most 2 requests for more steps
MAX_STEPS = 8        # worker steps in a run
MAX_REVIEWS = 2      # after 2 rejections the answer is given as it is, marked as not approved
MAX_TOOL_CALLS = {"sql": 6, "statistiques": 4, "visualisation": 3}
CHART_WORDS = re.compile(r"\b(montre|trace|graphique|représente|visualis|dessine)", re.IGNORECASE)
DOCUMENTATION = "\n".join(text for _, _, text in dictionary_rows())


class TeamState(TypedDict, total=False):
    question: str
    phase: str            # "planifier" or "rediger"
    etapes: list          # [{"agent": ..., "consigne": ...}]
    index: int            # next step to run
    rounds: int
    brouillon: dict | None
    retours: list         # the problems found by the reviewer
    reviews: int
    final: dict | None
    interpretations: list  # the readings chosen by the interpreter for the ambiguous terms
    interprete_fait: bool


@dataclass
class TeamAnswer:
    question: str
    statut: str
    reponse: str
    approuve: bool
    problemes: list
    path: list
    graphique: object | None
    tables: dict
    statistiques: list
    events: list
    usage: list
    latence_s: float
    etapes: list = field(default_factory=list)
    run_id: str = ""

    @property
    def tokens(self):
        return (sum(u["input_tokens"] for u in self.usage), sum(u["output_tokens"] for u in self.usage))

    @property
    def cout_usd(self):
        costs = [cost_usd(u["model"], u["input_tokens"], u["output_tokens"]) for u in self.usage]
        return None if any(c is None for c in costs) else sum(costs)


def parse_json(text):
    """The JSON object of an LLM answer (code fences and text around it are tolerated)."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("aucun objet JSON")
    return json.loads(text[start:end + 1])


def check_plan(decision):
    """The planner's decision, validated (ValueError with the reason otherwise)."""
    statut = decision.get("statut")
    if statut in ("clarification", "impossible", "repondu"):
        if not str(decision.get("reponse", "")).strip():
            raise ValueError("le champ reponse est vide")
        return {"statut": statut, "reponse": str(decision["reponse"]).strip()}
    if statut != "a_traiter":
        raise ValueError(f"statut inconnu : {statut!r}")
    steps = decision.get("etapes")
    if not isinstance(steps, list) or not steps:
        raise ValueError("aucune étape")
    clean = []
    for step in steps:
        if not isinstance(step, dict) or step.get("agent") not in WORKERS or not str(step.get("consigne", "")).strip():
            raise ValueError(f"étape invalide : {step!r} (agents possibles : {', '.join(WORKERS)})")
        clean.append({"agent": step["agent"], "consigne": str(step["consigne"]).strip()})
    return {"statut": "a_traiter", "etapes": clean}


def model_name(llm):
    return getattr(llm, "model", None) or getattr(llm, "model_name", None) or type(llm).__name__


class Run:
    """Everything one question needs: the tools, what the agents produced, the trace."""

    def __init__(self, team, question, bridge=None, listeners=()):
        self.team, self.question = team, question
        self.sql = SqlTool(team.con, timeout_s=team.sql_timeout_s, values=team.values)
        self.charts, self.stats, self.reports = [], [], []
        self.recorder = Recorder(bridge, listeners)
        self.usage = []

    # ------------------------------------------------------------------------------------------ LLM calls

    def call_llm(self, role, llm, messages):
        with self.recorder.span(role, "appel_llm", modele=model_name(self.team.llms[role])) as event:
            reply = llm.invoke(messages)
            tokens_in, tokens_out = token_usage(reply)
            event.attributes.update(input_tokens=tokens_in, output_tokens=tokens_out)
        self.usage.append({"agent": role, "model": model_name(self.team.llms[role]), "input_tokens": tokens_in,
                           "output_tokens": tokens_out})
        return reply

    def ask_json(self, role, messages, check):
        """An LLM answer parsed as JSON and checked; one retry with the error, then ValueError."""
        for attempt in range(2):
            reply = self.call_llm(role, self.team.llms[role], messages)
            try:
                return check(parse_json(text_of(reply)))
            except (ValueError, json.JSONDecodeError) as error:
                if attempt == 1:
                    raise ValueError(f"réponse invalide du {role} : {error}") from None
                messages = messages + [reply, HumanMessage(f"Réponse invalide ({error}). Renvoie uniquement l'objet "
                                                           "JSON demandé.")]

    def tool_loop(self, role, system, request, tool, execute):
        """A worker: LLM <-> its single tool, until it answers without calling the tool (or the limit)."""
        llm = self.team.llms[role].bind_tools([tool])
        messages = [SystemMessage(system), HumanMessage(request)]
        calls, limit = 0, MAX_TOOL_CALLS[role]
        while True:
            reply = self.call_llm(role, llm, messages)
            messages.append(reply)
            if not reply.tool_calls:
                return text_of(reply).strip()
            for call in reply.tool_calls:
                calls += 1
                if calls > limit:
                    content = "Limite d'appels atteinte : n'appelle plus l'outil et résume ce que tu as obtenu."
                else:
                    content = execute(call.get("args", {}))
                messages.append(ToolMessage(content, tool_call_id=call.get("id") or str(calls), name=call["name"]))
            if calls > limit + 2:
                return "Limite d'appels atteinte."

    # ------------------------------------------------------------------------------------------ the tools

    def execute_sql(self, args):
        query = str(args.get("requete", "")).strip()
        with self.recorder.span("sql", "executer_sql", requete=query) as event:
            if any(query == t.sql for t in self.sql.tables.values()):
                event.ok = False
                event.attributes["erreur"] = "requête déjà exécutée"
                return "Cette requête a déjà été exécutée : utilise son résultat ci-dessus ou écris-en une autre."
            try:
                table = self.sql.run(query)
            except (QueryRejected, QueryFailed) as error:
                event.ok = False
                event.attributes["erreur"] = str(error)
                return f"Requête refusée ou en échec : {error}"
            event.attributes.update(tableau=table.id, lignes=len(table.data), duree_sql_ms=round(table.elapsed_ms, 1))
            if table.corrections:
                event.attributes["valeurs_corrigees"] = table.corrections
            if table.unknown_values:
                event.attributes["valeurs_inconnues"] = table.unknown_values
            return table.preview()

    def tables_description(self):
        if not self.sql.tables:
            return "(aucun tableau : l'agent SQL n'a rien produit)"
        parts = []
        for table in self.sql.tables.values():
            columns = ", ".join(f"{c} ({t})" for c, t in table.data.dtypes.astype(str).items())
            parts.append(f"- {table.id} : {len(table.data)} lignes ; colonnes {columns}\n"
                         + table.data.head(3).to_string(index=False, max_colwidth=30))
        return "\n".join(parts)

    def execute_python(self, args):
        code = str(args.get("code", ""))
        with self.recorder.span("statistiques", "executer_python", code=code) as event:
            frames = {table.id: table.data for table in self.sql.tables.values()}
            result = run_python(code, frames, timeout_s=self.team.python_timeout_s)
            event.ok = result.ok
            event.attributes.update(duree_python_ms=result.duree_ms, timeout=result.timeout)
            if not result.ok:
                event.attributes["erreur"] = (result.erreur or "")[-500:]
            else:
                self.stats.append({"code": code, "resultat": result.resultat, "sortie": result.sortie})
            return result.summary()

    def make_chart(self, args):
        spec = {k: v for k, v in args.items() if v not in (None, "")}
        with self.recorder.span("visualisation", "creer_graphique", specification=json.dumps(spec, ensure_ascii=False)
                                ) as event:
            try:
                chart = render(spec, self.sql.tables)
            except ChartError as error:
                event.ok = False
                event.attributes["erreur"] = str(error)
                return f"Graphique refusé : {error}"
            self.charts.append(chart)
            event.attributes.update(type=chart.type, series=chart.series, points=chart.points)
            return f"Graphique créé : {chart.type}, {chart.series} série(s), {chart.points} points."

    # ------------------------------------------------------------------------------------------ results as text

    def results_text(self):
        if not self.reports:
            return "(aucune étape exécutée)"
        parts = []
        for i, report in enumerate(self.reports, 1):
            parts.append(f"Étape {i} ({report['agent']}) : {report['consigne']}\nRésumé de l'agent : {report['texte']}")
        if self.sql.tables:
            parts.append("Tableaux obtenus :\n" + "\n".join(t.preview() for t in self.sql.tables.values()))
        for stat in self.stats:
            parts.append("Résultat statistique : " + json.dumps(stat["resultat"], ensure_ascii=False)[:3000]
                         + (f"\nSortie : {stat['sortie'][:1500]}" if stat["sortie"].strip() else ""))
        for chart in self.charts:
            parts.append(f"Graphique : {chart.type}, « {chart.spec.get('titre', '')} », {chart.series} série(s).")
        return "\n\n".join(parts)

    def allowed_numbers(self):
        """The numbers an answer may quote: the results, the question, and the documentation of the data (years
        of the sources, thresholds such as 512 kbit/s or 60 % of the median, frequency bands)."""
        frames = [t.data for t in self.sql.tables.values()]
        objects = [s["resultat"] for s in self.stats] + [s["sortie"] for s in self.stats] + [DOCUMENTATION]
        return AllowedNumbers.from_sources(self.question, frames, objects)


class Team:
    """The team. `llms` gives a chat model per role (or one model for every role)."""

    def __init__(self, llms, con, planner_prompt="standard", sql_timeout_s=20, python_timeout_s=20):
        self.llms = llms if isinstance(llms, dict) else {role: llms for role in ROLES}
        missing = [role for role in ROLES if role not in self.llms]
        if missing:
            raise ValueError(f"no model for {missing}")
        if planner_prompt not in prompts.PLANNER_PROMPTS:
            raise ValueError(f"unknown planner prompt {planner_prompt}")
        self.con, self.planner_prompt = con, planner_prompt
        self.values = ValueIndex.from_connection(con)  # synonyms and exact values for the SQL tool
        self.sql_timeout_s, self.python_timeout_s = sql_timeout_s, python_timeout_s

    def build_graph(self, run):
        planner_system = prompts.PLANNER_PROMPTS[self.planner_prompt]

        def planificateur(state: TeamState):
            phase = state.get("phase", "planifier")
            feedback = ""
            if state.get("retours"):
                feedback = ("\nLe relecteur a refusé la réponse précédente pour ces raisons :\n- "
                            + "\n- ".join(state["retours"]) + "\nCorrige-les.\n")
            with run.recorder.span("planificateur", "rediger" if phase == "rediger" else "planifier"):
                if phase == "planifier":
                    messages = [SystemMessage(planner_system), HumanMessage(state["question"] + feedback)]
                else:
                    more_allowed = state.get("rounds", 0) < MAX_ROUNDS and len(state.get("etapes", [])) < MAX_STEPS
                    request = prompts.WRITE_PROMPT.format(question=state["question"], results=run.results_text(),
                                                          feedback=feedback)
                    if not more_allowed:
                        request += "\nPlus aucune étape n'est possible : rédige la réponse avec ces résultats."
                    if state.get("interpretations"):
                        request += ("\nInterprétations retenues pour les termes ambigus (dis-le brièvement dans la "
                                    "réponse) :\n" + interprete.as_text(state["interpretations"]))
                    messages = [SystemMessage(planner_system), HumanMessage(request)]
                try:
                    decision = run.ask_json("planificateur", messages, check_plan)
                    done = state.get("etapes", [])
                    if decision["statut"] == "a_traiter" and phase == "rediger" and (
                            state.get("rounds", 0) >= MAX_ROUNDS or len(done) >= MAX_STEPS):
                        # no room for more steps: the answer must be written with the results there are
                        decision = run.ask_json("planificateur", messages + [HumanMessage(
                            "Plus aucune étape n'est possible. Réponds avec le statut repondu, à partir des résultats "
                            "obtenus, en disant ce qui manque.")], check_plan)
                        if decision["statut"] == "a_traiter":
                            raise ValueError("de nouvelles étapes demandées alors que la limite est atteinte")
                except ValueError as error:
                    return {"brouillon": {"statut": "echec", "reponse": f"L'équipe n'a pas pu répondre ({error})."}}
            if decision["statut"] == "a_traiter":
                steps = (state.get("etapes", []) + decision["etapes"])[:MAX_STEPS]
                return {"etapes": steps, "rounds": state.get("rounds", 0) + 1, "brouillon": None}
            return {"brouillon": decision}

        def worker(role, request_of, system_of, tool, execute):
            def node(state: TeamState):
                step = state["etapes"][state.get("index", 0)]
                with run.recorder.span(role, "etape", consigne=step["consigne"]):
                    text = run.tool_loop(role, system_of(), request_of(state, step), tool, execute)
                run.reports.append({"agent": role, "consigne": step["consigne"], "texte": text})
                return {"index": state.get("index", 0) + 1, "phase": "rediger"}
            return node

        def interpretations_text(state):
            if not state.get("interpretations"):
                return ""
            return "\nInterprétations retenues (à suivre) :\n" + interprete.as_text(state["interpretations"])

        def sql_request(state, step):
            known = run.tables_description() if run.sql.tables else ""
            return (f"Question de l'utilisateur : {state['question']}\nTa consigne : {step['consigne']}"
                    + interpretations_text(state) + (f"\nTableaux déjà obtenus :\n{known}" if known else ""))

        def other_request(state, step):
            return (f"Question de l'utilisateur : {state['question']}\nTa consigne : {step['consigne']}"
                    + interpretations_text(state))

        def interpreter(state: TeamState):
            terms = interprete.ambiguous_terms(state["question"])
            listed = "\n".join(f"- « {term} » : {why}" for term, why in terms)
            with run.recorder.span("interprete", "interpreter", termes=[t for t, _ in terms]) as event:
                request = interprete.INTERPRETER_PROMPT.format(
                    question=state["question"], plan=json.dumps(state.get("etapes", []), ensure_ascii=False),
                    terms=listed)
                try:
                    readings = run.ask_json("interprete", [HumanMessage(request)], interprete.check_interpretation)
                except ValueError as error:  # without an interpretation, the team goes on as before
                    event.ok = False
                    event.attributes["erreur"] = str(error)
                    readings = []
                event.attributes["interpretations"] = [f"{r['terme']} : {r['choix']}" for r in readings]
            return {"interpretations": readings, "interprete_fait": True}

        sql_tool = StructuredTool.from_function(
            func=lambda requete: "", name="executer_sql",
            description="Exécute UNE requête SELECT DuckDB sur la base et renvoie un aperçu du résultat.")
        python_tool = StructuredTool.from_function(
            func=lambda code: "", name="executer_python",
            description="Exécute du code Python dans le bac à sable (pd, np, stats, sm, smf disponibles, tableaux "
                        "t1, t2...). Mettre la réponse dans la variable resultat.")

        def creer_graphique(type: str, tableau: str, x: str = "", y: str = "", couleur: str = "", titre: str = "",
                            titre_x: str = "", titre_y: str = "", orientation: str = ""):
            """Crée un graphique : type parmi line, bar, scatter, box, histogram ; tableau = t1, t2... ; x, y et
            couleur sont des noms de colonnes du tableau ; orientation "h" pour des barres horizontales."""
            return ""

        chart_tool = StructuredTool.from_function(func=creer_graphique, name="creer_graphique")

        def relecteur(state: TeamState):
            draft = state.get("brouillon") or {"statut": "echec", "reponse": "Aucune réponse n'a été rédigée."}
            reviews = state.get("reviews", 0) + 1
            with run.recorder.span("relecteur", "relire", statut=draft["statut"]) as event:
                problems = []
                if draft["statut"] == "echec":
                    event.attributes["verdict"] = "echec"
                    return {"final": {**draft, "approuve": False, "problemes": [draft["reponse"]]},
                            "reviews": reviews}
                unsupported = unsupported_numbers(draft["reponse"], run.allowed_numbers())
                if unsupported:
                    problems.append("Ces nombres n'apparaissent dans aucun résultat : " + ", ".join(unsupported)
                                    + ". Fais-les calculer par l'agent sql ou statistiques, ou retire-les.")
                if (draft["statut"] == "repondu" and CHART_WORDS.search(state["question"]) and not run.charts):
                    problems.append("La question demande un graphique, mais aucun graphique n'a été créé.")
                event.attributes["controles_automatiques"] = len(problems)
                if not problems:
                    request = prompts.REVIEW_PROMPT.format(
                        question=state["question"], plan=json.dumps(state.get("etapes", []), ensure_ascii=False),
                        results=run.results_text(), statut=draft["statut"], reponse=draft["reponse"],
                        rules=prompts.RULES)
                    try:
                        verdict = run.ask_json("relecteur", [HumanMessage(request)], check_verdict)
                    except ValueError:
                        verdict = {"verdict": "approuve", "problemes": []}  # an unreadable review does not block
                    problems = verdict["problemes"]
                event.attributes["verdict"] = "a_corriger" if problems else "approuve"
                event.attributes["problemes"] = problems
            if not problems:
                return {"final": {**draft, "approuve": True, "problemes": []}, "reviews": reviews}
            if reviews >= MAX_REVIEWS:
                return {"final": {**draft, "approuve": False, "problemes": problems}, "reviews": reviews}
            phase = "rediger" if run.reports else "planifier"
            return {"retours": problems, "reviews": reviews, "brouillon": None, "phase": phase}

        def after_planner(state: TeamState):
            index = state.get("index", 0)
            if state.get("brouillon") or index >= len(state.get("etapes", [])):
                return "relecteur"  # (without a draft, the reviewer closes the run as a failure)
            if not state.get("interprete_fait") and interprete.ambiguous_terms(state["question"]):
                return "interprete"
            return state["etapes"][index]["agent"]

        def after_interpreter(state: TeamState):
            return state["etapes"][state.get("index", 0)]["agent"]

        def after_worker(state: TeamState):
            index = state.get("index", 0)
            return state["etapes"][index]["agent"] if index < len(state["etapes"]) else "planificateur"

        def after_review(state: TeamState):
            return END if state.get("final") else "planificateur"

        graph = StateGraph(TeamState)
        graph.add_node("planificateur", planificateur)
        graph.add_node("sql", worker("sql", sql_request, lambda: prompts.SQL_PROMPT, sql_tool, run.execute_sql))
        graph.add_node("statistiques", worker(
            "statistiques", other_request, lambda: prompts.STATS_PROMPT.format(tables=run.tables_description()),
            python_tool, run.execute_python))
        graph.add_node("visualisation", worker(
            "visualisation", other_request, lambda: prompts.VIZ_PROMPT.format(tables=run.tables_description()),
            chart_tool, run.make_chart))
        graph.add_node("relecteur", relecteur)
        graph.add_node("interprete", interpreter)
        graph.add_edge(START, "planificateur")
        graph.add_conditional_edges("planificateur", after_planner, ["relecteur", "interprete", *WORKERS])
        graph.add_conditional_edges("interprete", after_interpreter, list(WORKERS))
        for role in WORKERS:
            graph.add_conditional_edges(role, after_worker, ["planificateur", *WORKERS])
        graph.add_conditional_edges("relecteur", after_review, ["planificateur", END])
        return graph.compile()

    def ask(self, question, tracer=None, run_attributes=None, listeners=()):
        """Run the team on one question; returns a TeamAnswer with the answer, the trace and the costs.

        With an OpenTelemetry tracer, the run is also traced (one root span, one span per event); listeners are
        called with ("debut" | "fin", event) while the run goes on."""
        run_id = uuid.uuid4().hex
        bridge = None
        if tracer is not None:
            planner = self.llms["planificateur"]
            bridge = SpanBridge(tracer, {"gen_ai.conversation.id": run_id, "relais.question": question,
                                         "gen_ai.request.model": model_name(planner),
                                         "gen_ai.request.temperature": getattr(planner, "temperature", None),
                                         "relais.planner_prompt": self.planner_prompt, **(run_attributes or {})})
        run = Run(self, question, bridge, listeners)
        started = time.time()
        final = {"statut": "echec", "reponse": "", "approuve": False, "problemes": []}
        try:
            state = self.build_graph(run).invoke({"question": question, "phase": "planifier", "etapes": [],
                                                  "index": 0, "rounds": 0, "reviews": 0, "retours": []},
                                                 {"recursion_limit": 80})
            final = state.get("final") or final
        finally:
            if bridge:
                tokens_in = sum(u["input_tokens"] for u in run.usage)
                tokens_out = sum(u["output_tokens"] for u in run.usage)
                bridge.finish(**{"relais.answer.status": final["statut"], "relais.answer.approved": final["approuve"],
                                 "gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out})
        return TeamAnswer(
            question=question, statut=final["statut"], reponse=final["reponse"], approuve=final["approuve"],
            problemes=final["problemes"], path=run.recorder.path(), graphique=run.charts[-1] if run.charts else None,
            tables=dict(run.sql.tables), statistiques=run.stats, events=run.recorder.events, usage=run.usage,
            latence_s=time.time() - started, etapes=state.get("etapes", []), run_id=run_id)


def check_verdict(decision):
    verdict = decision.get("verdict")
    if verdict == "approuve":
        return {"verdict": "approuve", "problemes": []}
    if verdict == "a_corriger":
        problems = [str(p) for p in decision.get("problemes", []) if str(p).strip()]
        return {"verdict": "a_corriger", "problemes": problems or ["Réponse à revoir."]}
    raise ValueError(f"verdict inconnu : {verdict!r}")
