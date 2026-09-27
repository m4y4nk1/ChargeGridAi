"""Policy corpus for policy.search: PDFs chunked by page, embedded locally with fastembed
(BAAI/bge-small-en-v1.5, 384-d, no external API) and indexed for full text. Search fuses
the cosine and full-text rankings with reciprocal rank fusion, so exact terms ("Annexure",
"kVA", "GTP") and paraphrases both find their passages."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import load_config
from app.db.models.agents import PolicyChunk, PolicyDocument

RRF_K = 60
CANDIDATES = 20


def _data_dir() -> Path:
    return Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[4] / "data"


def _cfg() -> dict[str, Any]:
    return dict(load_config("agents.yaml")["policy"])


@lru_cache(maxsize=1)
def _model() -> Any:
    import os

    from fastembed import TextEmbedding

    cache = Path(os.environ.get("MODEL_CACHE_DIR") or _data_dir() / "models") / "fastembed"
    cache.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=_cfg()["embedding_model"], cache_dir=str(cache), threads=1)


def _vector_literal(v: Any) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in v) + "]"


def chunk_page(page_text: str, size: int, overlap: int) -> list[str]:
    """Split a page into ~size-character chunks on sentence/line boundaries."""
    clean = re.sub(r"[ \t]+", " ", page_text).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    if len(clean) <= size:
        return [clean] if len(clean) > 40 else []
    chunks, start = [], 0
    while start < len(clean):
        end = min(len(clean), start + size)
        if end < len(clean):
            cut = max(clean.rfind(". ", start, end), clean.rfind("\n", start, end))
            if cut > start + size // 2:
                end = cut + 1
        piece = clean[start:end].strip()
        if len(piece) > 40:
            chunks.append(piece)
        if end >= len(clean):
            break
        start = max(end - overlap, start + 1)
    return chunks


async def ingest(session: AsyncSession) -> dict[str, Any]:
    from pypdf import PdfReader

    from app.ingestion.provenance import quality_report, record_snapshot, sync_data_sources

    await sync_data_sources(session)
    cfg = _cfg()
    manifest = load_config("policy/documents.yaml")["documents"]
    folder = _data_dir() / "raw" / "policy"
    summary: dict[str, Any] = {}
    for doc_id, meta in manifest.items():
        path = folder / meta["file"]
        if not path.exists():
            summary[doc_id] = f"missing {path}"
            continue
        reader = PdfReader(str(path))
        pieces = [
            (page_no, i, chunk)
            for page_no, page in enumerate(reader.pages, start=1)
            for i, chunk in enumerate(
                chunk_page(page.extract_text() or "", cfg["chunk_chars"], cfg["chunk_overlap"])
            )
        ]
        vectors = list(_model().passage_embed([c for _, _, c in pieces]))
        report = quality_report(
            [], (), previous_row_count=None, document=doc_id, pages=len(reader.pages)
        )
        report["row_count"] = len(pieces)
        snapshot = await record_snapshot(
            session,
            source_id="policy_corpus",
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            raw=path.read_bytes(),
            raw_filename=meta["file"],
            row_count=len(pieces),
            granularity="page chunk",
            report=report,
        )
        await session.execute(delete(PolicyChunk).where(PolicyChunk.document_id == doc_id))
        await session.execute(delete(PolicyDocument).where(PolicyDocument.id == doc_id))
        issued = meta.get("issued_on")
        session.add(
            PolicyDocument(
                id=doc_id,
                title=meta["title"],
                issuer=meta["issuer"],
                issued_on=issued if isinstance(issued, date) else None,
                url=meta.get("url"),
                pages=len(reader.pages),
                snapshot_id=snapshot.id,
            )
        )
        await session.flush()
        for (page_no, i, chunk), vec in zip(pieces, vectors, strict=True):
            await session.execute(
                text(
                    "insert into policy_chunk (document_id, page, chunk_index, text, embedding) "
                    "values (:d, :p, :i, :t, cast(:e as vector))"
                ),
                {"d": doc_id, "p": page_no, "i": i, "t": chunk, "e": _vector_literal(vec)},
            )
        await session.commit()
        summary[doc_id] = {"pages": len(reader.pages), "chunks": len(pieces)}
    return summary


async def search(session: AsyncSession, query: str, top_k: int) -> list[dict[str, Any]]:
    has_rows = (await session.execute(text("select exists(select 1 from policy_chunk)"))).scalar()
    if not has_rows:
        return []
    q_vec = _vector_literal(next(iter(_model().query_embed([query]))))
    dense = (
        (
            await session.execute(
                text(
                    "select id from policy_chunk order by embedding <=> cast(:q as vector) limit :n"
                ),
                {"q": q_vec, "n": CANDIDATES},
            )
        )
        .scalars()
        .all()
    )
    sparse = (
        (
            await session.execute(
                text(
                    "select id from policy_chunk, websearch_to_tsquery('english', :q) query "
                    "where to_tsvector('english', text) @@ query "
                    "order by ts_rank(to_tsvector('english', text), query) desc limit :n"
                ),
                {"q": query, "n": CANDIDATES},
            )
        )
        .scalars()
        .all()
    )
    scores: dict[int, float] = {}
    for ranking in (dense, sparse):
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    best = sorted(scores, key=lambda c: -scores[c])[:top_k]
    if not best:
        return []
    rows = (
        await session.execute(
            text(
                "select c.id, c.page, c.text, d.id, d.title, d.issuer, d.issued_on, d.url "
                "from policy_chunk c join policy_document d on d.id = c.document_id "
                "where c.id = any(:ids)"
            ),
            {"ids": best},
        )
    ).all()
    by_id = {r[0]: r for r in rows}
    return [
        {
            "document": by_id[c][4],
            "document_id": by_id[c][3],
            "issuer": by_id[c][5],
            "issued_on": str(by_id[c][6]) if by_id[c][6] else None,
            "page": by_id[c][1],
            "text": by_id[c][2],
            "url": by_id[c][7],
        }
        for c in best
        if c in by_id
    ]
