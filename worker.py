"""Cross-platform RQ worker entry point."""

from __future__ import annotations

import platform

from redis import Redis
from rq import Queue, SpawnWorker, Worker

from app import app


def run_worker(burst: bool = False) -> None:
    connection = Redis.from_url(app.config["REDIS_URL"])
    queue = Queue(app.config["RQ_QUEUE"], connection=connection)
    worker_class = SpawnWorker if platform.system() == "Windows" else Worker
    worker_class([queue], connection=connection).work(burst=burst)


if __name__ == "__main__":
    run_worker()
