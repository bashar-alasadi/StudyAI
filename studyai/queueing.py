"""Queue abstraction that keeps RQ details out of routes and pipeline code."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from flask import Flask, current_app


class JobQueue(Protocol):
    def enqueue(self, job_id: str) -> str: ...


class RQJobQueue:
    def __init__(self, redis_url: str, queue_name: str, timeout: int):
        from redis import Redis
        from rq import Queue

        self.queue = Queue(queue_name, connection=Redis.from_url(redis_url))
        self.timeout = timeout

    def enqueue(self, job_id: str) -> str:
        queued = self.queue.enqueue(
            "studyai.tasks.process_lecture_job",
            job_id,
            job_id=rq_job_id(job_id),
            job_timeout=self.timeout,
            result_ttl=86400,
            failure_ttl=604800,
        )
        return queued.id


def rq_job_id(job_id: str) -> str:
    """Build an RQ-compatible deterministic identifier."""
    return f"studyai-{job_id}"


class SynchronousJobQueue:
    def __init__(self, runner=None):
        self.runner = runner

    def enqueue(self, job_id: str) -> str:
        if self.runner:
            self.runner(job_id)
        return f"sync:{job_id}"


class ThreadJobQueue:
    """Single-process background queue for small deployments without Redis."""

    def __init__(self, app: Flask):
        self.app = app
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studyai")

    def enqueue(self, job_id: str) -> str:
        self.executor.submit(self._run, job_id)
        return f"thread:{job_id}"

    def _run(self, job_id: str) -> None:
        from .pipeline import process_pipeline

        with self.app.app_context():
            process_pipeline(job_id)


def init_queue(app: Flask) -> None:
    factory = app.config.get("JOB_QUEUE_FACTORY")
    if factory:
        app.extensions["job_queue"] = factory(app)
    elif app.config["JOB_QUEUE_MODE"] == "sync":
        app.extensions["job_queue"] = SynchronousJobQueue()
    elif app.config["JOB_QUEUE_MODE"] == "thread":
        app.extensions["job_queue"] = ThreadJobQueue(app)
    else:
        app.extensions["job_queue"] = RQJobQueue(
            app.config["REDIS_URL"], app.config["RQ_QUEUE"], app.config["JOB_TIMEOUT_SECONDS"]
        )


def get_job_queue() -> JobQueue:
    return current_app.extensions["job_queue"]
