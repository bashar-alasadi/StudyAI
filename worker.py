"""Cross-platform RQ worker entry point."""

from __future__ import annotations

import logging
import platform

from redis import Redis
from rq import Queue, Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from app import app
from studyai.db import get_db
from studyai.jobs import INTERRUPTIBLE_STATES, prepare_interrupted_resume
from studyai.queueing import RQJobQueue, rq_job_id
from studyai.windows_worker import WindowsSpawnWorker

logger = logging.getLogger(__name__)
ACTIVE_RQ_STATES = {
    JobStatus.CREATED,
    JobStatus.QUEUED,
    JobStatus.STARTED,
    JobStatus.DEFERRED,
    JobStatus.SCHEDULED,
}


def run_worker(burst: bool = False) -> None:
    connection = Redis.from_url(app.config["REDIS_URL"])
    queue = Queue(app.config["RQ_QUEUE"], connection=connection)
    worker_class = WindowsSpawnWorker if platform.system() == "Windows" else Worker
    maintenance_worker = worker_class([queue], connection=connection)
    maintenance_worker.clean_registries()
    maintenance_worker.register_death()
    recover_interrupted_jobs(connection)
    worker = worker_class([queue], connection=connection)
    worker.work(burst=burst)


def recover_interrupted_jobs(connection: Redis) -> list[str]:
    """Requeue persisted nonterminal jobs whose RQ execution is no longer active."""
    recovered: list[str] = []
    with app.app_context():
        rows = get_db().execute(
            """SELECT id FROM processing_jobs
               WHERE status IN ({}) ORDER BY created_at""".format(
                ",".join("?" for _ in INTERRUPTIBLE_STATES)
            ),
            tuple(sorted(INTERRUPTIBLE_STATES)),
        ).fetchall()
        adapter = RQJobQueue(
            app.config["REDIS_URL"], app.config["RQ_QUEUE"], app.config["JOB_TIMEOUT_SECONDS"]
        )
        for row in rows:
            job_id = row["id"]
            try:
                rq_job = Job.fetch(rq_job_id(job_id), connection=connection)
                if rq_job.get_status(refresh=True) in ACTIVE_RQ_STATES:
                    continue
                rq_job.delete(remove_from_queue=True)
            except NoSuchJobError:
                pass
            prepare_interrupted_resume(job_id)
            adapter.enqueue(job_id)
            recovered.append(job_id)
            logger.info("requeued_interrupted_job job_id=%s", job_id)
    return recovered


if __name__ == "__main__":
    run_worker()
