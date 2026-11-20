"""Task execution: real DAG persisted in DB, independent nodes run in parallel
via asyncio, scheduler assigns each node, LLM calls are real, verification is
structural over real outputs, Self-Loop bounds retries. Snapshots + structured
agent messages are persisted at every step."""
import asyncio
import json
import time
from datetime import datetime

from app import models
from app.llm.client import chat_completion, LLMError
from app.scheduler.engine import Decision, refresh_live_loads, select_agent, update_feedback
from app.self_loop.engine import SelfLoop, classify_error
from app.verification.engine import structural_verify

PLAN_SPEC = [
    ("plan", "plan", ["planning"], []),
    ("research", "agent", ["research"], []),
    ("execute", "agent", ["coding", "general"], ["plan"]),
    ("verify", "verify", ["review"], ["execute", "research"]),
]

def _session_factory(db):
    """Session factory bound to the SAME database as `db`.

    The runner opens fresh sessions for its DAG passes and node threads.
    Resolving `app.database.SessionLocal` lazily at those points is wrong:
    a background task-runner thread (e.g. team-chat dispatch) that started
    under one database keeps re-resolving the global after another caller
    rebound it, so its DAG pass silently lands in a different database and
    — task ids collide across fresh databases — claims and corrupts the
    other run's nodes (observed in tests: a node left stuck in "running",
    surfacing as a false DEPENDENCY_ERROR). Deriving the factory from the
    task session's own engine pins every session of one run to one DB.
    """
    try:
        bind = db.get_bind()
    except Exception:
        bind = None
    if bind is None:
        from app.database import SessionLocal
        return SessionLocal
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=bind, autocommit=False, autoflush=False)

def build_dag(db, task):
    existing = db.query(models.TaskNode).filter_by(task_id=task.id).all()
    if existing:
        return existing
    by_name = {}
    nodes = []
    for name, ntype, caps, _deps in PLAN_SPEC:
        n = models.TaskNode(task_id=task.id, name=name, node_type=ntype,
                            required_capabilities=caps, required_tools=[],
                            dependencies=[], status="pending")
        db.add(n); db.flush()
        by_name[name] = n; nodes.append(n)
    for name, _t, _c, deps in PLAN_SPEC:
        by_name[name].dependencies = [by_name[d].id for d in deps]
    db.commit()
    return nodes

def _snapshot(db, task):
    nodes = db.query(models.TaskNode).filter_by(task_id=task.id).all()
    db.add(models.ExecutionSnapshot(task_id=task.id, state={
        "task_status": task.status,
        "nodes": [{"id": n.id, "name": n.name, "status": n.status,
                   "assigned_agent_id": n.assigned_agent_id} for n in nodes]}))
    db.commit()

def _message(db, task, sender, receiver, mtype, content):
    db.add(models.AgentMessage(team_id=task.team_id, task_id=task.id,
                               sender_agent_id=sender, receiver_agent_id=receiver,
                               message_type=mtype, content=content, meta={}))
    db.commit()

def _effective_system_prompt(db, agent):
    """Agent's own system prompt + the instructions of every enabled
    skill bound to the agent (agent.skills holds skill ids; names are
    also accepted for older rows). This is how skills take effect at
    execution time — through the same LLM call, not a side channel."""
    parts = [agent.system_prompt or "You are a helpful agent."]
    bound = agent.skills or []
    if bound:
        ids = [s for s in bound if isinstance(s, int)]
        names = [s for s in bound if isinstance(s, str)]
        q = db.query(models.Skill).filter(models.Skill.enabled == True)  # noqa: E712
        skills = [s for s in q.all()
                  if (s.id in ids or s.name in names) and s.instructions]
        for sk in skills:
            parts.append(f"【技能：{sk.name}】\n{sk.instructions}")
    return "\n\n".join(parts)

def _knowledge_hits(db, task, node):
    """Automatic knowledge-base retrieval for a node: hybrid search over
    the whole library with the task title + node name as the query.
    Returns [] when nothing matches or retrieval itself fails — a KB
    problem must never fail the task, and no citation is invented."""
    try:
        from app.memory.search import KnowledgeIndex
        return KnowledgeIndex(db).search(f"{task.title} {node.name}", top_k=3)
    except Exception:
        return []

