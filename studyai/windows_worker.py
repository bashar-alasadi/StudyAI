"""Windows-safe RQ work-horse launcher for long-running StudyAI jobs."""

from __future__ import annotations

import contextlib
import errno
import os
import signal
import sys
import time

from redis import Redis
from rq import Queue, SpawnWorker, Worker
from rq.executions import Execution
from rq.job import Job


class WindowsSpawnWorker(SpawnWorker):
    """Avoid RQ's Unix-only wait4/process-group calls on native Windows."""

    def fork_work_horse(self, job: Job, queue: Queue) -> None:
        os.environ["RQ_WORKER_ID"] = self.name
        os.environ["RQ_EXECUTION_ID"] = self.execution.id
        child_pid = os.spawnv(
            os.P_NOWAIT,
            sys.executable,
            [
                sys.executable,
                "-m",
                "studyai.windows_worker",
                "horse",
                self.key,
                job.id,
                queue.name,
                self.execution.id,
            ],
        )
        self._horse_pid = child_pid
        self.procline(f"Spawned {child_pid} at {time.time()}")

    def wait_for_horse(self):
        pid = status = None
        with contextlib.suppress(ChildProcessError):
            pid, status = os.waitpid(self.horse_pid, 0)
        return pid, status, None

    def kill_horse(self, sig=signal.SIGTERM) -> None:
        try:
            os.kill(self.horse_pid, sig)
            self.log.info("Worker %s: killed horse pid %s", self.name, self.horse_pid)
        except OSError as error:
            if error.errno != errno.ESRCH:
                raise


def run_horse(worker_key: str, job_id: str, queue_name: str, execution_id: str) -> None:
    from app import app

    connection = Redis.from_url(app.config["REDIS_URL"])
    worker = Worker.find_by_key(worker_key, connection=connection)
    if worker is None:
        raise RuntimeError("Parent RQ worker is no longer registered")
    job = Job.fetch(job_id, connection=connection, serializer=worker.serializer)
    queue = Queue(queue_name, connection=connection, serializer=worker.serializer)
    worker.execution = Execution.fetch(execution_id, job.id, connection=connection)
    worker._is_horse = True
    worker.main_work_horse(job, queue)


if __name__ == "__main__" and len(sys.argv) == 6 and sys.argv[1] == "horse":
    run_horse(*sys.argv[2:])
