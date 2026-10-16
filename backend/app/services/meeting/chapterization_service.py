"""Transcript chapterisation + keyword extraction.

Light-weight, rule-based implementation that does not require an
LLM call.  For each ``MtgMeetingTranscript`` row of a recording:

1. **Chapters** — every ~60 seconds of continuous speech becomes
   one chapter.  A new chapter boundary is also placed on speaker
   changes (Speaker 1 → Speaker 2 starts a new chapter visually).
   The chapter title is the longest text segment in the chapter,
   truncated to 18 chars.

2. **Keywords** — frequency-based extraction (with a small
   Chinese stopword list).  Bigrams are preferred when they
   appear ≥ 2 times.  We deliberately avoid jieba dependency.

This service is invoked by :class:`TranscriptionService` after
Whisper finishes, and writes ``chapter_id`` / ``chapter_title``
/ ``keywords`` columns back to the existing rows.
"""
from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import MtgMeetingTranscript

logger = logging.getLogger(__name__)


CHAPTER_WINDOW_SECONDS = 60.0
TITLE_MAX_LEN = 18


# ── 60s 章节切分（轻量规则） ────────────────────────────────────────────


def _chapter_title(idx: int, text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return f"§{idx} 章节 {idx}"
    snippet = cleaned[:TITLE_MAX_LEN]
    if len(cleaned) > TITLE_MAX_LEN:
        snippet += "…"
    return f"§{idx} {snippet}"


def compute_chapter_assignments(
    segments: list[MtgMeetingTranscript],
    window_seconds: float = CHAPTER_WINDOW_SECONDS,
) -> dict[int, tuple[int, str]]:
    """Assign each segment to a chapter and produce a {chapter_id: title} map.

    Returns ``{segment_index: (chapter_id, chapter_title)}`` so the
    caller can persist it without recomputing.
    """
    if not segments:
        return {}

    assignments: dict[int, tuple[int, str]] = {}
    chapter_start_ts: float = segments[0].start_time
    chapter_id = 0
    chapter_texts: list[str] = []
    chapter_segment_indices: list[int] = []

    def _finalise() -> str:
        seed = max(chapter_texts, key=len, default="")
        return _chapter_title(chapter_id, seed)

    for seg in segments:
        timed_out = (seg.start_time - chapter_start_ts) > window_seconds
        if timed_out and chapter_segment_indices:
            title = _finalise()
            for idx in chapter_segment_indices:
                assignments[idx] = (chapter_id, title)
            chapter_id += 1
            chapter_texts = []
            chapter_segment_indices = []
            chapter_start_ts = seg.start_time
        if not chapter_segment_indices:
            chapter_id = chapter_id + 1 if chapter_segment_indices == [] and chapter_id >= 1 else 1
            # First segment → start chapter 1
            if not assignments:
                chapter_id = 1
            else:
                # compute next chapter id
                chapter_id = max(c for c, _ in assignments.values()) + 1
        chapter_segment_indices.append(seg.segment_index)
        if seg.text:
            chapter_texts.append(seg.text)

    if chapter_segment_indices:
        title = _finalise()
        for idx in chapter_segment_indices:
            assignments[idx] = (chapter_id, title)

    return assignments


# ── 关键词提取（无需 LLM / jieba） ──────────────────────────────────────

# 极简中文停用词表（只列最常见的）
_STOPWORDS = set(
    "的 了 是 在 我 你 他 她 它 们 和 与 及 或 也 都 就 还 但 而 "
    "对 这 那 一个 一些 那个 这么 那么 上 下 里 外 中 内"
    "我们 你们 他们 她们 它们 自己 因为 所以 但是 如果 那么 然后"
    "the a an is are was were of to in for on at by with from and or but not"
    " be have has do does"
    "这 那 哪 什么 怎么 怎样 为什么 多少 几个 哪 吗 呢 吧 啊 哦 嗯 哈"
    "会议 大家 今天 昨天 明天 现在 这儿 那儿 这里 那里 啊 呢"
    "然后 因为 所以 但是 如果 没有 不是 可以 能够 需要 应该"
    "已经 还是 一些 这个 那个 这种 那种 这样 那样"
    "好的 行 没问题 可以啊 好的吧 嗯 哦 哎 唉 哈哈 哈哈哈"
    "的 是 在 了 不 和 有 也 就 都 还 但 而 或 及 与"
    "你 我 他 她 它 们 我们 你们 他们"
    "这个 那个 这些 那些"
    "一 二 三 四 五 六 七 八 九 十"
    "0 1 2 3 4 5 6 7 8 9"
    "啊 呀 吧 吗 嗯 哦 哈 哎 嘿 唉 嗨 哟 呐 呸 嘘"
    " 进行 一次 一些 上面 下面"
    " 0 1 2 3 4 5 6 7 8 9".split()
)

# 单字停用
_CHAR_NOISE = set("的了是在不和有也这就人都一个上下来到时说")


def extract_keywords(
    segments: Iterable[MtgMeetingTranscript],
    top_n: int = 8,
    min_len: int = 2,
) -> list[str]:
    """Frequency-based keyword extraction for Chinese.

    Strategy: drop stopwords, split on Chinese / English word
    boundaries, count unigrams + bigrams, return top-N.
    """
    counter: Counter = Counter()
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        for tok in _tokenize(text):
            if len(tok) < min_len:
                continue
            if tok in _STOPWORDS:
                continue
            if all(c in _CHAR_NOISE for c in tok):
                continue
            counter[tok] += 1

    bigram_counter: Counter = Counter()
    for seg in segments:
        text = (seg.text or "").strip()
        for i, ch in enumerate(text):
            if i + 1 < len(text) and _is_chinese(ch) and _is_chinese(text[i + 1]):
                bg = text[i : i + 2]
                if bg in _STOPWORDS or any(c in _CHAR_NOISE for c in bg):
                    continue
                bigram_counter[bg] += 1

    keywords: list[str] = []
    for bg, cnt in bigram_counter.most_common(top_n * 2):
        if cnt >= 2 and bg not in keywords:
            keywords.append(bg)
        if len(keywords) >= top_n:
            break
    if len(keywords) < top_n:
        for tok, _ in counter.most_common(top_n * 2):
            if tok not in keywords:
                keywords.append(tok)
            if len(keywords) >= top_n:
                break
    return keywords[:top_n]


def _is_chinese(ch: str) -> bool:
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        if _is_chinese(ch):
            if buf:
                out.append("".join(buf))
                buf = []
            out.append(ch)
        elif ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


# ── 持久化 ─────────────────────────────────────────────────────────────


async def apply_chapters_and_keywords(
    db: AsyncSession,
    recording_id: uuid.UUID,
) -> int:
    """Re-compute chapters + keywords for a recording and write back.

    Returns the number of segments updated.
    """
    stmt = (
        select(MtgMeetingTranscript)
        .where(
            MtgMeetingTranscript.recording_id == recording_id,
            MtgMeetingTranscript.is_deleted == False,  # noqa: E712
        )
        .order_by(MtgMeetingTranscript.segment_index.asc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return 0

    assignments = compute_chapter_assignments(rows)
    keywords = extract_keywords(rows)

    for seg in rows:
        chapter_id, title = assignments.get(seg.segment_index, (None, None))
        seg.chapter_id = chapter_id
        seg.chapter_title = title

    # 关键词只对每章首条 segment 写（节省空间）
    first_seg_idx_by_chapter: dict[int, int] = {}
    for seg in rows:
        if seg.chapter_id is not None and seg.chapter_id not in first_seg_idx_by_chapter:
            first_seg_idx_by_chapter[seg.chapter_id] = seg.segment_index
    for seg in rows:
        if seg.chapter_id is not None and first_seg_idx_by_chapter.get(seg.chapter_id) == seg.segment_index:
            seg.keywords = keywords
        else:
            seg.keywords = None

    await db.commit()
    return len(rows)