def _finish_simple_node(db, task, node, agent, started, output, ok=True,
                        error_type=None, error_message=None):
    """Shared completion path for the non-LLM node branches below."""
    node.output_data = output
    node.status = "completed" if ok else "failed"
    if not ok:
        node.error_type = error_type
        node.error_message = error_message
    node.finished_at = datetime.utcnow()
    try:
        db.refresh(agent)
    except Exception:
        pass
    update_feedback(agent, ok, latency=time.time() - started, cost=0.0)
    _release_agent(db, agent, node, failed=not ok)
    db.commit()
    _message(db, task, agent.id, None, "TASK_RESULT" if ok else "ERROR_REPORT",
             f"节点「{node.name}」已由 {agent.name} 完成" if ok else
             f"节点「{node.name}」失败：{error_message}")
    _snapshot(db, task)
    return ok, error_type, error_message

def _eval_condition(db, task, node, cfg):
    """Deterministic condition evaluation over upstream node outputs.
    Supported forms (cfg['condition']):
      字段 == 值 / != / > / >= / < / <=   (字段 = upstream JSON output key)
      包含 文本 / 不包含 文本            (searches upstream raw outputs)
    Returns (result: bool, detail: str)."""
    import re as _re
    cond = str(cfg.get("condition") or "").strip()
    upstream = [u for u in db.query(models.TaskNode).filter_by(task_id=task.id).all()
                if u.output_data and u.id in (node.dependencies or [])]
    ctx = {}
    raws = []
    for u in upstream:
        raws.append(u.output_data)
        try:
            parsed = json.loads(u.output_data)
            if isinstance(parsed, dict):
                ctx.update(parsed)
        except Exception:
            pass
    m = _re.match(r"^包含\s+(.+)$", cond)
    if m:
        needle = m.group(1).strip()
        return (any(needle in r for r in raws), f"上游输出{'包含' if any(needle in r for r in raws) else '不包含'}「{needle}」")
    m = _re.match(r"^不包含\s+(.+)$", cond)
    if m:
        needle = m.group(1).strip()
        return (not any(needle in r for r in raws), f"上游输出{'不包含' if not any(needle in r for r in raws) else '包含'}「{needle}」")
    m = _re.match(r"^(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)$", cond)
    if m:
        key, op, raw_val = m.group(1).strip(), m.group(2), m.group(3).strip()
        if key not in ctx:
            return False, f"上游输出里没有字段「{key}」"
        actual = ctx[key]
        def as_int(x):
            # Exact integer view when possible: routing everything through
            # float() makes distinct big integers compare equal.
            if isinstance(x, bool):
                return None
            if isinstance(x, int):
                return x
            if isinstance(x, str) and _re.fullmatch(r"[+-]?\d+", x.strip()):
                return int(x)
            return None
        def num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        ia, iv = as_int(actual), as_int(raw_val)
        if ia is not None and iv is not None:
            left, right = ia, iv
        else:
            na, nv = num(actual), num(raw_val)
            if na is not None and nv is not None:
                left, right = na, nv
            else:
                left, right = str(actual), raw_val
        result = {"==": left == right, "!=": left != right, ">": left > right,
                  ">=": left >= right, "<": left < right, "<=": left <= right}[op] \
            if not (isinstance(left, str) and op not in ("==", "!=")) else False
        return bool(result), f"{key} 的实际值是 {actual}，条件 {key} {op} {raw_val} {'成立' if result else '不成立'}"
    return False, f"无法理解的条件写法：「{cond}」"

