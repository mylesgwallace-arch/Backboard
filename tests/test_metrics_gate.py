"""Metrics regression gate over the committed reports in models/."""

import json

from src import metrics_gate as mg
from src.provenance import provenance, with_provenance


def _write(directory, name, payload):
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def _tiny_setup(tmp_path, monkeypatch, log_loss=0.60):
    monkeypatch.setattr(mg, "TRACKED", [("ll", "a.json", "metrics.model.log_loss", "lower"),
                                        ("acc", "a.json", "metrics.model.accuracy", "higher")])
    monkeypatch.setattr(mg, "MUST_BEAT", [("beats", "a.json", "metrics.model.log_loss",
                                           "metrics.baseline.log_loss")])
    _write(tmp_path, "a.json", {"metrics": {"model": {"log_loss": log_loss, "accuracy": 0.65},
                                            "baseline": {"log_loss": 0.69}}})


def test_recorded_bounds_pass_and_a_regression_fails(tmp_path, monkeypatch):
    _tiny_setup(tmp_path, monkeypatch)
    gates = mg.record_bounds(tmp_path, tolerance=0.01)
    assert all(ok for _, ok, _ in mg.check(gates, tmp_path))

    _tiny_setup(tmp_path, monkeypatch, log_loss=0.62)  # worse than 0.60 * 1.01
    results = {name: ok for name, ok, _ in mg.check(gates, tmp_path)}
    assert results == {"ll": False, "acc": True, "beats": True}


def test_losing_to_the_baseline_fails_even_within_bounds(tmp_path, monkeypatch):
    _tiny_setup(tmp_path, monkeypatch, log_loss=0.70)
    gates = mg.record_bounds(tmp_path)
    results = {name: ok for name, ok, _ in mg.check(gates, tmp_path)}
    assert results["ll"] is True and results["beats"] is False


def test_missing_bound_or_report_fails(tmp_path, monkeypatch):
    _tiny_setup(tmp_path, monkeypatch)
    gates = mg.record_bounds(tmp_path)
    del gates["bounds"]["acc"]
    results = {name: ok for name, ok, _ in mg.check(gates, tmp_path)}
    assert results["acc"] is False
    (tmp_path / "a.json").unlink()
    assert not any(ok for _, ok, _ in mg.check(gates, tmp_path))


def test_checkpoint_keys_with_dots_are_looked_up():
    report = {"s": {"0.25": {"mae": 5.0}}}
    assert mg.lookup(report, "s.0.25.mae") == 5.0


def test_committed_reports_pass_the_committed_gates():
    gates = json.loads(mg.GATES_PATH.read_text(encoding="utf-8"))
    failures = [(name, message) for name, ok, message in mg.check(gates) if not ok]
    assert failures == []


def test_provenance_stamp_has_code_and_environment():
    stamp = provenance()
    assert set(stamp) >= {"generated_at", "git_commit", "git_dirty", "versions", "data"}
    assert stamp["versions"]["numpy"]
    assert with_provenance({"a": 1})["a"] == 1
    assert with_provenance([1]) == [1]
