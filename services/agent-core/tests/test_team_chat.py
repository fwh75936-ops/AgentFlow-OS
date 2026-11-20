"""Team channel chat + @mention tests (on real AgentMessage rows)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.teams import chat


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "chat.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    # Dispatch runs on background threads through SessionLocal — and
    # app.teams.dispatch binds it at import time, so patching
    # app.database.SessionLocal alone does NOT redirect those threads.
    # Without both patches, a @mention test's worker thread runs the
    # REAL task whose id collides with the temp task's id (real LLM
    # calls, real token spend, real task mutation on every full run).
    import app.database as database
    from app.teams import dispatch
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(dispatch, "SessionLocal", TestSession)
    db = TestSession()
    yield db
    db.close()


def setup_team(db):
    pm = models.Agent(name="林晓", role="pm", tools=[], capability_tags=[],
                      capability_scores={}, status="active")
    dev = models.Agent(name="陈工", role="dev", tools=[], capability_tags=[],
                       capability_scores={}, status="active")
    qa = models.Agent(name="苏晴", role="qa", tools=[], capability_tags=[],
                      capability_scores={}, status="active")
    outsider = models.Agent(name="外人", role="x", tools=[], capability_tags=[],
                            capability_scores={}, status="active")
    db.add_all([pm, dev, qa, outsider]); db.commit()
    team = models.Team(name="演示修复小队", leader_agent_id=pm.id)
    db.add(team); db.commit(); db.refresh(team)
    for a in (pm, dev, qa):
        db.add(models.TeamMember(team_id=team.id, agent_id=a.id,
                                 is_leader=(a.id == pm.id)))
    db.commit()
    return team, pm, dev, qa, outsider


def test_post_and_list_channel(dbx):
    team, pm, dev, qa, _o = setup_team(dbx)
    r = chat.post_message(dbx, team.id, "大家好，开始今天的任务")
    assert r["mentions"] == [] and r["directed"] == []
    chat.post_message(dbx, team.id, "收到", sender_agent_id=dev.id)
    msgs = chat.list_messages(dbx, team.id)
    assert [m["content"] for m in msgs] == ["大家好，开始今天的任务", "收到"]
    assert msgs[0]["sender_agent_id"] is None  # user message
    assert msgs[1]["sender_agent_id"] == dev.id


def test_mention_creates_directed_message(dbx):
    team, pm, dev, qa, _o = setup_team(dbx)
    r = chat.post_message(dbx, team.id, "@陈工 权限 Bug 修一下，@苏晴 之后复验")
    assert set(r["mentions"]) == {dev.id, qa.id}
    assert len(r["directed"]) == 2
    dev_inbox = chat.list_mentions(dbx, team.id, dev.id)
    assert len(dev_inbox) == 1
    assert dev_inbox[0]["message_type"] == "MENTION"
    assert dev_inbox[0]["meta"]["source_message_id"] == r["message"]["message_id"]
    # channel list hides directed copies by default, shows them on request.
    # Since the group-chat dispatch upgrade, a user @message ALSO creates
    # one real task per mentioned member, announced by a TASK_CARD in the
    # channel: 1 user message + 2 task cards = 3 visible (5 with directed).
    assert len(chat.list_messages(dbx, team.id)) == 3
    assert len(chat.list_messages(dbx, team.id, include_directed=True)) == 5


def test_mention_id_form_and_non_member_ignored(dbx):
    team, pm, dev, qa, outsider = setup_team(dbx)
    r = chat.post_message(dbx, team.id, f"@agent:{qa.id} 请看一下")
    assert r["mentions"] == [qa.id]
    r2 = chat.post_message(dbx, team.id, "@外人 不在团队里")
    assert r2["mentions"] == [] and r2["directed"] == []


def test_sender_must_be_member_and_team_must_exist(dbx):
    team, pm, dev, qa, outsider = setup_team(dbx)
    with pytest.raises(chat.ChatError):
        chat.post_message(dbx, team.id, "hi", sender_agent_id=outsider.id)
    with pytest.raises(chat.ChatError):
        chat.post_message(dbx, 9999, "hi")
    with pytest.raises(chat.ChatError):
        chat.post_message(dbx, team.id, "   ")
