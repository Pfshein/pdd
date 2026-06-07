"""Interactive menu (PDD-23): job listing + dispatch, with a fake questionary."""
from orchestrator import config, menu, state as state_mod


class _Ans:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


class FakeQ:
    """Stand-in for questionary: every prompt pops the next scripted answer."""
    def __init__(self, answers):
        self.answers = list(answers)

    def _pop(self, *a, **k):
        return _Ans(self.answers.pop(0))

    select = text = path = confirm = _pop


def test_list_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.save_state(state_mod.new_state("A"))
    state_mod.save_state(state_mod.new_state("B"))

    jobs = menu.list_jobs()
    names = {j["job"]: j["node"] for j in jobs}
    assert names == {"A": "INTAKE", "B": "INTAKE"}


def test_menu_quit_returns_zero(monkeypatch):
    monkeypatch.setattr(menu, "questionary", FakeQ(["Quit"]))
    assert menu.run() == 0


def test_menu_doctor_dispatches(monkeypatch):
    seen = []
    monkeypatch.setattr(menu.cli, "cmd_doctor", lambda args: seen.append("doctor") or 0)
    monkeypatch.setattr(menu, "questionary", FakeQ(["Doctor", "Quit"]))
    menu.run()
    assert seen == ["doctor"]


def test_menu_run_job_dispatches_to_cmd_run(monkeypatch):
    captured = {}
    monkeypatch.setattr(menu.cli, "cmd_run", lambda args: captured.update(vars(args)) or 0)
    monkeypatch.setattr(
        menu, "questionary",
        FakeQ(["Run a job", "J1", "/repo", "/t.md", "/m.json", "pytest -q", "", "Quit"]),
    )
    menu.run()
    assert captured["job"] == "J1"
    assert captured["repo"] == "/repo"
    assert captured["task"] == "/t.md"
    assert captured["meta"] == "/m.json"
    assert captured["test_command"] == "pytest -q"
    assert captured["setup_command"] is None  # empty -> None
