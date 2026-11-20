from types import SimpleNamespace
from app.scheduler.engine import select_agent, hard_filter, update_feedback

def agent(**kw):
    base = dict(id=1, status="active", availability=True, tools=["file_read"],
                capability_tags=["coding"], capability_scores={"coding": 0.9},
                historical_success_rate=0.8, average_cost=1.0, average_latency=2.0,
                current_load=0.1, execution_count=0, success_count=0)
    base.update(kw)
    return SimpleNamespace(**base)

def test_hard_filter_rejects_missing_tool():
    ok, why = hard_filter(agent(), [], ["run_pytest"])
    assert not ok and "tools" in why

def test_hard_filter_rejects_disabled_and_missing_capability():
    assert not hard_filter(agent(status="disabled"), [], [])[0]
    assert not hard_filter(agent(capability_tags=[], capability_scores={}), ["research"], [])[0]

def test_adaptive_prefers_stronger_less_loaded_agent():
    weak = agent(id=1, capability_scores={"coding": 0.3}, historical_success_rate=0.4, current_load=0.9)
    strong = agent(id=2, capability_scores={"coding": 0.95}, historical_success_rate=0.95, current_load=0.0)
    d = select_agent([weak, strong], ["coding"], [], mode="adaptive")
    assert d.selected.id == 2
    assert len(d.candidates) == 2 and d.reason

def test_adaptive_result_changes_when_profile_changes():
    a = agent(id=1, capability_scores={"coding": 0.9}, historical_success_rate=0.9, current_load=0.0)
    b = agent(id=2, capability_scores={"coding": 0.85}, historical_success_rate=0.85, current_load=0.0)
    assert select_agent([a, b], ["coding"], [], mode="adaptive").selected.id == 1
    for _ in range(30):
        update_feedback(a, False)
    assert a.historical_success_rate < 0.5
    assert select_agent([a, b], ["coding"], [], mode="adaptive").selected.id == 2

def test_fixed_and_round_robin_baselines():
    agents = [agent(id=1), agent(id=2), agent(id=3)]
    assert select_agent(agents, ["coding"], [], mode="fixed").selected.id == 1
    assert select_agent(agents, ["coding"], [], mode="round_robin", rotation_index=1).selected.id == 2
    assert select_agent(agents, ["coding"], [], mode="round_robin", rotation_index=4).selected.id == 2

def test_no_eligible_agent_returns_none_with_reason():
    d = select_agent([agent(tools=[])], [], ["file_read"], mode="adaptive")
    assert d.selected is None and "hard constraints" in d.reason

def test_feedback_ema_updates_counts():
    a = agent(historical_success_rate=0.5)
    update_feedback(a, True)
    assert a.execution_count == 1 and a.success_count == 1 and a.historical_success_rate > 0.5