def _run_node(task_id, node_id, make_session=None):
    """Execute one node synchronously in its OWN DB session (thread-safe:
    parallel nodes each run in a worker thread). Returns (ok, error_type, error_msg).
    make_session is the factory bound to the task's own database (see
    _session_factory): run_task always passes it. It is optional only so
    direct callers (tests) keep working; they get the global SessionLocal,
    which is exactly what the old signature did — never pass None from a
    background thread, or the run can land in whatever database the
    global currently points at."""
    if make_session is None:
        from app.database import SessionLocal
        make_session = SessionLocal
    db = make_session()
    try:
        return _run_node_inner(db, task_id, node_id)
    except Exception as e:
        # The inner error handling itself failed (typically a broken
        # transaction after a failed commit). Converge the node (and its
        # agent) to a terminal state in a FRESH session so nothing is
        # left stuck in "running", then report the failure upward.
        et = classify_error(str(e)).value
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        db2 = make_session()
        try:
            node = db2.query(models.TaskNode).get(node_id)
            if node is not None and node.status == "running":
                node.status = "failed"; node.error_type = et
                node.error_message = str(e)[:500]
                node.finished_at = datetime.utcnow()
                if node.assigned_agent_id:
                    ag = db2.query(models.Agent).get(node.assigned_agent_id)
                    if ag is not None and ag.runtime_status == "Running":
                        ag.runtime_status = "Failed"
                db2.commit()
        except Exception:
            try:
                db2.rollback()
            except Exception:
                pass
        finally:
            db2.close()
        return False, et, str(e)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _release_agent(db, agent, node, failed=False):
    """Set the agent's runtime status after one of its nodes finishes —
    unless the same agent still has other nodes running in this task, in
    which case it stays Running. Callers refresh the agent BEFORE applying
    feedback updates so parallel completions do not lose each other's
    counters to a stale read."""
    others = (db.query(models.TaskNode)
              .filter_by(task_id=node.task_id, assigned_agent_id=agent.id,
                         status="running")
              .filter(models.TaskNode.id != node.id).count())
    if others == 0:
        agent.runtime_status = "Failed" if failed else "Idle"

