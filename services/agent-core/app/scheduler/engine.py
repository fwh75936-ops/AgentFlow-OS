"""Adaptive Agent Scheduler.

Hard constraints are applied FIRST (agent active, available, holds every
required tool, covers every required capability). Only survivors are scored:

  Score = w1*CapabilityMatch + w2*HistoricalSuccess + w3*Availability
        + w4*PriorityFit - w5*CostNorm - w6*LatencyNorm - w7*CurrentLoad

All components are normalized to [0,1]. Cost/latency are min-max normalized
across the eligible candidate set. Weights are configurable.
Feedback updates profiles with an exponential moving average (alpha=0.2).
Modes: fixed (lowest id, baseline A), round_robin (baseline B), adaptive.
"""
from dataclasses import dataclass, field

DEFAULT_WEIGHTS = {
    "capability": 0.30, "success": 0.20, "availability": 0.10, "priority": 0.10,
    "cost": 0.10, "latency": 0.10, "load": 0.10,
}

@dataclass
class Weights:
    capability: float = 0.30
    success: float = 0.20
    availability: float = 0.10
    priority: float = 0.10
    cost: float = 0.10
    latency: float = 0.10
    load: float = 0.10

def _get(agent, name, default=None):
    if isinstance(agent, dict):
        return agent.get(name, default)
    return getattr(agent, name, default)

def hard_filter(agent, required_capabilities, required_tools):
    """Return (eligible: bool, reason: str)."""
    if _get(agent, "status", "active") != "active":
        return False, "agent disabled"
    if not _get(agent, "availability", True):
        return False, "agent unavailable"
    tools = set(_get(agent, "tools", []) or [])
    missing_tools = [t for t in (required_tools or []) if t not in tools]
    if missing_tools:
        return False, f"missing required tools: {missing_tools}"
    tags = set(_get(agent, "capability_tags", []) or [])
    scores = _get(agent, "capability_scores", {}) or {}
    missing_caps = [c for c in (required_capabilities or []) if c not in tags and c not in scores]
    if missing_caps:
        return False, f"missing required capabilities: {missing_caps}"
    return True, "eligible"

def capability_match(agent, required_capabilities):
    if not required_capabilities:
        return 0.7  # no specific requirement: neutral-positive baseline
    scores = _get(agent, "capability_scores", {}) or {}
    tags = set(_get(agent, "capability_tags", []) or [])
    vals = []
    for cap in required_capabilities:
        if cap in scores:
            vals.append(float(scores[cap]))
        elif cap in tags:
            vals.append(0.7)
        else:
            vals.append(0.0)
    return sum(vals) / len(vals)

def _minmax(values):
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]

@dataclass
class Decision:
    selected: object = None
    candidates: list = field(default_factory=list)
    reason: str = ""

SACAS_RISK_WEIGHT = 0.30
SACAS_ENV_WEIGHT = 0.15
SACAS_DEP_WEIGHT = 0.20

def _sacas_inputs(agent, resource_state, env_state, dependencies_ready):
    """Resolve per-agent SACAS inputs. Accepted shapes:
    resource_state: {agent_id: risk 0..1} or {agent_id: {"conflict_risk": x}}
                    or {"held_by": {resource_key: holder_agent_id}, "resource_key": k}
    env_state: {agent_id: readiness 0..1} or a single float/bool.
    dependencies_ready: bool, or {agent_id: bool}."""
    aid = _get(agent, "id")
    risk = 0.0
    if isinstance(resource_state, dict):
        if "held_by" in resource_state:
            holder = (resource_state.get("held_by") or {}).get(resource_state.get("resource_key"))
            risk = 0.0 if holder in (None, aid) else 1.0
        else:
            v = resource_state.get(aid, resource_state.get(str(aid), 0.0))
            risk = float(v.get("conflict_risk", 0.0)) if isinstance(v, dict) else float(v or 0.0)
    readiness = 1.0
    if isinstance(env_state, dict):
        v = env_state.get(aid, env_state.get(str(aid), 1.0))
        readiness = float(v.get("readiness", 1.0)) if isinstance(v, dict) else float(v)
    elif env_state is not None:
        readiness = float(env_state)
    dep = True
    if isinstance(dependencies_ready, dict):
        dep = bool(dependencies_ready.get(aid, dependencies_ready.get(str(aid), True)))
    elif dependencies_ready is not None:
        dep = bool(dependencies_ready)
    return max(0.0, min(1.0, risk)), max(0.0, min(1.0, readiness)), dep

