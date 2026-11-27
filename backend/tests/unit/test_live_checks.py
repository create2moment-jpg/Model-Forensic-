"""`mf doctor --live` checks, run against the run-history fakes."""

from types import SimpleNamespace

from model_forensic.infrastructure.connectors.azureml.live import run_checks
from tests.unit.test_azure_runhistory import source


class Cred:
    def get_token(self, *scopes):
        return SimpleNamespace(token="t", expires_on=0)


class BadCred:
    def get_token(self, *scopes):
        raise RuntimeError("AzureCliCredential: Please run 'az login'")


TIMES = [
    {"runId": r, "startTimeUtc": "2026-11-01T08:00:00Z", "endTimeUtc": "2026-11-01T20:00:00Z"}
    for r in ("pipe", "pipe_train")
]


def test_live_checks_happy_path() -> None:
    src, hist, _ = source()
    hist.runs.get_by_query_by_experiment_name = lambda *a, **k: iter(TIMES)
    checks = {c.name: c for c in run_checks(src, "pipe", credential=Cred())}
    assert all(c.ok for c in checks.values()), {n: c.detail for n, c in checks.items()}
    assert checks["list_jobs"].detail == "2 jobs on the first page"
    assert checks["get_run"].detail == "pipe: pipeline, 2 stage(s)"
    assert "4/4 names map" in checks["naming"].detail  # 2 names in each of the 2 stages
    assert "1 stage outputs" in checks["files"].detail
    assert checks["times"].detail == "2/2 finished jobs have start and end (0 from the job, 2 from run history)"


def test_live_checks_say_which_jobs_have_no_duration() -> None:
    src, _, _ = source()  # run history does not answer the time query
    checks = {c.name: c for c in run_checks(src, "pipe", credential=Cred())}
    assert not checks["times"].ok
    assert "0/2 finished jobs" in checks["times"].detail and "pipe" in checks["times"].detail


def test_live_checks_stop_on_credential_error() -> None:
    src, _, _ = source()
    checks = run_checks(src, credential=BadCred())
    assert [c.name for c in checks] == ["token"] and not checks[0].ok and "az login" in checks[0].detail