def _run_node_inner(db, task_id, node_id):
    task = db.query(models.Task).get(task_id)
    node = db.query(models.TaskNode).get(node_id)
    if task is None or node is None:
        return False, "STATE_ERROR", "任务或节点不存在"
    # Atomic claim: only a pending node may enter execution. Without
    # this, a duplicate/late invocation could flip a completed or
    # failed node back to running and overwrite its recorded output.
    # (Self-Loop repair resets nodes to pending first, so retries are
    # unaffected.)
    claimed = (db.query(models.TaskNode)
               .filter(models.TaskNode.id == node_id,
                       models.TaskNode.status == "pending")
               .update({"status": "running"}, synchronize_session=False))
    db.commit()
    if claimed != 1:
        db.refresh(node)
        return False, "STATE_ERROR", f"节点当前状态为 {node.status}，不能重复执行"
    db.refresh(node)
    agents = db.query(models.Agent).all()
    refresh_live_loads(db, agents)
    prior = db.query(models.SchedulerDecision).filter_by(task_id=task.id).count()
    decision = select_agent(agents, node.required_capabilities, node.required_tools,
                            priority=task.priority, mode=task.scheduler_mode,
                            rotation_index=prior)
    # Plan confirmation: when the user pinned this node's assignee before
    # execution (PUT /tasks/{id}/nodes/{nid}/assign) and that agent is
    # still active, honor the pin. The scheduler's candidate scores are
    # still recorded below, so the decision stays auditable; a missing or
    # disabled pinned agent falls back to the scheduler's own choice.
    if node.assigned_agent_id:
        pinned_agent = db.query(models.Agent).get(node.assigned_agent_id)
        if pinned_agent is not None and pinned_agent.status == "active":
            decision = Decision(
                selected=pinned_agent, candidates=decision.candidates,
                reason=(f"计划确认时由用户指派：员工 {pinned_agent.id}；"
                        f"调度预览参考：{decision.reason}"))
    db.add(models.SchedulerDecision(task_id=task.id, node_id=node.id, mode=task.scheduler_mode,
                                    candidates=decision.candidates,
                                    selected_agent_id=decision.selected.id if decision.selected else None,
                                    reason=decision.reason))
    db.commit()
    if decision.selected is None:
        node.status = "failed"; node.error_type = "DEPENDENCY_ERROR"
        node.error_message = decision.reason; node.finished_at = datetime.utcnow()
        db.commit()
        return False, "DEPENDENCY_ERROR", decision.reason
    agent = db.query(models.Agent).get(decision.selected.id)
    node.assigned_agent_id = agent.id
    node.status = "running"; node.started_at = datetime.utcnow()
    agent.runtime_status = "Running"
    db.commit()
    _message(db, task, None, agent.id, "TASK_ASSIGNMENT", f"节点「{node.name}」已指派给 {agent.name}")
    started = time.time()
    if node.node_type == "tool":
        # Tool node (e.g. compiled from a workflow graph): execute through the
        # unified Tool Registry with the assigned agent's permissions — no LLM.
        from app.tools.registry import execute_tool, ToolError
        try:
            cfg = json.loads(node.input_data or "{}")
        except Exception:
            cfg = {}
        tool_id = cfg.get("tool_id")
        targs = dict(cfg.get("tool_args") or {})
        # The executing agent's identity is trusted context, never a
        # model/config-supplied argument: a node config must not be able
        # to act under another agent's id.
        targs["agent_id"] = agent.id
        try:
            result = execute_tool(tool_id, targs, agent.tools,
                                  approved=cfg.get("approved") is True)
            node.output_data = json.dumps(result, ensure_ascii=False, default=str)
            node.status = "completed"; node.finished_at = datetime.utcnow()
            try:
                db.refresh(agent)
            except Exception:
                pass
            update_feedback(agent, True, latency=time.time() - started, cost=0.0)
            _release_agent(db, agent, node)
            db.commit()
            _message(db, task, agent.id, None, "TASK_RESULT",
                     f"工具节点「{node.name}」已由 {agent.name} 执行")
            _snapshot(db, task)
            return True, None, None
        except ToolError as e:
            try:
                db.refresh(agent)
            except Exception:
                pass
            update_feedback(agent, False, latency=time.time() - started)
            _release_agent(db, agent, node, failed=True)
            node.status = "failed"; node.error_type = e.error_type
            node.error_message = str(e); node.finished_at = datetime.utcnow()
            db.commit()
            _message(db, task, agent.id, None, "ERROR_REPORT",
                     f"工具节点「{node.name}」执行失败：{e}")
            _snapshot(db, task)
            return False, e.error_type, str(e)
    if node.node_type in ("condition", "approval", "verify_structural"):
        # Deterministic node kinds compiled from workflow graphs. They run
        # through the same assignment/audit path but never call a model or
        # a tool: each evaluates something real and records the evidence.
        try:
            cfg = json.loads(node.input_data or "{}")
        except Exception:
            cfg = {}
        inner = cfg.get("config") or {}
        if node.node_type == "condition":
            result, detail = _eval_condition(db, task, node, inner)
            out = json.dumps({"condition": inner.get("condition"), "result": result,
                              "detail": detail}, ensure_ascii=False)
            if result or str(inner.get("on_false") or "stop") == "continue":
                return _finish_simple_node(db, task, node, agent, started, out)
            return _finish_simple_node(db, task, node, agent, started, out, ok=False,
                                       error_type="CONDITION_NOT_MET",
                                       error_message=f"条件不成立：{inner.get('condition')}（{detail}）")
        if node.node_type == "approval":
            if cfg.get("approved") is True:
                out = json.dumps({"approved": True, "note": inner.get("note") or "",
                                  "approval_note": cfg.get("approval_note") or "",
                                  "decided_by": "用户",
                                  "decided_at": cfg.get("approved_at") or ""},
                                 ensure_ascii=False)
                return _finish_simple_node(db, task, node, agent, started, out)
            return _finish_simple_node(db, task, node, agent, started, None, ok=False,
                                       error_type="APPROVAL_REQUIRED",
                                       error_message="这个节点需要人工审批：请在工作流页运行时先确认审批，批准后才能继续执行")
        upstream = [u for u in db.query(models.TaskNode).filter_by(task_id=task.id).all()
                    if u.id in (node.dependencies or [])]
        problems = []
        for u in upstream:
            if not u.output_data:
                problems.append(f"上游节点「{u.name}」没有输出")
                continue
            try:
                parsed = json.loads(u.output_data)
                if isinstance(parsed, dict) and not any(
                        k in parsed for k in ("summary", "result", "files")):
                    problems.append(f"上游节点「{u.name}」的输出缺少关键字段")
            except Exception:
                pass  # non-JSON output still counts as present output
        out = json.dumps({"mode": "structural", "passed": not problems,
                          "problems": problems}, ensure_ascii=False)
        if problems:
            return _finish_simple_node(db, task, node, agent, started, out, ok=False,
                                       error_type="VERIFICATION_ERROR",
                                       error_message="结构验证未通过：" + "；".join(problems))
        return _finish_simple_node(db, task, node, agent, started, out)
    try:
        provider = None
        if agent.model_provider_id:
            provider = db.query(models.ModelProvider).get(agent.model_provider_id)
        upstream = db.query(models.TaskNode).filter_by(task_id=task.id).all()
        context = "\n".join(f"[{u.name}] {u.output_data}" for u in upstream
                            if u.output_data and u.id in (node.dependencies or []))
        try:
            node_cfg = (json.loads(node.input_data or "{}").get("config") or {})
        except Exception:
            node_cfg = {}
        instruction = str(node_cfg.get("instruction") or "").strip()
        # Automatic knowledge-base retrieval: relevant chunks join the
        # context, and each cited document is named in the task's message
        # log so the user can see what the agent leaned on.
        kb_hits = _knowledge_hits(db, task, node)
        kb_text = ""
        if kb_hits:
            lines = []
            cited = set()
            for hit in kb_hits:
                item = db.query(models.MemoryItem).get(hit["item_id"])
                title = (item.title if item and item.title else
                         (hit["content"].splitlines()[0][:40] if hit["content"] else f"文档 {hit['item_id']}"))
                lines.append(f"《{title}》：{hit['chunk']}")
                if hit["item_id"] not in cited:
                    cited.add(hit["item_id"])
                    _message(db, task, agent.id, None, "KNOWLEDGE_REFERENCE",
                             f"节点「{node.name}」引用了知识库文档《{title}》的相关分片")
            kb_text = "\nKnowledge base references:\n" + "\n".join(lines)
        prompt = (f"Task: {task.title}\n{task.description}\nStep: {node.name} "
                  f"(capabilities: {', '.join(node.required_capabilities)})\n"
                  + (f"Step instruction: {instruction}\n" if instruction else "")
                  + f"Upstream results:\n{context}\n"
                  + (kb_text + "\n" if kb_text else "")
                  + "Return a JSON object with keys: summary, result.")
        res = chat_completion(provider, agent.model_id,
                              [{"role": "system", "content": _effective_system_prompt(db, agent)},
                               {"role": "user", "content": prompt}])
        node.output_data = res["content"]
        # Atomic increment in SQL: parallel nodes each hold their own
        # session copy of the task, so a Python read-modify-write here
        # silently loses the other nodes' usage.
        from sqlalchemy import func as _func
        (db.query(models.Task).filter_by(id=task.id)
         .update({models.Task.token_usage:
                  _func.coalesce(models.Task.token_usage, 0) + res["token_usage"]},
                 synchronize_session=False))
        db.add(models.ExecutionSnapshot(task_id=task.id, state={
            "node_token_usage": {"node": node.name, "agent_id": agent.id,
                                 "prompt_tokens": res.get("prompt_tokens", 0),
                                 "completion_tokens": res.get("completion_tokens", 0),
                                 "total_tokens": res["token_usage"]}}))
        node.status = "completed"; node.finished_at = datetime.utcnow()
        latency = time.time() - started
        try:
            db.refresh(agent)
        except Exception:
            pass
        update_feedback(agent, True, latency=latency, cost=res["token_usage"] / 1000.0)
        _release_agent(db, agent, node)
        db.commit()
        _message(db, task, agent.id, None, "TASK_RESULT", f"节点「{node.name}」已由 {agent.name} 完成")
        _snapshot(db, task)
        return True, None, None
    except LLMError as e:
        latency = time.time() - started
        try:
            db.refresh(agent)
        except Exception:
            pass
        update_feedback(agent, False, latency=latency)
        _release_agent(db, agent, node, failed=True)
        node.status = "failed"; node.error_type = "MODEL_ERROR"
        node.error_message = str(e); node.finished_at = datetime.utcnow()
        db.commit()
        _message(db, task, agent.id, None, "ERROR_REPORT", f"节点「{node.name}」失败：{e}")
        _snapshot(db, task)
        return False, "MODEL_ERROR", str(e)
    except Exception as e:
        et = classify_error(str(e)).value
        # Same convergence as the LLMError branch: without feedback and
        # _release_agent, the agent's runtime_status stays "Running"
        # forever and the UI reports it as working when nothing is.
        try:
            db.refresh(agent)
        except Exception:
            pass
        update_feedback(agent, False, latency=time.time() - started)
        _release_agent(db, agent, node, failed=True)
        node.status = "failed"; node.error_type = et
        node.error_message = str(e); node.finished_at = datetime.utcnow()
        db.commit()
        _message(db, task, agent.id, None, "ERROR_REPORT", f"节点「{node.name}」失败：{e}")
        _snapshot(db, task)
        return False, et, str(e)

