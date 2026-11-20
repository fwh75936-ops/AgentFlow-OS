"""Chief stagnation monitor tests (pure signal, threshold semantics)."""
from app.chief.service import assess_stagnation


def test_two_consecutive_failures_flag_replan():
    r = assess_stagnation([True, False, False])
    assert r["stagnant"] is True and r["consecutive_no_progress"] == 2
    assert r["action"] == "replan_required"


def test_progress_resets_counter():
    r = assess_stagnation([False, False, True, False])
    assert r["stagnant"] is False and r["consecutive_no_progress"] == 1
    assert r["action"] == "continue"


def test_empty_and_all_progress():
    assert assess_stagnation([])["stagnant"] is False
    assert assess_stagnation([True, True])["consecutive_no_progress"] == 0


def test_threshold_configurable():
    assert assess_stagnation([False, False], threshold=3)["stagnant"] is False
    assert assess_stagnation([False, False, False], threshold=3)["stagnant"] is True
