"""The live monitor of a run: it is told of every event as it starts and ends (listener of the recorder) and keeps
what the dashboard shows: the agent at work, the handovers so far, the deviations from the reference model, the
alerts and the risk of a wrong answer.

It knows nothing about Streamlit: the dashboard only draws its state, and the tests drive it without a browser.
"""
import threading
import time
from collections import Counter
from dataclasses import dataclass

from relais.benchmark import load_questions
from relais.mining import directly_follows, reference_frame

SLOW_EVENT_S = 25.0


@dataclass
class Alert:
    niveau: str        # "info", "attention" or "alerte"
    message: str
    moment: float


def reference_transitions():
    """The directly-follows pairs allowed by the reference model (the expected paths of the question set)."""
    return set(directly_follows(reference_frame(load_questions())))


class LiveMonitor:
    def __init__(self, config, reference=None, risk_model=None):
        self.config = config
        self.reference = reference if reference is not None else reference_transitions()
        self.risk_model = risk_model
        self.events, self.alerts = [], []
        self.running = {}        # events started and not finished yet
        self.started_at = None
        self.path, self.active = [], None
        self.edges = Counter()
        self.risks = []          # (number of finished events, risk)
        self.finished = False
        self._lock = threading.Lock()

    # called from the thread of the team ------------------------------------------------------------------
    def __call__(self, moment, event):
        with self._lock:
            if moment == "debut":
                self.running[event.id] = event
                if self.started_at is None:
                    self.started_at = event.start
                if event.parent is None:
                    self._new_turn(event)
                return
            self.running.pop(event.id, None)
            self.events.append(event)
            self._check(event)
            if event.parent is None and self.active == event.agent:
                self.active = None
            if self.risk_model is not None:
                self.risks.append((len(self.events), self.risk_model.risk(self.events, self.config)))

    def _new_turn(self, event):
        previous = self.path[-1] if self.path else "début"
        self.path.append(event.agent)
        self.active = event.agent
        self.edges[(previous, event.agent)] += 1
        if (previous, event.agent) not in self.reference:
            self._alert("attention", f"Transition hors du modèle de référence : {previous} → {event.agent}.")

    def _check(self, event):
        a = event.attributes
        if not event.ok and event.action in ("executer_sql", "executer_python", "creer_graphique"):
            self._alert("attention", f"{event.agent} : échec de {event.action} ({str(a.get('erreur', ''))[:120]}).")
        if event.action == "relire" and a.get("verdict") == "a_corriger":
            problems = a.get("problemes") or []
            self._alert("alerte", "Le relecteur refuse la réponse : " + (str(problems[0])[:160] if problems else ""))
        if event.duration_ms and event.duration_ms > SLOW_EVENT_S * 1000 and event.parent is not None:
            self._alert("info", f"{event.agent} : {event.action} a pris {event.duration_ms / 1000:.0f} s.")

    def _alert(self, level, message):
        self.alerts.append(Alert(level, message, time.time()))

    def finish(self, answer=None):
        with self._lock:
            if self.path:
                last = self.path[-1]
                self.edges[(last, "fin")] += 1
                if (last, "fin") not in self.reference:
                    self._alert("attention", f"Fin de run hors du modèle de référence (après {last}).")
            if answer is not None and not answer.approuve:
                self._alert("alerte", "Réponse donnée sans l'approbation du relecteur.")
            self.active, self.finished = None, True
            self.running.clear()

    # read by the dashboard -----------------------------------------------------------------------------
    def snapshot(self):
        with self._lock:
            return {"path": list(self.path), "active": self.active, "edges": Counter(self.edges),
                    "alerts": list(self.alerts), "events": list(self.events), "risks": list(self.risks),
                    "finished": self.finished, "running": list(self.running.values()),
                    "started_at": self.started_at}

    @property
    def risk(self):
        return self.risks[-1][1] if self.risks else None