def select_agent(agents, required_capabilities=None, required_tools=None,
                 priority=3, mode="adaptive", weights=None, rotation_index=0,
                 resource_state=None, env_state=None, dependencies_ready=None):
    w = weights or Weights()
    required_capabilities = required_capabilities or []
    required_tools = required_tools or []
    details = []
    eligible = []
    for a in agents:
        ok, why = hard_filter(a, required_capabilities, required_tools)
        details.append({"agent": a, "agent_id": _get(a, "id"), "eligible": ok,
                        "filter_reason": why, "components": {}, "total": None})
        if ok:
            eligible.append(a)
    if not eligible:
        return Decision(selected=None, candidates=_public(details),
                        reason="no agent satisfies hard constraints (status/tools/capabilities)")
    costs = _minmax([float(_get(a, "average_cost", 0.0) or 0.0) for a in eligible])
    lats = _minmax([float(_get(a, "average_latency", 0.0) or 0.0) for a in eligible])
    priority_norm = max(0.0, min(1.0, (priority - 1) / 4.0)) if priority else 0.5
    by_id = {id(a): i for i, a in enumerate(eligible)}
    for d in details:
        if not d["eligible"]:
            continue
        a = d["agent"]
        i = by_id[id(a)]
        load = float(_get(a, "current_load", 0.0) or 0.0)
        responsiveness = 1.0 - load
        comp = {
            "capability_match": round(capability_match(a, required_capabilities), 4),
            "historical_success": round(float(_get(a, "historical_success_rate", 0.5)), 4),
            "availability": 1.0,
            "priority_fit": round(1.0 - abs(priority_norm - responsiveness) * 0.5, 4),
            "cost_norm": round(costs[i], 4),
            "latency_norm": round(lats[i], 4),
            "current_load": round(load, 4),
        }
        total = (w.capability * comp["capability_match"] + w.success * comp["historical_success"]
                 + w.availability * comp["availability"] + w.priority * comp["priority_fit"]
                 - w.cost * comp["cost_norm"] - w.latency * comp["latency_norm"]
                 - w.load * comp["current_load"])
        if mode == "sacas":
            risk, readiness, dep_ok = _sacas_inputs(a, resource_state, env_state, dependencies_ready)
            comp["resource_conflict_risk"] = round(risk, 4)
            comp["env_readiness"] = round(readiness, 4)
            comp["dependency_readiness"] = 1.0 if dep_ok else 0.0
            total = (total - SACAS_RISK_WEIGHT * risk + SACAS_ENV_WEIGHT * readiness
                     + SACAS_DEP_WEIGHT * (1.0 if dep_ok else 0.0) - (0.0 if dep_ok else 1.0))
            d["dependency_ready"] = dep_ok
        d["components"] = comp
        d["total"] = round(total, 4)
    elig_details = [d for d in details if d["eligible"]]
    if mode == "sacas":
        ready = [d for d in elig_details if d.get("dependency_ready", True)]
        if not ready:
            return Decision(selected=None, candidates=_public(details),
                            reason="sacas: task dependencies are not ready; no agent selected")
        chosen = max(ready, key=lambda d: d["total"])
        c = chosen["components"]
        reason = (f"sacas: agent {chosen['agent_id']} highest score {chosen['total']} "
                  f"(resource_conflict_risk={c.get('resource_conflict_risk')}, "
                  f"env_readiness={c.get('env_readiness')}, "
                  f"dependency_readiness={c.get('dependency_readiness')})")
        return Decision(selected=chosen["agent"], candidates=_public(details), reason=reason)
    if mode == "fixed":
        chosen = sorted(elig_details, key=lambda d: d["agent_id"])[0]
        reason = f"fixed baseline: lowest-id eligible agent {chosen['agent_id']}"
    elif mode == "round_robin":
        ordered = sorted(elig_details, key=lambda d: d["agent_id"])
        chosen = ordered[rotation_index % len(ordered)]
        reason = f"round-robin baseline: rotation index {rotation_index} -> agent {chosen['agent_id']}"
    else:
        chosen = max(elig_details, key=lambda d: d["total"])
        reason = (f"adaptive: agent {chosen['agent_id']} highest score {chosen['total']} "
                  f"(capability={chosen['components']['capability_match']}, "
                  f"success={chosen['components']['historical_success']}, "
                  f"load={chosen['components']['current_load']})")
    return Decision(selected=chosen["agent"], candidates=_public(details), reason=reason)

def load_from_running(running_nodes: int) -> float:
    """Current load in [0,1) derived from a real running-node count:
    0 when idle, 0.5 with one node running, saturating towards 1.
    This is the single definition of load used by the scheduler and
    the API — the stored Agent.current_load column has no writer and
    must never be trusted as a live value."""
    if running_nodes <= 0:
        return 0.0
    return running_nodes / (running_nodes + 1.0)

def refresh_live_loads(db, agents):
    """Overwrite each agent's current_load with the live value derived
    from TaskNode rows in status='running' (one grouped query).
    Callers invoke this immediately before select_agent so scheduling
    decisions use real load instead of the never-written column."""
    from sqlalchemy import func
    from app import models
    ids = [_get(a, "id") for a in agents]
    ids = [i for i in ids if i is not None]
    counts = {}
    if ids:
        rows = (db.query(models.TaskNode.assigned_agent_id, func.count(models.TaskNode.id))
                .filter(models.TaskNode.status == "running",
                        models.TaskNode.assigned_agent_id.in_(ids))
                .group_by(models.TaskNode.assigned_agent_id).all())
        counts = {aid: n for aid, n in rows}
    for a in agents:
        load = load_from_running(counts.get(_get(a, "id"), 0))
        if isinstance(a, dict):
            a["current_load"] = load
        else:
            a.current_load = load
    return agents

def _public(details):
    return [{"agent_id": d["agent_id"], "eligible": d["eligible"],
             "filter_reason": d["filter_reason"], "components": d["components"],
             "total": d["total"]} for d in details]

def update_feedback(agent, success: bool, latency: float = 0.0, cost: float = 0.0, alpha: float = 0.2):
    """Exponential moving average update of the agent capability profile."""
    rate = float(_get(agent, "historical_success_rate", 0.5))
    new_rate = rate + alpha * ((1.0 if success else 0.0) - rate)
    lat = float(_get(agent, "average_latency", 0.0) or 0.0)
    cst = float(_get(agent, "average_cost", 0.0) or 0.0)
    def _set(name, value):
        if isinstance(agent, dict):
            agent[name] = value
        else:
            setattr(agent, name, value)
    _set("historical_success_rate", round(new_rate, 6))
    if latency:
        _set("average_latency", round(lat + alpha * (latency - lat), 6) if lat else latency)
    if cost:
        _set("average_cost", round(cst + alpha * (cost - cst), 6) if cst else cost)
    _set("execution_count", int(_get(agent, "execution_count", 0)) + 1)
    if success:
        _set("success_count", int(_get(agent, "success_count", 0)) + 1)
    return agent
