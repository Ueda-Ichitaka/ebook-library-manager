"""About this file: runs pipeline commands as background processes for the web UI.

A job is a command such as identify or build, started as its own process so the web UI
stays responsive and a crashing job can't take it down. Only one job runs at a time,
because the commands share the state and the output folder. Output goes to a log file
per job, and status and exit code are stored in the SQLite state. A job can be stopped.
Jobs still marked as running when the web UI starts were cut off by a restart and are
marked as interrupted.
"""

import subprocess
import threading
from pathlib import Path

from ebookprep.state import JobRow, StateStore

STOP_GRACE_SECONDS = 10


class JobBusy(Exception):
    """Raised when a job is started while another one is running."""


class JobRunner:
    def __init__(self, store: StateStore, log_dir: Path):
        self.store = store
        self.log_dir = log_dir
        self._lock = threading.Lock()
        self._processes: dict[int, subprocess.Popen] = {}
        self._stopping: set[int] = set()
        for job in store.jobs(limit=1000):
            if job.status == "running":
                store.finish_job(job.id, "interrupted", None)

    def start(self, name: str, argv: list[str]) -> int:
        with self._lock:
            running = self.current()
            if running is not None:
                raise JobBusy(f"{running.name} is still running")
            self.log_dir.mkdir(parents=True, exist_ok=True)
            job_id = self.store.add_job(name, "")
            log_path = self.log_dir / f"job-{job_id}-{name}.log"
            self.store.set_job_log(job_id, str(log_path))
            log = log_path.open("wb")
            process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self._processes[job_id] = process
        threading.Thread(target=self._wait, args=(job_id, process, log), daemon=True).start()
        return job_id

    def _wait(self, job_id: int, process: subprocess.Popen, log) -> None:
        exit_code = process.wait()
        log.close()
        if job_id in self._stopping:
            status = "stopped"
        else:
            status = "succeeded" if exit_code == 0 else "failed"
        self.store.finish_job(job_id, status, exit_code)
        self._processes.pop(job_id, None)

    def stop(self, job_id: int) -> None:
        process = self._processes.get(job_id)
        if process is None:
            return
        self._stopping.add(job_id)
        process.terminate()
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()

    def current(self) -> JobRow | None:
        return next((j for j in self.store.jobs(limit=20) if j.status == "running"), None)

    def job(self, job_id: int) -> JobRow:
        return self.store.job(job_id)

    def history(self, limit: int = 50) -> list[JobRow]:
        return self.store.jobs(limit=limit)

    def log(self, job_id: int, tail_bytes: int | None = None) -> str:
        path = Path(self.store.job(job_id).log_path)
        if not path.is_file():
            return ""
        data = path.read_bytes()
        if tail_bytes is not None:
            data = data[-tail_bytes:]
        return data.decode("utf-8", errors="replace")
