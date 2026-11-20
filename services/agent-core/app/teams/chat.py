"""Team channel chat on top of AgentMessage (original implementation).

One channel per team, stored as AgentMessage rows with team_id set and
meta.channel == 'team':
  - a posted message is stored once as message_type='CHANNEL_MESSAGE';
  - @mentions are parsed from the content: '@<agent name>' (team members
    only, longest name first so overlapping names resolve correctly) and
    the explicit form '@agent:<id>'. Each resolved mention ALSO creates a
    directed copy (message_type='MENTION', receiver_agent_id=<agent>,
    meta.source_message_id=<channel message id>) so an agent can poll
    exactly the messages that target it.
Sender may be a user (sender_agent_id=None) or a team-member agent.
"""
import re

from app import models

CHANNEL = "team"
_ID_MENTION = re.compile(r"@agent:(\d+)")


class ChatError(Exception):
    pass


def _members(db, team_id):
    rows = db.query(models.TeamMember).filter_by(team_id=team_id).all()
    ids = [r.agent_id for r in rows]
    team = db.query(models.Team).get(team_id)
    if team and team.leader_agent_id and team.leader_agent_id not in ids:
        ids.append(team.leader_agent_id)
    agents = {}
    for aid in ids:
        a = db.query(models.Agent).get(aid)
        if a is not None:
            agents[aid] = a
    return agents


def parse_mentions(content, members):
    """members: {agent_id: Agent}. Returns ordered unique mentioned ids."""
    found = []
    content = content or ""
    for m in _ID_MENTION.finditer(content):
        aid = int(m.group(1))
        if aid in members and aid not in found:
            found.append(aid)
    # Name mentions: longest names first, and a matched span is consumed
    # — otherwise "@林晓" would ALSO match a shorter-named member "林"
    # whose needle is a prefix of the longer mention.
    spans = []
    for aid, agent in sorted(members.items(), key=lambda kv: -len(kv[1].name or "")):
        if not agent.name:
            continue
        needle = f"@{agent.name}"
        start = 0
        while True:
            i = content.find(needle, start)
            if i < 0:
                break
            span = (i, i + len(needle))
            if not any(s < span[1] and span[0] < e for s, e in spans):
                spans.append(span)
                if aid not in found:
                    found.append(aid)
                break
            start = i + 1
    return found


def _out(m):
    return {"message_id": m.message_id, "team_id": m.team_id, "task_id": m.task_id,
            "sender_agent_id": m.sender_agent_id,
            "receiver_agent_id": m.receiver_agent_id,
            "message_type": m.message_type, "content": m.content,
            "meta": m.meta or {},
            "timestamp": m.timestamp.isoformat() if getattr(m, "timestamp", None) else None}


def post_message(db, team_id, content, sender_agent_id=None, mentions=None,
                 mode="direct", reply_to=None):
    team = db.query(models.Team).get(team_id)
    if team is None:
        raise ChatError(f"团队不存在：{team_id}")
    if not (content or "").strip():
        raise ChatError("消息内容不能为空")
    members = _members(db, team_id)
    if sender_agent_id is not None and sender_agent_id not in members:
        raise ChatError(f"发送者员工 {sender_agent_id} 不是团队 {team_id} 的成员")
    # Structured mentions from the client win; text parsing is the fallback
    # and only ever ADDS members the client did not name.
    resolved = []
    for aid in mentions or []:
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            continue  # a malformed id names nobody; never crash the post
        if aid in members and aid not in resolved:
            resolved.append(aid)
    for aid in parse_mentions(content, members):
        if aid not in resolved:
            resolved.append(aid)
    mentions = resolved
    if mode not in ("direct", "chief", "discuss"):
        # Never silently downgrade: a misspelled mode must surface as an
        # error, not execute as a direct dispatch the user never asked for.
        raise ChatError(f"不支持的群聊模式：{mode}")
    msg = models.AgentMessage(
        team_id=team_id, task_id=None, sender_agent_id=sender_agent_id,
        receiver_agent_id=None, message_type="CHANNEL_MESSAGE", content=content,
        meta={"channel": CHANNEL, "mentions": mentions, "mode": mode,
              "reply_to": reply_to})
    # The channel message and its directed copies are ONE transaction:
    # committing the main message first could leave it stored without
    # its mention copies when the second write failed.
    db.add(msg)
    db.flush()
    directed = []
    for aid in mentions:
        if aid == sender_agent_id:
            continue
        d = models.AgentMessage(
            team_id=team_id, task_id=None, sender_agent_id=sender_agent_id,
            receiver_agent_id=aid, message_type="MENTION", content=content,
            meta={"channel": CHANNEL, "mentioned": True,
                  "source_message_id": msg.message_id})
        db.add(d)
        directed.append(d)
    db.commit()
    db.refresh(msg)
    for d in directed:
        db.refresh(d)
    out = {"message": _out(msg), "mentions": mentions,
           "directed": [_out(d) for d in directed]}
    # Only USER messages dispatch work; agent/system posts never retrigger
    # (loop safety lives in app/teams/dispatch.py).
    if sender_agent_id is None:
        from app.teams import dispatch
        try:
            out["dispatch"] = dispatch.dispatch_user_message(
                db, team_id, msg, mentions, mode, members)
        except Exception as e:
            # The message IS saved. A dispatch failure must not surface
            # as a failed post — the client would retry and duplicate
            # the message. Report the failure inside the result instead.
            db.rollback()
            out["dispatch"] = {"mode": mode, "error": str(e)}
    return out


