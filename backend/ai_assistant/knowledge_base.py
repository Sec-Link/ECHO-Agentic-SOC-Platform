import hashlib
import json
import math
import os
import re
import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings

from ai_assistant.models import KnowledgeBaseItem, KnowledgeEmbedding, KnowledgeRetrievalLog

_KB_SCAN_LOCK = threading.Lock()
_KB_LAST_SCAN = 0.0


@dataclass
class RetrievalResult:
    item: KnowledgeBaseItem
    chunk_index: int
    chunk_text: str
    similarity: float
    score: float


def _kb_base_path() -> str:
    return str(getattr(settings, "AI_KNOWLEDGE_BASE_PATH", "knowledge_base"))


def _ensure_dir(path: str) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_markdown_sections(text: str) -> List[Tuple[str, str]]:
    header_regex = re.compile(r"^#{1,6}\s+.+$", flags=re.MULTILINE)
    matches = list(header_regex.finditer(text))
    if not matches:
        return [("", text.strip())] if text.strip() else []

    sections: List[Tuple[str, str]] = []
    header_path: List[str] = []

    for i, match in enumerate(matches):
        start = match.start()
        end = match.end()
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        header_line = text[start:end].strip()

        level = len(header_line.split(" ", 1)[0])
        new_path = []
        for h in header_path:
            h_level = len(h.split(" ", 1)[0])
            if h_level < level:
                new_path.append(h)
        new_path.append(header_line)
        header_path = new_path

        content = text[start:next_start].strip()
        if not content:
            continue
        sections.append((" / ".join(header_path), content))

    return sections


def _split_large_text(text: str, max_chars: int, overlap: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunk = text[start:end]
        chunks.append(chunk)
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _chunk_content(content: str, max_chars: int = 1200, overlap: int = 200) -> List[str]:
    out: List[str] = []
    sections = _split_markdown_sections(content)
    for header_path, section in sections:
        if not section:
            continue
        prefix = f"[{header_path}] " if header_path else ""
        for chunk in _split_large_text(section, max_chars=max_chars, overlap=overlap):
            chunk = chunk.strip()
            if not chunk:
                continue
            out.append(prefix + chunk)
    return out


def _embedding_client_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    overrides = overrides or {}
    api_key = overrides.get("api_key") or getattr(settings, "AI_KNOWLEDGE_EMBEDDING_API_KEY", "")
    if not api_key:
        api_key = getattr(settings, "OPENAI_API_KEY", "")
    base_url = overrides.get("base_url") or getattr(settings, "AI_KNOWLEDGE_EMBEDDING_BASE_URL", "")
    if not base_url:
        base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = overrides.get("model") or getattr(settings, "AI_KNOWLEDGE_EMBEDDING_MODEL", "text-embedding-3-large")
    timeout_value = overrides.get("timeout_seconds")
    if timeout_value in (None, ""):
        timeout_value = 30
    try:
        timeout = int(timeout_value or 30)
    except Exception:
        timeout = 30
    return {"api_key": api_key, "base_url": base_url, "model": model, "timeout": timeout}


def embed_text(text: str, overrides: Optional[Dict[str, Any]] = None) -> List[float]:
    cfg = _embedding_client_config(overrides)
    if not cfg["api_key"]:
        raise RuntimeError("Embedding API key is not configured")
    url = f"{cfg['base_url'].rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {"model": cfg["model"], "input": text}
    res = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout"])
    if res.status_code >= 400:
        raise RuntimeError(f"Embedding API error: {res.status_code} {res.text[:500]}")
    data = res.json()
    items = data.get("data") if isinstance(data, dict) else None
    if isinstance(items, list) and items:
        vec = items[0].get("embedding")
        if isinstance(vec, list):
            return [float(v) for v in vec]
    raise RuntimeError("Invalid embedding response")


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def bm25_score(query: str, text: str) -> float:
    query_terms = _normalize_ws(query).lower().split()
    if not query_terms:
        return 0.0
    text_terms = _normalize_ws(text).lower().split()
    if not text_terms:
        return 0.0
    k1 = 1.2
    b = 0.75
    avg_len = 150.0
    doc_len = float(len(text_terms))
    term_freq: Dict[str, int] = {}
    for t in text_terms:
        term_freq[t] = term_freq.get(t, 0) + 1
    score = 0.0
    matched = 0
    for term in query_terms:
        tf = term_freq.get(term, 0)
        if tf == 0:
            continue
        matched += 1
        length_norm = 1 - b + b * (doc_len / avg_len)
        tf_score = tf / (tf + k1 * length_norm)
        term_len = len(term)
        if term_len <= 2:
            idf = 1.2 + math.log(1.0 + tf / 20.0)
        elif term_len <= 4:
            idf = 1.0 + math.log(1.0 + tf / 15.0)
        else:
            idf = 0.9 + math.log(1.0 + tf / 10.0)
        score += tf_score * idf
    if query_terms:
        match_ratio = matched / float(len(query_terms))
        score = (score / float(len(query_terms))) * (1 + match_ratio) / 2
    return min(score, 1.0)


def scan_knowledge_base() -> List[str]:
    base_path = _kb_base_path()
    if not base_path:
        raise RuntimeError("Knowledge base path is not configured")
    _ensure_dir(base_path)

    to_index: List[str] = []
    for root, _, files in os.walk(base_path):
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, base_path)
            parts = rel.split(os.sep)
            category = parts[0] if len(parts) > 1 else "uncategorized"
            title = os.path.splitext(os.path.basename(path))[0]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            content_hash = _hash_text(content)
            item, created = KnowledgeBaseItem.objects.get_or_create(
                file_path=path,
                defaults={
                    "category": category,
                    "title": title,
                    "content": content,
                    "content_hash": content_hash,
                },
            )
            if created:
                to_index.append(str(item.id))
                continue
            if item.content_hash != content_hash:
                item.category = category
                item.title = title
                item.content = content
                item.content_hash = content_hash
                item.save(update_fields=["category", "title", "content", "content_hash", "updated_at"])
                to_index.append(str(item.id))
    return to_index


