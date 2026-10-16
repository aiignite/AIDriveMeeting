import React, { useMemo, useRef, useState } from 'react';
import { Copy, Check, UserCircle2, ChevronDown, ChevronRight, Hash } from 'lucide-react';

export interface TranscriptSegment {
  id: string;
  segmentIndex: number;
  startTime: number;
  endTime: number;
  speakerLabel?: string | null;
  text: string;
  confidence?: number | null;
  language?: string | null;
  // 阶段六 6.3: 章节 + 关键词
  chapterId?: number | null;
  chapterTitle?: string | null;
  keywords?: string[] | null;
}

interface TranscriptViewProps {
  segments: TranscriptSegment[];
  emptyHint?: string;
  className?: string;
  /** 自定义滚动容器最大高度（默认 480px）。仅在 fillHeight=false 时生效。 */
  maxHeight?: string;
  /** 是否填满父容器高度（父容器需有明确高度，常配合 flex-1 使用） */
  fillHeight?: boolean;
}

const formatTime = (s: number) => {
  if (!isFinite(s) || s < 0) return '00:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s - m * 60);
  return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
};

const SPEAKER_PALETTE = [
  'bg-sky-100 text-sky-700 border-sky-200',
  'bg-emerald-100 text-emerald-700 border-emerald-200',
  'bg-violet-100 text-violet-700 border-violet-200',
  'bg-amber-100 text-amber-700 border-amber-200',
  'bg-rose-100 text-rose-700 border-rose-200',
  'bg-cyan-100 text-cyan-700 border-cyan-200',
  'bg-pink-100 text-pink-700 border-pink-200',
  'bg-indigo-100 text-indigo-700 border-indigo-200',
];

