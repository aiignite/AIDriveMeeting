import React, { useState } from 'react';
import { Sparkles, Check, Download, Eye, EyeOff } from 'lucide-react';

export interface AIDraft {
  summary: string;
  decisions: string[];
  action_items: Array<{
    description: string;
    owner?: string | null;
    due?: string | null;
    priority?: string;
  }>;
  raw_text?: string;
  model?: string | null;
  language?: string | null;
}

interface MeetingMinutesAIDraftPanelProps {
  /** True while the backend is generating the draft. */
  loading?: boolean;
  /** The draft returned by the AI tool (or null if none yet). */
  draft: AIDraft | null;
  /** Whether the current minutes record already has an applied draft. */
  currentStatus?: string | null;
  /** Persist the draft onto mtg_minutes (PATCH apply payload). */
  onApply: () => Promise<void> | void;
  /** Mark the minutes as Confirmed (only enabled when status >= AI-Generated). */
  onConfirm: () => Promise<void> | void;
  /** Optional className. */
  className?: string;
}

/**
 * 阶段五 — AI 纪要草稿面板。
 *  关键约束：草稿必须由用户主动点击「应用草稿」才会写入 ai_draft / ai_structured；
 *  「确认纪要」才会将状态置为 Confirmed。任何自动覆盖都被禁止。
 */
export const MeetingMinutesAIDraftPanel: React.FC<MeetingMinutesAIDraftPanelProps> = ({
  loading,
  draft,
  currentStatus,
  onApply,
  onConfirm,
  className,
}) => {
  const [showRaw, setShowRaw] = useState(false);
  const [busy, setBusy] = useState<'apply' | 'confirm' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canApply = !!draft && !loading;
  // 允许直接从 Draft 状态确认（业务上确认是用户的主动行为，无需等 AI-Generated 落地）。
  // 仅在完全没有草稿、或者会议已被归档/取消时禁用。
  const canConfirm =
    !!draft && currentStatus !== 'Cancelled' && currentStatus !== 'Archived';

  const wrap = async (kind: 'apply' | 'confirm', fn: () => Promise<void> | void) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (e: any) {
      setError(e?.message || '操作失败');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={`border border-violet-200 dark:border-violet-800 rounded-lg p-3 bg-violet-50/40 dark:bg-violet-900/10 ${className || ''}`}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5 text-sm font-semibold text-violet-700 dark:text-violet-300">
          <Sparkles size={14} /> AI 纪要草稿
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-gray-500">
          {draft?.model && <span>模型：{draft.model}</span>}
          {draft?.language && <span>· {draft.language}</span>}
        </div>
      </div>

      {!draft && !loading && (
        <p className="text-xs text-gray-500">
          在右侧 AI 助手发出「根据本次会议录音生成纪要」指令后，结构化草稿将在此处显示。
          点击「应用草稿」才会写入数据库，点击「确认纪要」才会标记为 Confirmed。
        </p>
      )}
      {loading && (
        <p className="text-xs text-violet-600">正在调用 LLM 生成草稿…</p>
      )}

      {draft && (
        <div className="space-y-3">
          <section>
            <h4 className="text-[11px] font-semibold text-gray-500 mb-1">摘要</h4>
            <p className="text-sm text-gray-800 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">
              {draft.summary || '（无）'}
            </p>
          </section>

          {draft.decisions?.length > 0 && (
            <section>
              <h4 className="text-[11px] font-semibold text-gray-500 mb-1">决议事项</h4>
              <ul className="list-disc pl-5 text-sm text-gray-800 dark:text-gray-100 space-y-0.5">
                {draft.decisions.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            </section>
          )}

          {draft.action_items?.length > 0 && (
            <section>
              <h4 className="text-[11px] font-semibold text-gray-500 mb-1">行动项</h4>
              <ul className="text-sm text-gray-800 dark:text-gray-100 space-y-1">
                {draft.action_items.map((ai, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-violet-500 flex-shrink-0" />
                    <div>
                      <div>{ai.description}</div>
                      <div className="text-[11px] text-gray-500 mt-0.5 flex items-center gap-2">
                        {ai.owner && <span>负责人：{ai.owner}</span>}
                        {ai.due && <span>截止：{ai.due}</span>}
                        {ai.priority && <span>优先级：{ai.priority}</span>}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {draft.raw_text && (
            <section>
              <button
                type="button"
                onClick={() => setShowRaw((s) => !s)}
                className="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-700"
              >
                {showRaw ? <EyeOff size={12} /> : <Eye size={12} />}
                {showRaw ? '隐藏' : '展开'} Markdown 原文
              </button>
              {showRaw && (
                <pre className="mt-1 max-h-40 overflow-auto text-[11px] bg-white dark:bg-gray-900 p-2 rounded border border-gray-100 dark:border-gray-700 whitespace-pre-wrap">
                  {draft.raw_text}
                </pre>
              )}
            </section>
          )}

          {error && <p className="text-xs text-rose-600">{error}</p>}

          <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-violet-200/70 dark:border-violet-800/60">
            <button
              type="button"
              disabled={!canApply || busy !== null}
              onClick={() => wrap('apply', onApply)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
            >
              <Download size={12} /> {busy === 'apply' ? '写入中…' : '应用草稿'}
            </button>
            <button
              type="button"
              disabled={!canConfirm || busy !== null}
              onClick={() => wrap('confirm', onConfirm)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
              title={!canConfirm ? '需要先生成 AI 草稿' : '标记为 Confirmed'}
            >
              <Check size={12} /> {busy === 'confirm' ? '提交中…' : '确认纪要'}
            </button>
            <span className="text-[11px] text-gray-500">
              当前状态：<span className="font-mono">{currentStatus || 'Draft'}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default MeetingMinutesAIDraftPanel;