async def _run_dag_once(task_id, make_session=None):
    """One full DAG pass. Ready nodes (dependencies completed) run in parallel,
    each in its own thread + DB session. make_session is the factory bound to
    the task's own database (see _session_factory); None falls back to the
    global SessionLocal for direct callers only (see _run_node)."""
    if make_session is None:
        from app.database import SessionLocal
        make_session = SessionLocal
    errors = []
    while True:
        db = make_session()
        try:
            nodes = db.query(models.TaskNode).filter_by(task_id=task_id).all()
            snapshot = [(n.id, n.status, list(n.dependencies or [])) for n in nodes]
        finally:
            db.close()
        pending = [x for x in snapshot if x[1] == "pending"]  # failed nodes are reset by Self-Loop between attempts, never re-run inside one pass
        if not pending:
            break
        done_ids = {x[0] for x in snapshot if x[1] == "completed"}
        ready = [x for x in pending if set(x[2]) <= done_ids]
        if not ready:
            errors.append("dependency deadlock: no runnable nodes")
            break
        results = await asyncio.gather(*[
            asyncio.to_thread(_run_node, task_id, x[0], make_session) for x in ready])
        for (ok, et, msg) in results:
            if not ok:
                errors.append(f"{et}: {msg}")
        db = make_session()
        try:
            nodes = db.query(models.TaskNode).filter_by(task_id=task_id).all()
            any_failed = any(n.status == "failed" for n in nodes)
            any_pending = any(n.status == "pending" for n in nodes)
        finally:
            db.close()
        if any_failed and not any_pending:
            break
    db = make_session()
    try:
        failed = db.query(models.TaskNode).filter_by(task_id=task_id, status="failed").all()
        failed_info = [(n.name, n.error_type, n.error_message) for n in failed]
        # A pass that ends with nodes neither completed nor failed (a
        # dependency cycle, a dependency on a missing node, a node stuck
        # in running) did NOT succeed — reporting success here would let
        # verification bless a half-executed DAG.
        incomplete = (db.query(models.TaskNode).filter_by(task_id=task_id)
                      .filter(models.TaskNode.status.notin_(["completed", "failed"]))
                      .all())
        incomplete_info = [(n.name, "DEPENDENCY_ERROR",
                            "节点未执行完成：依赖未满足、依赖成环或依赖了不存在的节点")
                           for n in incomplete]
    finally:
        db.close()
    return (not failed_info and not incomplete_info), failed_info + incomplete_info

