"""Knowledge retrieval over MemoryItem — hybrid keyword + TF-IDF search
(original implementation; document structure kept separate from the
indexing technique, per a layered design where items are chunked first
and each chunk is indexed).

Method, stated honestly: this is NOT neural embedding search.
  * keyword component: SQLite FTS5 with BM25 ranking over tokenized chunks;
  * vector component: classic TF-IDF sparse vectors + cosine similarity;
  * fusion: both scores normalized to [0, 1] and combined with weights
    (default 0.5 / 0.5).
Parent-child scheme: items are split into chunks (children) for scoring;
results return the best chunk together with its parent item's full text.

Tokenizer: latin words plus CJK unigrams+bigrams so Chinese text and
queries work without an external segmenter. The index is rebuilt lazily
whenever the corpus fingerprint (row count, max id) changes — the corpus
is desktop-scale, so a rebuild is milliseconds and always fresh.
"""
import math
import re
import threading
from collections import Counter

from sqlalchemy import func
from sqlalchemy import text as sql_text

from app import models

# One process-wide index snapshot: rebuilding (chunking, TF-IDF and the
# FTS table rewrite) happens only when the corpus fingerprint actually
# changes, instead of on every search request. The fingerprint uses
# cheap SQL aggregates (count, max id, total content length, newest
# created_at), which catch imports, deletions and content edits.
_INDEX_CACHE = {"fp": None, "chunks": None, "idf": None, "vectors": None}
_INDEX_LOCK = threading.Lock()

CHUNK_CHARS = 240
_W_KW = 0.5
_W_TF = 0.5


def tokenize(s):
    s = (s or "").lower()
    tokens = re.findall(r"[a-z0-9]+", s)
    for run in re.findall(r"[一-鿿]+", s):
        tokens.extend(list(run))
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def chunk_text(content, size=CHUNK_CHARS):
    parts = [p.strip() for p in re.split(r"[\n。；;]+", content or "") if p.strip()]
    chunks, buf = [], ""
    for p in parts:
        if len(buf) + len(p) + 1 <= size:
            buf = f"{buf} {p}".strip()
        else:
            if buf:
                chunks.append(buf)
            while len(p) > size:
                chunks.append(p[:size]); p = p[size:]
            buf = p
    if buf:
        chunks.append(buf)
    return chunks or ([content] if content else [])


