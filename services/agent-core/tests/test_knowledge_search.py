"""Knowledge hybrid search tests (FTS5 BM25 keyword + TF-IDF cosine).

Corpus has clearly separated topics (Chinese + English). The correct
item must rank first for its topic query; scores must be decomposed and
the method label must honestly say TF-IDF, not embeddings.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.memory.search import KnowledgeIndex, chunk_text, tokenize

CORPUS = [
    (1, "权限 Bug 的根因是 admin_stats 接口缺少角色校验，修复方法是增加 PermissionError 检查并补充回归测试。"),
    (1, "The adaptive scheduler scores agents by capability, success rate, load, cost and latency."),
    (2, "演示视频录制流程：先启动后端和前端，再按脚本逐步操作共享桌面，最后导出 MP4 并核对时长。"),
    (2, "Desktop lease: only one agent controls the shared desktop; others wait in a FIFO queue until timeout."),
    (None, "每周例会纪要模板包括议程、决议、负责人和截止日期四部分，与技术实现无关。"),
]


@pytest.fixture()
def dbx(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "kb.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    ids = []
    for agent_id, content in CORPUS:
        m = models.MemoryItem(agent_id=agent_id, scope="long_term", content=content)
        db.add(m); db.commit(); db.refresh(m); ids.append(m.id)
    yield db, ids
    db.close()


def test_topic_queries_rank_correct_item_first(dbx):
    db, ids = dbx
    idx = KnowledgeIndex(db)
    r = idx.search("权限校验 PermissionError 修复")
    assert r and r[0]["item_id"] == ids[0], r
    r = idx.search("scheduler capability scoring")
    assert r and r[0]["item_id"] == ids[1], r
    r = idx.search("演示视频录制 MP4")
    assert r and r[0]["item_id"] == ids[2], r
    r = idx.search("desktop lease FIFO queue")
    assert r and r[0]["item_id"] == ids[3], r


def test_scores_decomposed_and_method_honest(dbx):
    db, ids = dbx
    idx = KnowledgeIndex(db)
    st = idx.stats()
    assert st["items"] == 5 and "TF-IDF" in st["method"] and "not neural" in st["method"]
    r = idx.search("权限 Bug 角色校验")
    top = r[0]
    assert top["item_id"] == ids[0]
    assert top["tfidf_score"] > 0 and top["keyword_score"] > 0
    assert abs(top["score"] - 0.5 * (top["tfidf_score"] + top["keyword_score"])) < 0.01
    assert top["chunk"] in top["content"]  # child chunk belongs to parent item


def test_agent_filter_and_no_match(dbx):
    db, ids = dbx
    idx = KnowledgeIndex(db)
    r = idx.search("权限", agent_id=2)
    assert all(x["agent_id"] == 2 for x in r)
    assert idx.search("火星殖民计划") == []  # zero token overlap with the corpus


def test_tokenize_and_chunk_helpers():
    toks = tokenize("权限校验 PermissionError")
    assert "权限" in toks and "permissionerror" in toks
    chunks = chunk_text("甲" * 100 + "。" + "乙" * 300, size=120)
    assert len(chunks) >= 3 and all(len(c) <= 121 for c in chunks)