def index_item(item_id: str) -> int:
    item = KnowledgeBaseItem.objects.filter(id=item_id).first()
    if not item:
        return 0
    KnowledgeEmbedding.objects.filter(item=item).delete()
    chunks = _chunk_content(item.content)
    created = 0
    for idx, chunk in enumerate(chunks):
        try:
            emb = embed_text(chunk)
        except Exception:
            continue
        KnowledgeEmbedding.objects.create(
            item=item,
            chunk_index=idx,
            chunk_text=chunk,
            embedding=emb,
        )
        created += 1
    return created


def rebuild_index() -> int:
    KnowledgeEmbedding.objects.all().delete()
    total = 0
    for item in KnowledgeBaseItem.objects.all():
        total += index_item(str(item.id))
    return total


def ensure_index(scan_ttl_seconds: Optional[int] = None) -> None:
    global _KB_LAST_SCAN
    ttl = scan_ttl_seconds
    if ttl is None:
        ttl = int(getattr(settings, "AI_KNOWLEDGE_SCAN_TTL_SECONDS", 300))
    now = time.time()
    if now - _KB_LAST_SCAN < max(ttl, 5):
        return
    with _KB_SCAN_LOCK:
        now = time.time()
        if now - _KB_LAST_SCAN < max(ttl, 5):
            return
        to_index = scan_knowledge_base()
        for item_id in to_index:
            index_item(item_id)
        _KB_LAST_SCAN = time.time()


def get_categories() -> List[str]:
    return list(
        KnowledgeBaseItem.objects.values_list("category", flat=True).distinct().order_by("category")
    )