class KnowledgeIndex:
    def __init__(self, db):
        self.db = db
        self._fingerprint = None
        self.chunks = []          # [{item_id, chunk_no, text, tokens}]
        self._idf = {}
        self._vectors = []        # parallel to chunks: {token: tfidf}

    # ---- build ------------------------------------------------------------
    def _corpus(self):
        q = self.db.query(models.MemoryItem)
        return q.order_by(models.MemoryItem.id.asc()).all()

    def _corpus_fingerprint(self):
        row = self.db.query(
            func.count(models.MemoryItem.id),
            func.max(models.MemoryItem.id),
            func.coalesce(func.sum(func.length(models.MemoryItem.content)), 0),
            func.max(models.MemoryItem.created_at)).one()
        return (row[0], row[1] or 0, int(row[2] or 0), str(row[3] or ""))

    def refresh(self):
        fp = self._corpus_fingerprint()
        if fp == self._fingerprint:
            return
        with _INDEX_LOCK:
            if _INDEX_CACHE["fp"] == fp and _INDEX_CACHE["chunks"] is not None:
                # Another instance already built this exact corpus:
                # adopt the snapshot instead of rebuilding (and instead
                # of rewriting the shared FTS table under other readers).
                self.chunks = _INDEX_CACHE["chunks"]
                self._idf = _INDEX_CACHE["idf"]
                self._vectors = _INDEX_CACHE["vectors"]
                self._fingerprint = fp
                return
            items = self._corpus()
            self.chunks = []
            for it in items:
                for i, ch in enumerate(chunk_text(it.content)):
                    self.chunks.append({"item_id": it.id, "chunk_no": i, "text": ch,
                                        "tokens": tokenize(ch)})
            n = len(self.chunks)
            df = Counter()
            for c in self.chunks:
                for t in set(c["tokens"]):
                    df[t] += 1
            self._idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
            self._vectors = [self._vec(c["tokens"]) for c in self.chunks]
            self._sync_fts()
            self._fingerprint = fp
            _INDEX_CACHE.update({"fp": fp, "chunks": self.chunks,
                                 "idf": self._idf, "vectors": self._vectors})

    def _vec(self, tokens):
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = len(tokens)
        return {t: (c / total) * self._idf.get(t, 1.0) for t, c in counts.items()}

    def _sync_fts(self):
        bind = self.db.get_bind()
        with bind.connect() as conn:
            conn.execute(sql_text("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                                  "USING fts5(item_id UNINDEXED, chunk_no UNINDEXED, body)"))
            conn.execute(sql_text("DELETE FROM memory_fts"))
            for c in self.chunks:
                conn.execute(sql_text(
                    "INSERT INTO memory_fts (item_id, chunk_no, body) VALUES (:i, :n, :b)"),
                    {"i": c["item_id"], "n": c["chunk_no"], "b": " ".join(c["tokens"])})
            conn.commit()

    # ---- scoring ----------------------------------------------------------
    def _keyword_scores(self, query_tokens):
        """FTS5 BM25 -> {chunk_index: score in [0,1]}."""
        if not query_tokens or not self.chunks:
            return {}
        match = " OR ".join(f'"{t}"' for t in sorted(set(query_tokens)))
        bind = self.db.get_bind()
        try:
            with bind.connect() as conn:
                rows = conn.execute(sql_text(
                    "SELECT item_id, chunk_no, bm25(memory_fts) AS rank "
                    "FROM memory_fts WHERE memory_fts MATCH :q"),
                    {"q": match}).fetchall()
        except Exception:
            return {}
        # Map by (item_id, chunk_no), never by rowid: rowids belong to
        # whichever snapshot last rewrote the shared FTS table, while
        # the key identifies the chunk in ANY snapshot.
        index_of = {(c["item_id"], c["chunk_no"]): i
                    for i, c in enumerate(self.chunks)}
        out = {}
        for item_id, chunk_no, rank in rows:
            i = index_of.get((item_id, chunk_no))
            if i is None:
                continue
            neg = -float(rank)  # bm25(): more negative = more relevant
            out[i] = neg / (1.0 + neg)  # [0, inf) -> [0, 1)
        return out

    def _tfidf_scores(self, query_tokens):
        qv = self._vec(query_tokens)
        if not qv:
            return {}
        qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
        out = {}
        for i, dv in enumerate(self._vectors):
            if not dv:
                continue
            dot = sum(qv[t] * dv.get(t, 0.0) for t in qv)
            dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
            s = dot / (qn * dn)
            if s > 0:
                out[i] = s
        return out

    # ---- public API -------------------------------------------------------
    def stats(self):
        self.refresh()
        return {"items": len({c["item_id"] for c in self.chunks}),
                "chunks": len(self.chunks), "vocabulary": len(self._idf),
                "method": "FTS5 BM25 keyword + TF-IDF cosine hybrid (not neural embeddings)",
                "weights": {"keyword": _W_KW, "tfidf": _W_TF}}

    def search(self, query, top_k=5, agent_id=None):
        self.refresh()
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(top_k, 50))
        qtokens = tokenize(query)
        kw = self._keyword_scores(qtokens)
        tf = self._tfidf_scores(qtokens)
        items = {it.id: it for it in self._corpus()}
        best = {}  # item_id -> best chunk result (RAW scores until output)
        for i, c in enumerate(self.chunks):
            it = items.get(c["item_id"])
            if it is None or (agent_id is not None and it.agent_id != agent_id):
                continue
            score = _W_KW * kw.get(i, 0.0) + _W_TF * tf.get(i, 0.0)
            if score <= 0:
                continue
            cur = best.get(it.id)
            if cur is None or score > cur["_raw"]:
                best[it.id] = {"item_id": it.id, "agent_id": it.agent_id,
                               "scope": it.scope, "_raw": score,
                               "_kw": kw.get(i, 0.0), "_tf": tf.get(i, 0.0),
                               "chunk": c["text"], "chunk_no": c["chunk_no"],
                               "content": it.content,
                               "created_at": it.created_at.isoformat() if it.created_at else None}
        # Compare and sort on raw floats; rounding is presentation only
        # (rounding first picked the wrong best chunk at close scores).
        ranked = sorted(best.values(), key=lambda r: -r["_raw"])[:top_k]
        return [{"item_id": r["item_id"], "agent_id": r["agent_id"],
                 "scope": r["scope"], "score": round(r["_raw"], 4),
                 "keyword_score": round(r["_kw"], 4),
                 "tfidf_score": round(r["_tf"], 4),
                 "chunk": r["chunk"], "chunk_no": r["chunk_no"],
                 "content": r["content"], "created_at": r["created_at"]}
                for r in ranked]