def run_task(db, task, max_retries=2, required_output_fields=None):
    """Synchronous entry used by the API. Wraps DAG passes in the Self-Loop.
    required_output_fields: structural-verify keys expected in the combined
    node outputs; defaults to the built-in plan/execute/verify spec. Workflow
    executions pass their own node names (graph-compiled DAGs differ).

    The task is claimed with ONE conditional UPDATE: if another execution
    already owns it (status == "running"), this call refuses instead of
    running the same DAG twice and letting the two runs overwrite each
    other. Any unexpected exception after the claim converges the task to
    "failed" in a clean session — a task must never be left "running"
    with nothing behind it."""
    claimed = (db.query(models.Task)
               .filter(models.Task.id == task.id,
                       models.Task.status != "running")
               .update({models.Task.status: "running"},
                       synchronize_session=False))
    db.commit()
    if claimed != 1:
        raise RuntimeError("任务正在执行中，不能重复启动")
    db.expire(task)
    try:
        return _run_task_claimed(db, task, max_retries, required_output_fields)
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        # Clean session on the SAME database as the task (see _session_factory):
        # resolving the global SessionLocal here could land in another DB.
        db2 = _session_factory(db)()
        try:
            t2 = db2.query(models.Task).get(task.id)
            if t2 is not None and t2.status == "running":
                t2.status = "failed"
                t2.error_type = classify_error(str(e)).value
                t2.error_message = str(e)[:500]
                db2.commit()
        except Exception:
            try:
                db2.rollback()
            except Exception:
                pass
        finally:
            db2.close()
        raise


