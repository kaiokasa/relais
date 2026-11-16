"""The shared job queue of the console: several people can ask questions at the same time.

A question is submitted and runs in the background; the page stays free. At most `max_workers` runs go on at once
(the others wait in the queue), which protects the API budget and quotas; each person can have only a few
questions waiting, and the whole queue has a size limit. Every run has its own live monitor, so any page can follow
any run while it goes on.
"""
import itertools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

WAITING, RUNNING, DONE, FAILED = "en attente", "en cours", "terminé", "échec"


class QueueFull(RuntimeError):
    """The question cannot be queued now (too many questions waiting)."""


@dataclass
class Job:
    id: int
    question: str
    config: dict
    owner: str
    monitor: object
    submitted: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None
    status: str = WAITING
    answer: object = None
    error: str | None = None

    @property
    def active(self):
        return self.status in (WAITING, RUNNING)

    def duration(self, now=None):
        if self.started is None:
            return 0.0
        return (self.ended or now or time.time()) - self.started


class JobManager:
    def __init__(self, team_factory, monitor_factory, max_workers=4, max_waiting_per_owner=3, max_queue=20,
                 keep=60):
        """team_factory(config) -> a team (with .ask(question, listeners=...)); monitor_factory(config) -> a
        live monitor (a listener with .finish(answer))."""
        self.team_factory, self.monitor_factory = team_factory, monitor_factory
        self.max_waiting_per_owner, self.max_queue, self.keep = max_waiting_per_owner, max_queue, keep
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="relais-run")
        self.jobs = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def submit(self, question, config, owner):
        with self._lock:
            active = [j for j in self.jobs.values() if j.active]
            if len(active) >= self.max_queue:
                raise QueueFull(f"La file est pleine ({self.max_queue} questions en cours ou en attente).")
            if sum(1 for j in active if j.owner == owner) >= self.max_waiting_per_owner:
                raise QueueFull(f"Tu as déjà {self.max_waiting_per_owner} questions en cours ou en attente.")
            job = Job(next(self._ids), question, dict(config), owner, self.monitor_factory(config))
            self.jobs[job.id] = job
            self._forget_old()
        self.pool.submit(self._run, job)
        return job

    def _run(self, job):
        job.started, job.status = time.time(), RUNNING
        try:
            team = self.team_factory(job.config)
            job.answer = team.ask(job.question, listeners=[job.monitor])
            job.monitor.finish(job.answer)
            job.status = DONE
        except Exception as error:  # a failed run must not stop the queue
            job.error = f"{type(error).__name__}: {error}"
            job.monitor.finish()
            job.status = FAILED
        finally:
            job.ended = time.time()

    def _forget_old(self):
        finished = sorted((j for j in self.jobs.values() if not j.active), key=lambda j: j.ended or 0)
        for job in finished[:max(0, len(self.jobs) - self.keep)]:
            del self.jobs[job.id]

    def list(self):
        """The jobs, most recent first."""
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: j.id, reverse=True)

    def get(self, job_id):
        return self.jobs.get(job_id)

    def counts(self):
        jobs = self.list()
        return {status: sum(1 for j in jobs if j.status == status) for status in (WAITING, RUNNING, DONE, FAILED)}
