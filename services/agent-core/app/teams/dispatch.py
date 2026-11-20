"""Group-chat dispatch: turns channel messages into real work.

Three modes, all wired to existing engines (nothing re-implemented):
  - direct: a user message that @mentions members creates one real Task
    per mentioned member (single node pinned to that member) and runs it
    through app.execution.runner.run_task on a background thread. A
    TASK_CARD message is posted immediately and a RESULT_CARD when the
    run finishes, both stored as ordinary channel messages.
  - chief: the message becomes a chief mission via
    app.chief.service.create_mission (rule-based planning, existing
    team), with a MISSION_CARD on start and a REPORT_CARD built from
    build_report when done.
  - discuss: each member (leader first, at most MAX_DISCUSS_AGENTS)
    speaks once, in order, through the real LLM client using the
    member's own provider/model and persona. If a member has no usable
    model, one SYSTEM message says so and the round stops — replies are
    never fabricated.

Loop safety: dispatch only ever triggers on USER messages
(sender_agent_id is None). Agent posts, cards and system messages never
re-trigger dispatch, and per-message caps bound the fan-out.
"""
import threading

from app import models
from app.database import SessionLocal
from app.teams.chat import CHANNEL

MAX_DIRECT_TASKS = 3
MAX_DISCUSS_AGENTS = 4

# 任务状态 → 中文：结果卡是直接展示给用户的群聊文本，英文状态码不得原样出现。
_TASK_STATUS_ZH = {
    "pending": "待执行", "running": "执行中", "completed": "已完成",
    "failed": "失败", "cancelled": "已取消",
}


def task_status_zh(status):
    return _TASK_STATUS_ZH.get(status or "", status or "未知状态")


def _post(db, team_id, message_type, content, sender_agent_id, meta, task_id=None):
    m = models.AgentMessage(
        team_id=team_id, task_id=task_id, sender_agent_id=sender_agent_id,
        receiver_agent_id=None, message_type=message_type, content=content,
        meta={"channel": CHANNEL, **meta})
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _system(db, team_id, content):
    return _post(db, team_id, "SYSTEM", content, None, {"kind": "system"})


def _excerpt(text, limit=200):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _extract_summary(raw):
    """Best-effort human-readable summary from a node output / task result.

    Model outputs arrive in several shapes: plain JSON, the same JSON
    wrapped in a ```json fence, or {"节点名": "<fenced json string>"}.
    A fenced blob used to defeat json.loads and the result card fell back
    to the raw escaped task.result — a wall of \\n and quotes in chat.
    """
    if not raw:
        return ""
    import json as _json

    def _from_obj(obj):
        if isinstance(obj, dict):
            s = obj.get("summary")
            if isinstance(s, str) and s.strip():
                return s
            for v in obj.values():
                if isinstance(v, str):
                    r = _from_text(v)
                    if r:
                        return r
        return ""

    def _from_text(text):
        t = (text or "").strip()
        if t.startswith("```"):
            t = t.strip("`").strip()
            if t.lower().startswith("json"):
                t = t[4:].strip()
        try:
            return _from_obj(_json.loads(t))
        except Exception:
            return ""

    try:
        return _from_obj(_json.loads(raw)) or _from_text(raw)
    except Exception:
        return _from_text(raw)


# ------------------------------------------------------------------ direct