def _run_task_claimed(db, task, max_retries=2, required_output_fields=None):
    """Body of run_task, executed after the task has been claimed."""
    build_dag(db, task)
    _snapshot(db, task)
    state_holder = {}
    # One factory for the whole run, bound to this task's database: every
    # DAG pass / node thread session comes from it (see _session_factory).
    make_session = _session_factory(db)

    def execute(attempt):
        for n in db.query(models.TaskNode).filter_by(task_id=task.id).all():
            if n.status != "completed":
                n.status = "pending"; n.error_type = None; n.error_message = None
        db.commit()
        ok, failed = asyncio.run(_run_dag_once(task.id, make_session))
        state_holder["failed"] = failed
        if not ok and failed:
            _name, et, msg = failed[0]
            # The error type is already stored on the task/node
            # (task.error_type); the exception message itself is shown
            # to users (task Toast / group-chat system message), so it
            # must be the human message alone, not "MODEL_ERROR: ...".
            raise RuntimeError(msg or "节点执行失败")
        outputs = {n.name: n.output_data for n in
                   db.query(models.TaskNode).filter_by(task_id=task.id).all()}
        return json.dumps(outputs, ensure_ascii=False)

    def verify(output):
        try:
            data = json.loads(output)
            # An explicit empty list means "no required fields" — only
            # None falls back to the built-in plan/execute/verify spec.
            res = structural_verify(
                data, required_fields=(["plan", "execute", "verify"]
                                       if required_output_fields is None
                                       else required_output_fields))
        except Exception:
            res = structural_verify(output)
        db.add(models.VerificationRecord(task_id=task.id, verification_type="structural",
                                         passed=res.passed, score=res.score,
                                         evidence=res.evidence, errors=res.errors))
        db.commit()
        state_holder["verify_failed"] = not res.passed
        return res

    def repair(errors, attempt):
        task.retry_count = (task.retry_count or 0) + 1
        if state_holder.pop("verify_failed", False):
            # The failure was verification rejecting COMPLETED nodes'
            # outputs. execute() only resets non-completed nodes, so
            # without this the next attempt would re-verify byte-identical
            # output and die as a "repeated error" without ever retrying
            # the work. Reset the completed nodes so the retry actually
            # produces new output.
            for n in db.query(models.TaskNode).filter_by(task_id=task.id).all():
                if n.status == "completed":
                    n.status = "pending"; n.output_data = None
                    n.error_type = None; n.error_message = None
                    n.started_at = None; n.finished_at = None
        db.commit()

    loop = SelfLoop(max_retries=max_retries, max_seconds=900)
    final, _output, history = loop.run(execute, verify, repair)
    db.add(models.ExecutionSnapshot(task_id=task.id, state={"self_loop_history": history}))
    if final == "COMPLETE":
        task.status = "completed"
        outs = {n.name: n.output_data for n in
                db.query(models.TaskNode).filter_by(task_id=task.id).all()}
        task.result = json.dumps(outs, ensure_ascii=False)
        task.error_type = None; task.error_message = None
    else:
        task.status = "failed"
        last_err = next((h for h in reversed(history) if h.get("error_type")), {})
        task.error_type = last_err.get("error_type", "MODEL_ERROR")
        # Surface the root-cause node error, not just the loop's generic detail.
        failed_node = next((n for n in db.query(models.TaskNode).filter_by(task_id=task.id).all()
                            if n.status == "failed" and n.error_message), None)
        task.error_message = (failed_node.error_message if failed_node
                              else last_err.get("detail", "自循环结束时仍未成功"))
    db.commit()
    _snapshot(db, task)
    return task
