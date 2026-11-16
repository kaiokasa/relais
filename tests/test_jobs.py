"""The shared job queue: concurrent runs, limits, isolation (no LLM: a stub team)."""
import threading
import time

import pytest

from relais.jobs import DONE, FAILED, RUNNING, WAITING, JobManager, QueueFull


class StubTeam:
    """Answers after a while, telling the listeners; the gate lets a test hold the runs."""

    def __init__(self, gate, fail=False):
        self.gate, self.fail = gate, fail

    def ask(self, question, listeners=()):
        for listener in listeners:
            listener("debut", question)
        self.gate.wait(5)
        if self.fail:
            raise RuntimeError("API indisponible")
        return f"réponse à {question}"


class StubMonitor:
    def __init__(self):
        self.seen, self.finished = [], False

    def __call__(self, moment, event):
        self.seen.append((moment, event))

    def finish(self, answer=None):
        self.finished = True


def wait_until(condition, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def manager(gate, max_workers=2, **kwargs):
    return JobManager(lambda config: StubTeam(gate, fail=config.get("fail", False)), lambda config: StubMonitor(),
                      max_workers=max_workers, **kwargs)


def test_runs_go_on_in_parallel_up_to_the_limit_and_the_rest_waits():
    gate = threading.Event()
    jobs = manager(gate, max_workers=2, max_waiting_per_owner=5)
    submitted = [jobs.submit(f"q{i}", {}, owner="alice") for i in range(3)]
    assert wait_until(lambda: sum(j.status == RUNNING for j in submitted) == 2)
    assert submitted[2].status == WAITING  # the third waits for a free slot
    gate.set()
    assert wait_until(lambda: all(j.status == DONE for j in submitted))
    assert [j.answer for j in submitted] == ["réponse à q0", "réponse à q1", "réponse à q2"]
    assert all(j.monitor.finished and j.monitor.seen == [("debut", j.question)] for j in submitted)
    assert jobs.list()[0].id == submitted[2].id and jobs.counts()[DONE] == 3


def test_several_people_at_the_same_time_and_limits():
    gate = threading.Event()
    jobs = manager(gate, max_workers=1, max_waiting_per_owner=2, max_queue=3)
    jobs.submit("a1", {}, "alice")
    jobs.submit("a2", {}, "alice")
    with pytest.raises(QueueFull, match="déjà 2"):
        jobs.submit("a3", {}, "alice")  # the limit is per person
    jobs.submit("b1", {}, "bob")  # someone else can still ask
    with pytest.raises(QueueFull, match="file est pleine"):
        jobs.submit("c1", {}, "carol")
    gate.set()
    assert wait_until(lambda: not any(j.active for j in jobs.list()))
    jobs.submit("a3", {}, "alice")  # room again once the runs are over


def test_a_failed_run_does_not_stop_the_queue():
    gate = threading.Event()
    gate.set()
    jobs = manager(gate)
    bad, good = jobs.submit("q", {"fail": True}, "alice"), jobs.submit("q", {}, "bob")
    assert wait_until(lambda: not bad.active and not good.active)
    assert bad.status == FAILED and "API indisponible" in bad.error and bad.monitor.finished
    assert good.status == DONE and good.duration() >= 0


def test_old_finished_jobs_are_forgotten():
    gate = threading.Event()
    gate.set()
    jobs = manager(gate, max_waiting_per_owner=10, keep=3)
    for i in range(6):
        job = jobs.submit(f"q{i}", {}, "alice")
        assert wait_until(lambda: not job.active)
    jobs.submit("last", {}, "alice")
    assert len(jobs.list()) <= 4