def _run_direct_task(team_id, task_id, agent_id, source_message_id):
    db = SessionLocal()
    try:
        from app.execution.runner import run_task
        task = db.query(models.Task).get(task_id)
        if task is None:
            return
        # The runner's structural verify defaults to a plan/execute/verify
        # DAG; a chat-dispatched task is a single node named 执行, so the
        # expected output fields are that node's name (the same extension
        # point the workflow engine uses for its own graphs).
        run_task(db, task, required_output_fields=["执行"])
        db.refresh(task)
        agent = db.query(models.Agent).get(agent_id)
        name = agent.name if agent else f"员工 {agent_id}"
        if task.status == "completed":
            summary = ""
            node = (db.query(models.TaskNode)
                    .filter_by(task_id=task.id, assigned_agent_id=agent_id).first())
            if node and node.output_data:
                summary = _extract_summary(node.output_data)
            content = (f"{name} 已完成任务 #{task.id}："
                       f"{_excerpt(summary, 300) or _excerpt(_extract_summary(task.result), 300) or _excerpt(task.result, 300) or '（无文字结果）'}")
        else:
            content = (f"{name} 的任务 #{task.id} 未完成（状态：{task_status_zh(task.status)}）"
                       f"{'：' + _excerpt(task.error_message, 200) if task.error_message else ''}")
        _post(db, team_id, "RESULT_CARD", content, agent_id,
              {"kind": "result_card", "task_id": task.id, "agent_id": agent_id,
               "status": task.status, "token_usage": task.token_usage or 0,
               "retry_count": task.retry_count or 0,
               "source_message_id": source_message_id},
              task_id=task.id)
    except Exception as e:  # never let a worker thread die silently
        try:
            # The session may be holding a failed transaction; writing
            # the error notice on it would fail too. Roll back first,
            # converge the task, then post.
            db.rollback()
            task = db.query(models.Task).get(task_id)
            if task is not None and task.status == "running":
                task.status = "failed"
                task.error_message = f"执行线程异常：{e}"
                db.commit()
            _system(db, team_id, f"任务 #{task_id} 执行线程异常：{e}")
        except Exception:
            pass
    finally:
        db.close()


def start_direct_tasks(db, team_id, content, mentions, source_message_id):
    """Create one pinned task per mentioned member. Returns task ids."""
    task_ids = []
    for agent_id in mentions[:MAX_DIRECT_TASKS]:
        agent = db.query(models.Agent).get(agent_id)
        if agent is None:
            continue
        title = _excerpt(content, 60) or f"群聊任务（指派给 {agent.name}）"
        task = models.Task(title=title, description=content, team_id=team_id,
                           scheduler_mode="adaptive", status="pending")
        # Task + its single node commit together: separate commits could
        # strand a task with no node (nothing for the runner to execute).
        db.add(task)
        db.flush()
        node = models.TaskNode(task_id=task.id, name="执行", node_type="agent",
                               assigned_agent_id=agent_id, status="pending",
                               input_data=content)
        db.add(node)
        db.commit()
        db.refresh(task)
        _post(db, team_id, "TASK_CARD",
              f"已为 {agent.name} 创建任务 #{task.id}：{title}",
              agent_id,
              {"kind": "task_card", "task_id": task.id, "agent_id": agent_id,
               "status": "pending", "source_message_id": source_message_id},
              task_id=task.id)
        task_ids.append(task.id)
        threading.Thread(target=_run_direct_task,
                         args=(team_id, task.id, agent_id, source_message_id),
                         daemon=True).start()
    if len(mentions) > MAX_DIRECT_TASKS:
        _system(db, team_id,
                f"一条消息最多同时指派 {MAX_DIRECT_TASKS} 位成员，其余成员未派发。")
    return task_ids


# ------------------------------------------------------------------- chief

def _run_chief_mission(team_id, goal, source_message_id):
    db = SessionLocal()
    try:
        from app.chief import service as chief_service
        mission = chief_service.create_mission(db, goal, run=True)
        report = chief_service.build_report(db, mission)
        done = sum(1 for n in report["nodes"] if n["status"] == "completed")
        content = (f"总管任务 #{report['task_id']} 已结束："
                   f"{done}/{len(report['nodes'])} 个环节完成，"
                   f"任务状态 {task_status_zh(report['task_status'])}。")
        _post(db, team_id, "RESULT_CARD", content, report["chief_agent_id"],
              {"kind": "mission_card", "task_id": report["task_id"],
               "mission_id": report["mission_id"], "status": report["task_status"],
               "nodes": report["nodes"], "source_message_id": source_message_id},
              task_id=report["task_id"])
    except Exception as e:
        try:
            db.rollback()
            _system(db, team_id, f"总管模式执行失败：{e}")
        except Exception:
            pass
    finally:
        db.close()


