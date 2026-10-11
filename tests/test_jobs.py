"""About this file: tests for ebookprep.jobs, which runs pipeline commands (identify,
build, ...) as background processes for the web UI: one at a time, with a log file, a
status in the SQLite state, and the possibility to stop a job."""

import sys
import time

import pytest

from ebookprep.jobs import JobBusy, JobRunner
from ebookprep.state import StateStore


def wait_until_finished(runner, job_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.job(job_id)
        if job.status != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def runner(tmp_path):
    return JobRunner(StateStore(tmp_path / "state.db"), tmp_path / "jobs")


def test_job_runs_in_background_and_logs_output(runner):
    job_id = runner.start("demo", [sys.executable, "-c", "print('hello from the job')"])

    job = wait_until_finished(runner, job_id)

    assert (job.name, job.status, job.exit_code) == ("demo", "succeeded", 0)
    assert "hello from the job" in runner.log(job_id)
    assert job.finished_at is not None


def test_failing_job_is_marked_failed(runner):
    job = wait_until_finished(runner, runner.start("demo", [sys.executable, "-c", "import sys; sys.exit(3)"]))

    assert (job.status, job.exit_code) == ("failed", 3)


def test_only_one_job_at_a_time(runner):
    first = runner.start("slow", [sys.executable, "-c", "import time; time.sleep(5)"])

    with pytest.raises(JobBusy, match="slow"):
        runner.start("other", [sys.executable, "-c", "pass"])

    runner.stop(first)
    assert wait_until_finished(runner, first).status == "stopped"


def test_current_and_history(runner):
    assert runner.current() is None
    done = runner.start("a", [sys.executable, "-c", "pass"])
    wait_until_finished(runner, done)
    running = runner.start("b", [sys.executable, "-c", "import time; time.sleep(5)"])

    assert runner.current().id == running
    assert [j.name for j in runner.history()] == ["b", "a"]
    runner.stop(running)
    wait_until_finished(runner, running)


def test_jobs_left_running_by_a_restart_are_marked_interrupted(tmp_path):
    store = StateStore(tmp_path / "state.db")
    job_id = store.add_job("build", "/tmp/x.log")

    runner = JobRunner(store, tmp_path / "jobs")

    assert runner.job(job_id).status == "interrupted"
    assert runner.current() is None
