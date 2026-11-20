"""Workflow graph -> real execution (original implementation).

Compiles a saved Workflow graph into a Task + TaskNode DAG and runs it
through the standard task runner (app.execution.runner.run_task), so
scheduling, Self-Loop, verification records and snapshots are exactly the
ones used for ordinary tasks. Supported graph node kinds (both the React
Flow editor format {data:{label,...}} and the shared schema format
{kind,label,config} are accepted):

  Start / End   - structural markers; skipped, dependencies pass through
  Agent         - LLM node via the runner (no provider -> honest MODEL_ERROR);
                  config {agent_id} pins the assignee, {instruction} is the
                  node's own task brief, both honored by the runner
  Verification  - config {verify_mode}: "llm" (default) is an LLM verify
                  node via the runner; "structural" runs the runner's
                  deterministic structural check over upstream outputs
  Condition     - config {condition, on_false}: deterministic evaluation
                  over upstream outputs (field comparisons / text contains);
                  on_false "stop" (default) fails the run with the reason,
                  "continue" records the false verdict and proceeds
  HumanApproval - config {note}: a human decision gate. The synchronous
                  engine cannot park a run mid-flight, so the decision is
                  collected before the run (the UI asks, the execute call
                  carries approvals={graph_id: {approved, note}}) and the
                  runner records it as the node's output; without an
                  approval the node fails with APPROVAL_REQUIRED instead
                  of being waved through
  Tool          - Tool Registry node: config {tool_id, tool_args, approved}
                  executed by the runner's tool branch with the assigned
                  agent's permissions; required_tools=[tool_id] makes the
                  scheduler hard-filter for a capable agent.

Any other kind raises WorkflowError instead of being silently faked.
The run is recorded in workflow_runs with a graph->task node map, and the
run status mirrors the finished Task status.
"""
import json
from datetime import datetime

from app import models

SKIP_KINDS = {"start", "end"}
LLM_KINDS = {"agent": "agent", "verification": "verify", "plan": "plan"}
DETERMINISTIC_KINDS = {"condition": "condition", "humanapproval": "approval"}
SUPPORTED = SKIP_KINDS | set(LLM_KINDS) | set(DETERMINISTIC_KINDS) | {"tool"}


class WorkflowError(Exception):
    pass


def _kind_of(node):
    data = node.get("data") or {}
    kind = node.get("kind") or data.get("kind") or data.get("type") or node.get("type")
    if not kind:
        label = str(node.get("label") or data.get("label") or "")
        kind = label.split("：")[0].split(":")[0].strip()
    return str(kind).strip().lower()


def _label_of(node):
    data = node.get("data") or {}
    return str(node.get("label") or data.get("label") or node.get("id"))


def _config_of(node):
    data = node.get("data") or {}
    cfg = dict(data.get("config") or {})
    cfg.update(node.get("config") or {})
    for k, v in data.items():
        if k not in ("label", "config"):
            cfg.setdefault(k, v)
    return cfg


def compile_graph(graph):
    """Returns (specs, ) where specs = [{graph_id, name, node_type, cfg,
    required_capabilities, required_tools, dep_graph_ids}] in topo order."""
    if not isinstance(graph, dict):
        raise WorkflowError("工作流图必须是对象")
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    # WorkflowIn only validates that the graph itself is a dict: a
    # non-list nodes/edges (or a non-dict entry) used to crash here with
    # AttributeError — a bare 500 for a malformed saved graph.
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise WorkflowError("工作流节点与连线必须是数组")
    for n in nodes:
        if not isinstance(n, dict):
            raise WorkflowError("工作流节点必须是对象")
    for e in edges:
        if not isinstance(e, dict):
            raise WorkflowError("工作流连线必须是对象")
    by_id = {}
    for n in nodes:
        if n.get("id") is None:
            raise WorkflowError("工作流节点缺少编号")
        gid = str(n["id"])
        if gid in by_id:
            raise WorkflowError(f"工作流节点编号重复：{gid}")
        by_id[gid] = n
    kinds = {gid: _kind_of(n) for gid, n in by_id.items()}
    bad = {gid: k for gid, k in kinds.items() if k not in SUPPORTED}
    if bad:
        raise WorkflowError(f"不支持的节点类型：{bad}，支持的类型：{sorted(SUPPORTED)}")
    # raw adjacency among ALL nodes, then collapse skipped Start/End nodes
    preds = {gid: set() for gid in by_id}
    for e in edges:
        s, t = str(e.get("source")), str(e.get("target"))
        if s not in by_id or t not in by_id:
            raise WorkflowError(f"连线引用了不存在的节点：{e}")
        preds[t].add(s)

    def kept_ancestors(gid, seen=None):
        seen = seen if seen is not None else set()
        out = set()
        for p in preds.get(gid, ()):
            if p in seen:
                continue
            seen.add(p)
            if kinds[p] in SKIP_KINDS:
                out |= kept_ancestors(p, seen)
            else:
                out.add(p)
        return out

    kept = [gid for gid in by_id if kinds[gid] not in SKIP_KINDS]
    if not kept:
        raise WorkflowError("图中没有可执行的节点")
    specs = []
    used_names = set()
    for gid in kept:
        n = by_id[gid]
        cfg = _config_of(n)
        kind = kinds[gid]
        name = _label_of(n)
        if name in used_names:
            name = f"{name}#{gid}"
        used_names.add(name)
        if kind == "tool":
            tool_id = cfg.get("tool_id") or cfg.get("tool")
            if not tool_id:
                raise WorkflowError(f"工具节点「{name}」还没有选择工具")
            node_type, req_tools = "tool", [tool_id]
        elif kind == "condition":
            if not str(cfg.get("condition") or "").strip():
                raise WorkflowError(f"条件节点「{name}」还没有填写判断条件")
            node_type, req_tools = "condition", []
        elif kind == "humanapproval":
            node_type, req_tools = "approval", []
        elif kind == "verification" and str(cfg.get("verify_mode") or "llm") == "structural":
            node_type, req_tools = "verify_structural", []
        else:
            node_type, req_tools = LLM_KINDS[kind], list(cfg.get("required_tools") or [])
        specs.append({
            "graph_id": gid, "name": name, "node_type": node_type, "cfg": cfg,
            "assigned_agent_id": cfg.get("agent_id") or None,
            "required_capabilities": list(cfg.get("required_capabilities")
                                          or cfg.get("capabilities") or []),
            "required_tools": req_tools,
            "dep_graph_ids": sorted(kept_ancestors(gid)),
        })
    # cycle check (Kahn) over kept specs
    deps = {s["graph_id"]: set(s["dep_graph_ids"]) for s in specs}
    done, remaining = set(), set(deps)
    while remaining:
        ready = {g for g in remaining if deps[g] <= done}
        if not ready:
            raise WorkflowError(f"节点之间存在循环依赖：{sorted(remaining)}")
        done |= ready; remaining -= ready
    return specs


