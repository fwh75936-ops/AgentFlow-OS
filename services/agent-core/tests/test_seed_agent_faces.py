"""回归：种子员工（产品经理/开发工程师/测试工程师）创建时必须带头像与形象。

背景（2026-11-19 第二轮）：开发库三行是人工 SQL 回填的头像数据，
但两处代码创建点——总管 ensure_team 与 workspace seed_demo——建人时
仍不带 avatar/appearance，新库又会缺头像，全站回退单字色圈。
两处共用 app/seed_faces.py 同一份映射；旧行缺失时幂等回填，
已有配置不覆盖。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.chief.service import ensure_team
from app.database import Base
from app.seed_faces import SEED_AGENT_FACES
from app.workspace.routes import _DEMO_AGENTS, seed_demo


@pytest.fixture()
def db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "seed.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield session
    session.close()


def _faces(db):
    rows = db.query(models.Agent).filter(
        models.Agent.name.in_(list(SEED_AGENT_FACES))).all()
    return {a.name: (a.avatar, a.appearance) for a in rows}


def test_ensure_team_assigns_seed_faces(db):
    ensure_team(db)
    faces = _faces(db)
    assert set(faces) == set(SEED_AGENT_FACES)
    for name, (avatar, appearance) in SEED_AGENT_FACES.items():
        assert faces[name] == (avatar, appearance)
    # 总管不在映射里：不给总管乱配头像。
    chief = db.query(models.Agent).filter_by(name="AI总管").first()
    assert chief is not None
    assert not chief.avatar
    assert not chief.appearance


def test_ensure_team_backfills_missing_faces_without_overwriting(db):
    # 旧行：两人缺头像、一人已有自定义配置。
    for name in ("产品经理", "开发工程师"):
        db.add(models.Agent(name=name, role="product", status="active"))
    db.add(models.Agent(name="测试工程师", role="tester", status="active",
                        avatar="avatar-09",
                        appearance={"gender": "female", "skin": 5,
                                    "outfit": 9, "hair": 2}))
    db.commit()
    ensure_team(db)
    ensure_team(db)  # 幂等：跑两遍结果一致
    faces = _faces(db)
    assert faces["产品经理"] == SEED_AGENT_FACES["产品经理"]
    assert faces["开发工程师"] == SEED_AGENT_FACES["开发工程师"]
    assert faces["测试工程师"] == (
        "avatar-09",
        {"gender": "female", "skin": 5, "outfit": 9, "hair": 2})


def test_seed_demo_assigns_faces_and_chinese_description(db):
    out = seed_demo(db)
    assert out["created_agent_ids"]
    faces = _faces(db)
    for name, (avatar, appearance) in SEED_AGENT_FACES.items():
        assert faces[name] == (avatar, appearance)
    for a in db.query(models.Agent).filter(
            models.Agent.name.in_(list(SEED_AGENT_FACES))).all():
        assert a.description == "共享 Linux 电脑验收角色"
        assert "Shared" not in (a.description or "")
    # 旧行回填：清掉头像再跑一次，应补回且不重复建人。
    pm = db.query(models.Agent).filter_by(name="产品经理").first()
    pm.avatar = ""
    pm.appearance = {}
    db.commit()
    seed_demo(db)
    assert db.query(models.Agent).filter_by(name="产品经理").count() == 1
    assert (pm.avatar, pm.appearance) == SEED_AGENT_FACES["产品经理"]


def test_demo_agent_specs_covered_by_face_map():
    # 两处种子路径的名单必须一致，否则映射会漏人。
    assert {spec["name"] for spec in _DEMO_AGENTS} == set(SEED_AGENT_FACES)


def test_ensure_team_team_description_is_chinese(db):
    _, team, _ = ensure_team(db)
    assert team.description == "总管主导的演示团队"
    assert "Chief" not in (team.description or "")


def test_ensure_team_backfills_old_english_team_description(db):
    # 存量行仍是旧英文描述时改中文；自定义描述不覆盖。
    db.add(models.Team(name="V3演示团队",
                       description="Chief-led demo team (round 3)",
                       leader_agent_id=None))
    db.commit()
    _, team, _ = ensure_team(db)
    assert team.description == "总管主导的演示团队"
    team.description = "演示专用团队"
    db.commit()
    ensure_team(db)
    assert (db.query(models.Team).filter_by(name="V3演示团队").first()
            .description == "演示专用团队")
