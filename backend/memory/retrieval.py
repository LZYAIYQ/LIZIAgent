"""Memory retrieval and ranking.

Scoring factors (v2 with time decay + access pattern boost):

1. **Substring match** (8.0) — exact query in content
2. **Term overlap** (4.0) — shared tokens between query and content
3. **Char-gram overlap** (3.0) — bigram similarity for fuzzy CJK matching
4. **SequenceMatcher** (2.0) — overall string similarity
5. **Pinned boost** (0.8) — pinned entries always rank higher
6. **Recall count boost** (0.5 * log) — frequently accessed memories rank higher
7. **Time decay** (-0.5 to 0.0) — older memories decay, recent ones get a boost
8. **Recency boost** (0.3) — memories accessed recently get a boost
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

_WORD_RE = re.compile(r"[a-zA-Z0-9_\-]+|[一-鿿]")

_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "沟通": ("回复", "语气", "风格", "简短", "详细"),
    "风格": ("回复", "语气", "格式", "简短", "详细"),
    "偏好": ("喜欢", "不喜欢", "希望", "习惯"),
    "格式": ("输出", "markdown", "标题", "列表"),
    "称呼": ("叫", "名字", "昵称"),
}

# Time decay half-life in days. After this many days, a memory's
# time-based score contribution drops to half.
_TIME_DECAY_HALF_LIFE_DAYS = 30.0


def rank_memories(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not candidates:
        return []
    if not q:
        return candidates[: max(1, limit)]

    q_terms = _expanded_terms(q)
    q_grams = _char_grams(q)
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in candidates:
        content = str(row.get("content") or "")
        c_lower = content.lower()
        q_lower = q.lower()
        score = 0.0

        # 1. Substring match
        if q_lower and q_lower in c_lower:
            score += 8.0

        # 2. Term overlap
        c_terms = _terms(content)
        if q_terms and c_terms:
            overlap = q_terms & c_terms
            score += 4.0 * (len(overlap) / max(1, len(q_terms)))

        # 3. Char-gram overlap
        c_grams = _char_grams(content)
        if q_grams and c_grams:
            score += 3.0 * (len(q_grams & c_grams) / max(1, len(q_grams)))

        # 4. SequenceMatcher
        ratio = SequenceMatcher(None, q_lower, c_lower).ratio()
        if ratio >= 0.12:
            score += 2.0 * ratio

        # 5. Pinned boost
        if row.get("pinned"):
            score += 0.8

        # 6. Recall count boost (logarithmic)
        recall_count = int(row.get("recall_count") or 0)
        score += min(0.5, math.log1p(recall_count) / 10)

        # 7. Time decay — older memories lose score
        created_at = _parse_dt(row.get("created_at"))
        if created_at is not None:
            age_days = max(0.0, (now - created_at).total_seconds() / 86400)
            decay = math.exp(-0.693 * age_days / _TIME_DECAY_HALF_LIFE_DAYS)
            score += 0.3 * decay  # max +0.3 for brand-new memories

        # 8. Recency boost — recently accessed memories get a boost
        last_recalled = _parse_dt(row.get("last_recalled_at"))
        if last_recalled is not None:
            recency_days = max(0.0, (now - last_recalled).total_seconds() / 86400)
            recency = math.exp(-0.693 * recency_days / 7.0)  # 7-day half-life
            score += 0.2 * recency

        if score > 0.05:
            scored.append((score, row))

    scored.sort(
        key=lambda item: (
            item[0],
            bool(item[1].get("pinned")),
            int(item[1].get("recall_count") or 0),
            str(item[1].get("created_at") or ""),
        ),
        reverse=True,
    )
    return [row for _, row in scored[: max(1, limit)]]


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO datetime string, return UTC-aware datetime or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _expanded_terms(text: str) -> set[str]:
    terms = _terms(text)
    expanded = set(terms)
    for term in terms:
        for key, values in _QUERY_EXPANSIONS.items():
            if key in term or term in key:
                expanded.update(values)
    return expanded


def _terms(text: str) -> set[str]:
    raw = [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]
    merged: set[str] = {t for t in raw if len(t) >= 2 or _is_cjk(t)}
    cjk_chars = "".join(t for t in raw if _is_cjk(t))
    for n in (2, 3):
        for i in range(0, max(0, len(cjk_chars) - n + 1)):
            merged.add(cjk_chars[i:i + n])
    return merged


def _char_grams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", (text or "").lower())
    if len(compact) < 2:
        return set()
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def _is_cjk(text: str) -> bool:
    return len(text) == 1 and "一" <= text <= "鿿"


# ---------------------------------------------------------------------------
# Memory consolidation (find similar memories for merging)
# ---------------------------------------------------------------------------

def find_similar_memories(
    memories: list[dict[str, Any]],
    *,
    threshold: float = 0.7,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Find pairs of memories that are likely duplicates.

    Returns list of (mem_a, mem_b, similarity_score) where score >= threshold.
    """
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    n = len(memories)
    for i in range(n):
        for j in range(i + 1, n):
            a = memories[i]
            b = memories[j]
            # Skip if different kinds
            if a.get("kind") != b.get("kind"):
                continue
            content_a = str(a.get("content") or "").strip()
            content_b = str(b.get("content") or "").strip()
            if not content_a or not content_b:
                continue

            # Quick substring check
            if content_a in content_b or content_b in content_a:
                pairs.append((a, b, 1.0))
                continue

            # Term overlap
            terms_a = _terms(content_a)
            terms_b = _terms(content_b)
            if terms_a and terms_b:
                overlap = len(terms_a & terms_b)
                union = len(terms_a | terms_b)
                jaccard = overlap / union if union else 0
                if jaccard >= threshold:
                    pairs.append((a, b, jaccard))
                    continue

            # SequenceMatcher
            ratio = SequenceMatcher(None, content_a.lower(), content_b.lower()).ratio()
            if ratio >= threshold:
                pairs.append((a, b, ratio))

    return pairs