def start_chief_mission(db, team_id, content, source_message_id):
    _post(db, team_id, "TASK_CARD",
          f"总管已接单，正在组队规划：{_excerpt(content, 80)}",
          None, {"kind": "mission_start", "status": "planning",
                 "source_message_id": source_message_id})
    threading.Thread(target=_run_chief_mission,
                     args=(team_id, content, source_message_id),
                     daemon=True).start()


# ----------------------------------------------------------------- discuss

def _run_discussion(team_id, topic, member_ids, source_message_id):
    db = SessionLocal()
    try:
        from app.llm.client import chat_completion, LLMError
        spoken = []
        for agent_id in member_ids[:MAX_DISCUSS_AGENTS]:
            agent = db.query(models.Agent).get(agent_id)
            if agent is None:
                continue
            provider = None
            if agent.model_provider_id:
                provider = db.query(models.ModelProvider).get(agent.model_provider_id)
            persona = agent.system_prompt or (
                f"你是 {agent.name}，岗位是 {agent.role or '团队成员'}。"
                f"职责说明：{agent.description or '按团队目标协作'}。"
                "你在团队群聊里参加讨论，请结合你的岗位，用中文简短发言（150 字以内），"
                "给出明确观点或建议，不要重复前面同事已经说过的话。")
            context = "\n".join(f"{n}：{c}" for n, c in spoken)
            user = f"讨论话题：{topic}"
            if context:
                user += f"\n\n前面同事的发言：\n{context}"
            try:
                res = chat_completion(provider, agent.model_id,
                                      [{"role": "system", "content": persona},
                                       {"role": "user", "content": user}])
            except LLMError as e:
                _system(db, team_id,
                        f"讨论中断：{agent.name} 还不能发言（{e}）。"
                        "讨论模式需要为成员配置可用的模型供应商与模型。")
                return
            text = (res.get("content") or "").strip()
            if not text:
                continue
            spoken.append((agent.name, text))
            _post(db, team_id, "CHANNEL_MESSAGE", text, agent_id,
                  {"kind": "discussion", "source_message_id": source_message_id})
        if not spoken:
            _system(db, team_id, "讨论没有产生发言：成员可能尚未配置模型。")
    except Exception as e:
        try:
            db.rollback()
            _system(db, team_id, f"讨论模式执行异常：{e}")
        except Exception:
            pass
    finally:
        db.close()


def start_discussion(db, team_id, content, member_ids, source_message_id):
    threading.Thread(target=_run_discussion,
                     args=(team_id, content, member_ids, source_message_id),
                     daemon=True).start()


# -------------------------------------------------------------------- hook

def dispatch_user_message(db, team_id, message, mentions, mode, members):
    """Called by chat.post_message after a USER message is stored.

    members: {agent_id: Agent} of the channel's team.
    """
    mode = mode if mode in ("direct", "chief", "discuss") else "direct"
    if mode == "chief":
        start_chief_mission(db, team_id, message.content, message.message_id)
        return {"mode": "chief"}
    if mode == "discuss":
        ordered = []
        team = db.query(models.Team).get(team_id)
        if team and team.leader_agent_id in members:
            ordered.append(team.leader_agent_id)
        for aid in mentions:
            if aid in members and aid not in ordered:
                ordered.append(aid)
        if not ordered:
            ordered = list(members.keys())
        start_discussion(db, team_id, message.content, ordered, message.message_id)
        return {"mode": "discuss", "speakers": ordered[:MAX_DISCUSS_AGENTS]}
    if mentions:
        task_ids = start_direct_tasks(db, team_id, message.content, mentions,
                                      message.message_id)
        return {"mode": "direct", "task_ids": task_ids}
    return {"mode": "direct", "task_ids": []}