const hashSpeaker = (label: string): number => {
  let h = 0;
  for (let i = 0; i < label.length; i++) {
    h = ((h << 5) - h + label.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
};

const speakerBadgeClass = (label?: string | null): string => {
  if (!label) return 'bg-gray-100 text-gray-500 border-gray-200';
  return SPEAKER_PALETTE[hashSpeaker(label) % SPEAKER_PALETTE.length];
};

const speakerInitial = (label?: string | null): string => {
  if (!label) return '?';
  const tail = label.split(/[:_\s]+/).pop() || label;
  if (/^\d+$/.test(tail)) return `S${tail}`;
  return tail.slice(0, 2).toUpperCase();
};

// 6.3-04: 用 <mark> 高亮关键词
const highlightText = (text: string, keywords: string[] | null | undefined) => {
  if (!text || !keywords || keywords.length === 0) return text;
  // 按关键词长度降序，避免短词优先匹配（如 "是" 优先于 "是否"）
  const sorted = [...keywords].sort((a, b) => b.length - a.length);
  const escaped = sorted
    .map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .filter(Boolean);
  if (escaped.length === 0) return text;
  try {
    const re = new RegExp(`(${escaped.join('|')})`, 'g');
    const parts = text.split(re);
    return parts.map((p, i) =>
      i % 2 === 1 ? (
        <mark key={i} className="bg-yellow-100 text-yellow-900 rounded px-0.5">
          {p}
        </mark>
      ) : (
        <React.Fragment key={i}>{p}</React.Fragment>
      )
    );
  } catch {
    return text;
  }
};

export const TranscriptView: React.FC<TranscriptViewProps> = ({
  segments,
  emptyHint = '暂无转写内容',
  className,
  maxHeight = '480px',
  fillHeight = false,
}) => {
  const sorted = useMemo(
    () => [...segments].sort((a, b) => a.segmentIndex - b.segmentIndex),
    [segments]
  );

  const speakers = useMemo(() => {
    const set = new Set<string>();
    sorted.forEach((s) => {
      if (s.speakerLabel) set.add(s.speakerLabel);
    });
    return Array.from(set);
  }, [sorted]);

  const showSpeakers = speakers.length > 0;

  const [activeSpeakers, setActiveSpeakers] = useState<Set<string> | null>(null);

  // 6.3-03: 按 chapter 分组
  const chapters = useMemo(() => {
    const map = new Map<number, { id: number; title: string; segments: TranscriptSegment[]; keywords: string[] | null }>();
    for (const seg of sorted) {
      if (activeSpeakers && (!seg.speakerLabel || !activeSpeakers.has(seg.speakerLabel))) {
        continue;
      }
      const chId = seg.chapterId ?? 0;
      if (!map.has(chId)) {
        map.set(chId, {
          id: chId,
          title: seg.chapterTitle || `§${chId || '无章节'}`,
          segments: [],
          keywords: seg.keywords ?? null,
        });
      }
      map.get(chId)!.segments.push(seg);
    }
    return Array.from(map.values());
  }, [sorted, activeSpeakers]);

  // 6.3-03: 折叠状态 — 默认全部展开
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const toggleChapter = (id: number) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const collapseAll = () => setCollapsed(new Set(chapters.map((c) => c.id)));
  const expandAll = () => setCollapsed(new Set());

  const fullText = useMemo(
    () =>
      chapters
        .flatMap((c) => c.segments)
        .map((s) => {
          const who = s.speakerLabel ? `${s.speakerLabel}: ` : '';
          return `${who}${s.text}`;
        })
        .filter(Boolean)
        .join('\n'),
    [chapters]
  );

  const [copied, setCopied] = useState(false);
  const listRef = useRef<HTMLOListElement | null>(null);

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(fullText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be denied; ignore */
    }
  };

  const scrollTo = (idx: number) => {
    const el = listRef.current?.querySelector(`[data-seg-index="${idx}"]`) as HTMLElement | null;
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const toggleSpeaker = (label: string) => {
    setActiveSpeakers((prev) => {
      const next = new Set(prev ?? speakers);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const clearFilter = () => setActiveSpeakers(null);

  if (sorted.length === 0) {
    return (
      <div
        className={`text-xs text-gray-400 italic p-4 border border-dashed border-gray-200 dark:border-gray-700 rounded ${
          className || ''
        }`}
      >
        {emptyHint}
      </div>
    );
  }

  return (
    <div className={`flex flex-col gap-2 ${fillHeight ? 'h-full min-h-0' : ''} ${className || ''}`}>
      {(showSpeakers && speakers.length > 1) || chapters.length > 1 ? (
        <div className="flex flex-wrap items-center gap-1.5 px-1">
          {showSpeakers && speakers.length > 1 && (
            <>
              <span className="text-[11px] text-gray-500 mr-1">说话人：</span>
              {speakers.map((label) => {
                const isActive = activeSpeakers === null || activeSpeakers.has(label);
                return (
                  <button
                    key={label}
                    type="button"
                    onClick={() => toggleSpeaker(label)}
                    className={`text-[10px] inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border transition-opacity ${speakerBadgeClass(
                      label
                    )} ${isActive ? 'opacity-100' : 'opacity-30'}`}
                    title={`仅显示 ${label} 的发言`}
                  >
                    <UserCircle2 size={10} />
                    {label}
                  </button>
                );
              })}
              {activeSpeakers && (
                <button
                  type="button"
                  onClick={clearFilter}
                  className="text-[10px] text-sky-600 hover:underline ml-1"
                >
                  清除筛选
                </button>
              )}
            </>
          )}
          {chapters.length > 1 && (
            <span className={`flex gap-2 ${showSpeakers && speakers.length > 1 ? 'ml-auto' : 'ml-auto w-full justify-end'}`}>
              <button
                type="button"
                onClick={expandAll}
                className="text-[10px] text-sky-600 hover:underline"
              >
                展开章节
              </button>
              <button
                type="button"
                onClick={collapseAll}
                className="text-[10px] text-sky-600 hover:underline"
              >
                折叠章节
              </button>
            </span>
          )}
        </div>
      ) : null}

      <div className="flex gap-3 min-h-0">
        <ol
          ref={listRef}
          className={`flex-1 space-y-2 pr-1 ${
            fillHeight
              ? 'min-h-0 overflow-y-auto'
              : `max-h-[${maxHeight}] overflow-y-auto`
          }`}
        >
          {chapters.length === 0 && (
            <li className="text-xs text-gray-400 italic p-3 border border-dashed border-gray-200 rounded">
              当前筛选下没有段落
            </li>
          )}
          {chapters.map((chapter) => {
            const isCollapsed = collapsed.has(chapter.id);
            return (
              <li key={`ch-${chapter.id}`} className="space-y-1.5">
                {/* 章节标题（可点击折叠） */}
                <button
                  type="button"
                  onClick={() => toggleChapter(chapter.id)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 bg-gradient-to-r from-violet-50 to-transparent dark:from-violet-900/30 rounded border-l-2 border-violet-400 hover:from-violet-100 transition-colors"
                >
                  {isCollapsed ? (
                    <ChevronRight size={14} className="text-violet-600" />
                  ) : (
                    <ChevronDown size={14} className="text-violet-600" />
                  )}
                  <span className="text-[12px] font-semibold text-violet-800 dark:text-violet-200 truncate">
                    {chapter.title}
                  </span>
                  <span className="text-[10px] text-gray-400 ml-auto">
                    {chapter.segments.length} 段
                  </span>
                </button>

                {/* 关键词高亮（仅章节首部显示） */}
                {!isCollapsed && chapter.keywords && chapter.keywords.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1 px-2">
                    <Hash size={10} className="text-amber-500" />
                    {chapter.keywords.map((k) => (
                      <span
                        key={k}
                        className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                      >
                        {k}
                      </span>
                    ))}
                  </div>
                )}

                {!isCollapsed && (
                  <ol className="space-y-1.5 pl-2">
                    {chapter.segments.filter((s) => (s.text || '').trim().length > 0).map((seg) => {
                      const label = seg.speakerLabel;
                      return (
                        <li
                          key={seg.id}
                          data-seg-index={seg.segmentIndex}
                          className="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-md p-2.5 hover:border-sky-300 cursor-pointer"
                          onClick={() => scrollTo(seg.segmentIndex)}
                        >
                          <div className="flex items-center gap-2 text-[11px] text-gray-500">
                            <span className="font-mono">
                              {formatTime(seg.startTime)} – {formatTime(seg.endTime)}
                            </span>
                            {label && (
                              <span
                                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border ${speakerBadgeClass(label)}`}
                              >
                                <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-white/60 text-[9px] font-bold">
                                  {speakerInitial(label)}
                                </span>
                                {label}
                              </span>
                            )}
                            {typeof seg.confidence === 'number' && (
                              <span className="text-gray-400">
                                置信度 {(seg.confidence * 100).toFixed(0)}%
                              </span>
                            )}
                          </div>
                          <p className="mt-1 text-sm text-gray-800 dark:text-gray-100 leading-relaxed whitespace-pre-wrap">
                            {highlightText(seg.text, chapter.keywords)}
                          </p>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </li>
            );
          })}
        </ol>
        <div className="w-10 flex-shrink-0">
          <button
            type="button"
            onClick={copyAll}
            className="w-full text-[11px] flex flex-col items-center gap-1 py-2 rounded border border-gray-200 dark:border-gray-700 text-gray-500 hover:text-sky-600 hover:border-sky-300"
            title="复制全文（含说话人）"
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
            {copied ? '已复制' : '复制'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default TranscriptView;