def search_knowledge_base(
    query: str,
    risk_type: str = "",
    top_k: Optional[int] = None,
    threshold: Optional[float] = None,
    hybrid_weight: Optional[float] = None,
) -> List[RetrievalResult]:
    if not query:
        return []
    top_k = top_k or getattr(settings, "AI_KNOWLEDGE_TOP_K", 5)
    threshold = threshold if threshold is not None else getattr(settings, "AI_KNOWLEDGE_SIMILARITY_THRESHOLD", 0.7)
    hybrid_weight = hybrid_weight if hybrid_weight is not None else getattr(settings, "AI_KNOWLEDGE_HYBRID_WEIGHT", 0.7)

    query_text = f"[risk_type: {risk_type}] {query}" if risk_type else query
    query_embedding = embed_text(query_text)

    embeddings_qs = KnowledgeEmbedding.objects.select_related("item")
    if risk_type:
        embeddings_qs = embeddings_qs.filter(item__category__iexact=risk_type)

    candidates: List[RetrievalResult] = []
    for row in embeddings_qs.iterator():
        try:
            emb = row.embedding or []
            similarity = cosine_similarity(query_embedding, list(emb))
        except Exception:
            similarity = 0.0
        if similarity < 0.1:
            continue
        bm25_chunk = bm25_score(query, row.chunk_text)
        bm25_cat = bm25_score(query, row.item.category)
        bm25_title = bm25_score(query, row.item.title)
        bm25 = max(bm25_chunk, bm25_cat, bm25_title)
        score = float(hybrid_weight) * similarity + (1 - float(hybrid_weight)) * min(bm25, 1.0)
        candidates.append(
            RetrievalResult(
                item=row.item,
                chunk_index=row.chunk_index,
                chunk_text=row.chunk_text,
                similarity=similarity,
                score=score,
            )
        )

    candidates.sort(key=lambda r: r.score, reverse=True)
    filtered = [c for c in candidates if c.similarity >= float(threshold)]
    if not filtered and candidates:
        filtered = candidates[: top_k or 5]
    if top_k:
        filtered = filtered[:top_k]
    return filtered


def expand_results(results: List[RetrievalResult], query: str, hybrid_weight: Optional[float] = None) -> List[RetrievalResult]:
    if not results:
        return []
    hybrid_weight = hybrid_weight if hybrid_weight is not None else getattr(settings, "AI_KNOWLEDGE_HYBRID_WEIGHT", 0.7)
    item_ids = {r.item.id for r in results}
    expanded: List[RetrievalResult] = []
    query_embedding = embed_text(query)
    for item_id in item_ids:
        chunks = KnowledgeEmbedding.objects.filter(item_id=item_id).order_by("chunk_index")
        for row in chunks:
            emb = row.embedding or []
            similarity = cosine_similarity(query_embedding, list(emb))
            bm25 = bm25_score(query, row.chunk_text)
            score = float(hybrid_weight) * similarity + (1 - float(hybrid_weight)) * min(bm25, 1.0)
            expanded.append(
                RetrievalResult(
                    item=row.item,
                    chunk_index=row.chunk_index,
                    chunk_text=row.chunk_text,
                    similarity=similarity,
                    score=score,
                )
            )
    expanded.sort(key=lambda r: (r.item.id, r.chunk_index))
    return expanded


def format_search_results(results: List[RetrievalResult]) -> Tuple[str, List[str]]:
    if not results:
        return "No relevant knowledge found.", []
    grouped: Dict[str, List[RetrievalResult]] = {}
    for r in results:
        grouped.setdefault(str(r.item.id), []).append(r)
    ordered_groups = sorted(grouped.values(), key=lambda rs: max(r.score for r in rs), reverse=True)

    retrieved_ids: List[str] = []
    lines: List[str] = []
    lines.append(f"Found {len(results)} relevant knowledge chunks.\n")
    for idx, group in enumerate(ordered_groups, start=1):
        group.sort(key=lambda r: r.chunk_index)
        item = group[0].item
        max_score = max(r.score for r in group)
        max_sim = max(r.similarity for r in group)
        lines.append(f"--- Result {idx} (similarity {max_sim*100:.2f}%, score {max_score*100:.2f}%) ---")
        lines.append(f"Source: [{item.category}] {item.title} (ID: {item.id})")
        for c_idx, r in enumerate(group, start=1):
            marker = " [best]" if r.score == max_score else ""
            lines.append(f"  [chunk {c_idx}{marker}]\n{r.chunk_text}\n")
        lines.append("")
        retrieved_ids.append(str(item.id))

    meta = json.dumps({"_metadata": {"retrievedItemIDs": retrieved_ids}}, ensure_ascii=True)
    lines.append(f"<!-- METADATA: {meta} -->")
    return "\n".join(lines), retrieved_ids


def log_retrieval(conversation_id: str, message_id: str, query: str, risk_type: str, item_ids: List[str]) -> None:
    KnowledgeRetrievalLog.objects.create(
        conversation_id=conversation_id or "",
        message_id=message_id or "",
        query=query,
        risk_type=risk_type or "",
        retrieved_item_ids=item_ids or [],
    )
