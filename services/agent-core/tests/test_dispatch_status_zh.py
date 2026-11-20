"""回归：群聊结果卡里的任务状态必须显示中文，不得原样输出英文状态码。

背景（2026-11-19 第六轮）：_run_direct_task 的未完成卡写
「未完成（状态：failed）」，_run_chief_mission 的汇报卡写
「任务状态 completed」，英文状态码直接展示给群聊用户。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.teams.dispatch import _run_direct_task, task_status_zh


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "dispatch.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    # _run_direct_task 在后台线程式路径里用模块级 SessionLocal；
    # 不打补丁会落进真实库（见 test_team_chat.py 的同款说明）。
    import app.database as database
    from app.teams import dispatch
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(dispatch, "SessionLocal", TestSession)
    db = TestSession()
    yield db
    db.close()


def test_task_status_zh_maps_all_known_statuses():
    assert task_status_zh("pending") == "待执行"
    assert task_status_zh("running") == "执行中"
    assert task_status_zh("completed") == "已完成"
    assert task_status_zh("failed") == "失败"
    assert task_status_zh("cancelled") == "已取消"
    assert task_status_zh("") == "未知状态"
    assert task_status_zh(None) == "未知状态"
    # 未知码如实原样返回，不在映射里编造。
    assert task_status_zh("weird") == "weird"


def test_direct_task_result_card_shows_chinese_status(dbx):
    team = models.Team(name="测试团队", description="", leader_agent_id=None)
    dbx.add(team)
    dbx.commit()
    agent = models.Agent(name="陈工", role="developer", tools=[],
                         capability_tags=[], capability_scores={},
                         status="active")
    dbx.add(agent)
    dbx.commit()
    task = models.Task(title="测试任务", description="x", team_id=team.id,
                       scheduler_mode="adaptive", status="pending")
    dbx.add(task)
    dbx.flush()
    node = models.TaskNode(task_id=task.id, name="执行", node_type="agent",
                           assigned_agent_id=agent.id, status="pending",
                           input_data="做点事")
    dbx.add(node)
    dbx.commit()
    # 成员未配模型：节点以 MODEL_ERROR 快速失败，不产生真实模型调用。
    _run_direct_task(team.id, task.id, agent.id, 1)
    card = (dbx.query(models.AgentMessage)
            .filter_by(team_id=team.id, message_type="RESULT_CARD").first())
    assert card is not None
    assert "状态：失败" in card.content
    assert "failed" not in card.content