def execute_workflow(db, workflow_id, title=None, max_retries=2, approvals=None):
    """Create Task + DAG from the saved graph and run it for real.
    Returns the WorkflowRun row (status mirrors the Task outcome).
    approvals: {graph_node_id: {"approved": bool, "note": str}} — human
    decisions collected by the caller before the run (see HumanApproval
    in the module docstring); merged into the node's input, never into
    the saved graph."""
    from app.execution.runner import run_task
    wf = db.query(models.Workflow).get(workflow_id)
    if wf is None:
        raise WorkflowError(f"工作流不存在：{workflow_id}")
    graph = wf.graph or {}
    specs = compile_graph(graph)
    approvals = approvals or {}
    # Validate BEFORE creating the Task/WorkflowRun rows: a non-dict
    # approval used to crash on decision.get() mid-compilation (500)
    # after the run rows were already flushed.
    for gid, decision in approvals.items():
        if not isinstance(decision, dict):
            raise WorkflowError(f"节点 {gid} 的审批结果必须是对象")
    task = models.Task(title=title or f"工作流：{wf.name}",
                       description=f"由工作流 #{wf.id}「{wf.name}」触发执行",
                       scheduler_mode="adaptive", status="pending")
    db.add(task); db.flush()
    run = models.WorkflowRun(workflow_id=wf.id, task_id=task.id, status="running",
                             graph_snapshot=graph)
    db.add(run); db.flush()
    by_graph = {}
    for s in specs:
        cfg = s["cfg"]
        decision = approvals.get(s["graph_id"]) or {}
        # Approval is a per-run human decision ONLY. The saved graph is
        # configuration, not consent: honoring a preset "approved": true
        # in cfg would let whoever saved the graph permanently bypass
        # the approval gate for every future run.
        approved = decision.get("approved") is True
        input_data = json.dumps({
            "workflow_node_id": s["graph_id"],
            "tool_id": cfg.get("tool_id") or cfg.get("tool"),
            "tool_args": cfg.get("tool_args") or cfg.get("args") or {},
            "approved": approved,
            "approval_note": decision.get("note") or "",
            "approved_at": decision.get("decided_at") or "",
            "config": cfg,
        }, ensure_ascii=False)
        node = models.TaskNode(task_id=task.id, name=s["name"],
                               node_type=s["node_type"],
                               assigned_agent_id=s.get("assigned_agent_id"),
                               required_capabilities=s["required_capabilities"],
                               required_tools=s["required_tools"],
                               dependencies=[], status="pending",
                               input_data=input_data)
        db.add(node); db.flush()
        by_graph[s["graph_id"]] = node
    for s in specs:
        by_graph[s["graph_id"]].dependencies = [by_graph[g].id for g in s["dep_graph_ids"]]
    run.node_map = {gid: n.id for gid, n in by_graph.items()}
    db.commit()
    try:
        run_task(db, task, max_retries=max_retries,
                 required_output_fields=[s["name"] for s in specs])
    finally:
        try:
            _converge_run(db, run, task)
        except Exception:
            # The session itself is broken (e.g. a failed transaction
            # inside run_task): writing the run's final state on it would
            # raise again and strand the WorkflowRun in "running" with
            # no finished_at. Roll back and converge in a clean session,
            # the same pattern run_task itself uses. The clean session
            # must be on THIS run's database (see runner._session_factory):
            # the global SessionLocal may have been rebound since.
            try:
                db.rollback()
            except Exception:
                pass
            from app.execution.runner import _session_factory
            db2 = _session_factory(db)()
            try:
                run2 = db2.query(models.WorkflowRun).get(run.id)
                task2 = db2.query(models.Task).get(task.id)
                if run2 is not None and task2 is not None:
                    _converge_run(db2, run2, task2)
            finally:
                db2.close()
    return run


def _converge_run(db, run, task):
    db.refresh(task)
    if task.status not in ("completed", "failed", "cancelled"):
        # run_task raised before converging the task (or an unknown
        # state leaked): the run record must not claim "failed" while
        # the task itself hangs in "running" forever.
        task.status = "failed"
        task.error_message = task.error_message or "工作流执行异常中断"
        db.commit()
        db.refresh(task)
    run.status = task.status
    run.error = task.error_message if run.status == "failed" else None
    run.finished_at = datetime.utcnow()
    db.commit(); db.refresh(run)


def run_out(run):
    if run is None:
        return None
    return {"id": run.id, "workflow_id": run.workflow_id, "task_id": run.task_id,
            "status": run.status, "node_map": run.node_map, "error": run.error,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None}