def list_messages(db, team_id, limit=100, include_directed=False):
    if db.query(models.Team).get(team_id) is None:
        raise ChatError(f"团队不存在：{team_id}")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    q = db.query(models.AgentMessage).filter_by(team_id=team_id)
    if not include_directed:
        q = q.filter(models.AgentMessage.message_type != "MENTION")
    # Filter in SQL (channel marker + type) BEFORE limiting: fetching a
    # flat 500 rows and filtering in Python silently dropped older
    # channel messages whenever other rows interleaved.
    q = q.filter(models.AgentMessage.meta["channel"].as_string() == CHANNEL)
    rows = q.order_by(models.AgentMessage.timestamp.desc(),
                      models.AgentMessage.message_id.desc()).limit(limit).all()
    return [_out(m) for m in reversed(rows)]


def pin_message(db, team_id, message_id):
    """Toggle the pinned flag on a channel message (group memory: pinned
    messages form the channel's durable highlights)."""
    m = db.query(models.AgentMessage).filter_by(
        team_id=team_id, message_id=message_id).first()
    if m is None or (m.meta or {}).get("channel") != CHANNEL:
        raise ChatError(f"消息不存在：{message_id}")
    meta = dict(m.meta or {})
    meta["pinned"] = not meta.get("pinned")
    m.meta = meta
    db.commit()
    db.refresh(m)
    return _out(m)


def channel_summary(db, team_id):
    """Extractive channel digest: pinned messages + the latest work
    cards. Nothing is generated — every line is a real stored message."""
    if db.query(models.Team).get(team_id) is None:
        raise ChatError(f"团队不存在：{team_id}")
    rows = (db.query(models.AgentMessage).filter_by(team_id=team_id)
            .order_by(models.AgentMessage.timestamp.desc(),
                      models.AgentMessage.message_id.desc()).limit(500).all())
    pinned, cards = [], []
    for m in rows:
        if (m.meta or {}).get("channel") != CHANNEL:
            continue
        if (m.meta or {}).get("pinned"):
            pinned.append(_out(m))
        if m.message_type in ("TASK_CARD", "RESULT_CARD") and len(cards) < 10:
            cards.append(_out(m))
    return {"pinned": list(reversed(pinned)), "cards": list(reversed(cards))}


def list_mentions(db, team_id, agent_id):
    if db.query(models.Team).get(team_id) is None:
        raise ChatError(f"团队不存在：{team_id}")
    rows = (db.query(models.AgentMessage)
            .filter_by(team_id=team_id, receiver_agent_id=agent_id,
                       message_type="MENTION")
            .order_by(models.AgentMessage.timestamp.asc()).all())
    return [_out(m) for m in rows if (m.meta or {}).get("channel") == CHANNEL]
