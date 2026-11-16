from threading import Barrier, Thread, active_count
from unittest import TestCase

from surface_dynamics.service_contracts import JobRequest
from surface_dynamics.service_state import JobStore, LocalDict, Rejected


BODY = {"operation": "explore", "longitude": -97.7, "latitude": 48.2,
        "start_date": "2026-10-07", "end_date": "2026-10-19"}
ACTIVE_JOBS = 4
BARRIER_TIMEOUT_SECONDS = 5
DELIVERY_BUDGET_BYTES = 100


def request_at(longitude):
    return JobRequest(**{**BODY, "longitude": longitude})


class StatusBarrierDict(LocalDict):
    """Holds each watched read until all of them are in flight, so sequential reads time out."""

    def __init__(self, parties):
        super().__init__()
        self.barrier = Barrier(parties)
        self.watched = set()

    def get(self, key, default=None):
        if key in self.watched:
            self.barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
        return super().get(key, default)


class StatusReadCountingDict(LocalDict):
    def __init__(self):
        super().__init__()
        self.status_reads = 0

    def get(self, key, default=None):
        if key.startswith("job:"):
            self.status_reads += 1
        return super().get(key, default)


class JobStoreTests(TestCase):
    def store(self, state=None, **limits):
        self.spawned = []
        return JobStore(LocalDict() if state is None else state,
                        lambda *args: self.spawned.append(args), "test-revision", **limits)

    def test_active_jobs_fill_the_pool_and_finished_jobs_free_slots(self):
        store = self.store(max_active=2)
        first = store.submit(request_at(1), "client")
        store.submit(request_at(2), "client")
        with self.assertRaises(Rejected) as full:
            store.submit(request_at(3), "client")
        self.assertEqual((full.exception.status, full.exception.code), (503, "queue_full"))
        store.state["job:" + first.job_id].update(status="succeeded", result={"artifacts": []})
        self.assertEqual(store.submit(request_at(3), "client").status, "queued")

    def test_job_statuses_are_not_read_below_capacity(self):
        state = StatusReadCountingDict()
        store = self.store(state, max_active=ACTIVE_JOBS + 1)
        for longitude in range(ACTIVE_JOBS):
            store.submit(request_at(longitude), "client")
        state.status_reads = 0
        self.assertEqual(store.submit(request_at(99), "client").status, "queued")
        self.assertEqual(state.status_reads, 0)

    def test_active_job_statuses_are_read_concurrently_at_capacity(self):
        state = StatusBarrierDict(parties=ACTIVE_JOBS)
        store = self.store(state, max_active=ACTIVE_JOBS)
        for longitude in range(ACTIVE_JOBS):
            store.submit(request_at(longitude), "client")
        job_keys = ["job:" + job_id for job_id in state["active"]]
        state[job_keys[0]].update(status="succeeded", result={"artifacts": []})
        state.watched = set(job_keys)
        self.assertEqual(store.submit(request_at(99), "client").status, "queued")
        self.assertEqual(len(self.spawned), ACTIVE_JOBS + 1)

    def test_submission_leaves_no_background_threads_to_delay_container_exit(self):
        store = self.store(max_active=ACTIVE_JOBS + 1)
        threads_before = active_count()
        for longitude in range(ACTIVE_JOBS + 1):
            store.submit(request_at(longitude), "client")
        self.assertEqual(active_count(), threads_before)

    def test_delivery_budget_follows_the_configured_bytes(self):
        store = self.store(daily_delivery_bytes=DELIVERY_BUDGET_BYTES)
        store.reserve_delivery(60)
        with self.assertRaises(Rejected) as over:
            store.reserve_delivery(41)
        self.assertEqual((over.exception.status, over.exception.code), (429, "daily_budget"))
        store.reserve_delivery(40)

    def test_delivery_reservations_do_not_wait_for_job_admission(self):
        store = self.store()
        finished = []
        thread = Thread(target=lambda: finished.append(store.reserve_delivery(10)), daemon=True)
        with store.lock:
            thread.start()
            thread.join(timeout=BARRIER_TIMEOUT_SECONDS)
            waiting = thread.is_alive()
        thread.join()
        self.assertFalse(waiting)
