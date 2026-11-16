"""The trace of a run: every turn of an agent, LLM call and tool call is an event with a start, an end, a parent
and attributes. It is what the observability layer is built from:

- each event is also an OpenTelemetry span when a SpanBridge is given (relais/telemetry.py);
- listeners are told when an event starts and ends: the live dashboard follows a run this way.
"""
import contextlib
import itertools
import time
from dataclasses import dataclass, field


@dataclass
class Event:
    id: int
    parent: int | None
    agent: str
    action: str
    start: float
    end: float | None = None
    ok: bool = True
    attributes: dict = field(default_factory=dict)

    @property
    def duration_ms(self):
        return None if self.end is None else (self.end - self.start) * 1000


class Recorder:
    def __init__(self, bridge=None, listeners=()):
        self.events = []
        self.bridge = bridge
        self.listeners = list(listeners)
        self._ids = itertools.count(1)
        self._stack = []

    def _notify(self, moment, event):
        for listener in self.listeners:
            try:
                listener(moment, event)
            except Exception:  # an observer must never break the run it observes
                pass

    @contextlib.contextmanager
    def span(self, agent, action, **attributes):
        """Records an event around a block; an exception marks it failed (and is raised again)."""
        parent = self._stack[-1] if self._stack else None
        event = Event(next(self._ids), parent.id if parent else None, agent, action, time.time(),
                      attributes=dict(attributes))
        self.events.append(event)
        self._stack.append(event)
        if self.bridge:
            self.bridge.start(event, parent)
        self._notify("debut", event)
        try:
            yield event
        except BaseException as error:
            event.ok = False
            event.attributes.setdefault("erreur", f"{type(error).__name__}: {error}"[:500])
            raise
        finally:
            event.end = time.time()
            self._stack.pop()
            if self.bridge:
                self.bridge.end(event)
            self._notify("fin", event)

    def path(self):
        """The agents in the order they took their turn: the trace at the level of the reference model."""
        return [e.agent for e in self.events if e.parent is None]
