import React, { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, ListChecks, Send, X, AlertCircle } from 'lucide-react';
import SearchableSelect, { type SearchableSelectOption } from '../SearchableSelect';

export interface MeetingTaskCandidate {
  name: string;
  responsible?: string | null;
  responsibleUserId?: string | null;
  plannedStart?: string | null;
  plannedEnd?: string | null;
  priority?: string | null;
  description?: string | null;
}

interface UserOption {
  id: string;
  name?: string;
  realName?: string;
  real_name?: string;
  email?: string;
}

interface MeetingTaskConfirmModalProps {
  open: boolean;
  isDark?: boolean;
  items: MeetingTaskCandidate[];
  users: UserOption[];
  /** 项目组成员 userId，优先排在责任人列表顶部 */
  memberUserIds?: string[];
  /** 会议参会人，次优先 */
  attendeesIds?: string[];
  submitting?: boolean;
  onClose: () => void;
  onConfirm: (items: MeetingTaskCandidate[], publish: boolean) => void | Promise<void>;
}

const todayISO = () => new Date().toISOString().slice(0, 10);

const displayName = (u: UserOption) => u.realName || u.real_name || u.name || u.email || u.id;

export const MeetingTaskConfirmModal: React.FC<MeetingTaskConfirmModalProps> = ({
  open,
  isDark = false,
  items,
  users,
  memberUserIds = [],
  attendeesIds = [],
  submitting,
  onClose,
  onConfirm,
}) => {
  const [rows, setRows] = useState<MeetingTaskCandidate[]>([]);

  useEffect(() => {
    if (!open) return;
    const start = todayISO();
    setRows(
      items.map((it) => ({
        name: it.name || it.description || '',
        responsibleUserId: it.responsibleUserId || '',
        responsible: it.responsible || '',
        plannedStart: (it.plannedStart || start).slice(0, 10),
        plannedEnd: (it.plannedEnd || start).slice(0, 10),
        priority: it.priority || 'Medium',
        description: it.description || it.name || '',
      })),
    );
  }, [open, items]);

  const responsibleOptions: SearchableSelectOption[] = useMemo(() => {
    const memberSet = new Set(memberUserIds.map(String));
    const attendeeSet = new Set(attendeesIds.map(String));

    const rank = (id: string) => {
      if (memberSet.has(id)) return 0;
      if (attendeeSet.has(id)) return 1;
      return 2;
    };

    const groupMeta = (id: string) => {
      if (memberSet.has(id)) return '项目成员';
      if (attendeeSet.has(id)) return '参会人';
      return '其他用户';
    };

    return [...users]
      .sort((a, b) => {
        const aid = String(a.id);
        const bid = String(b.id);
        const ra = rank(aid);
        const rb = rank(bid);
        if (ra !== rb) return ra - rb;
        return displayName(a).localeCompare(displayName(b), 'zh');
      })
      .map((u) => {
        const id = String(u.id);
        return {
          value: id,
          label: displayName(u),
          meta: [groupMeta(id), u.email].filter(Boolean).join(' · '),
        };
      });
  }, [users, memberUserIds, attendeesIds]);

  const updateRow = (idx: number, patch: Partial<MeetingTaskCandidate>) => {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const removeRow = (idx: number) => {
    setRows((prev) => prev.filter((_, i) => i !== idx));
  };

  const invalid = rows.some(
    (r) =>
      !r.name?.trim() ||
      !r.responsibleUserId ||
      !r.plannedStart ||
      !r.plannedEnd,
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div
        className={`w-full max-w-5xl max-h-[90vh] flex flex-col rounded-2xl shadow-xl ${
          isDark ? 'bg-gray-800' : 'bg-white'
        }`}
      >
        <div
          className={`flex items-center justify-between px-5 py-4 border-b ${
            isDark ? 'border-gray-700' : 'border-gray-200'
          }`}
        >
          <div className="flex items-center gap-2">
            <ListChecks size={16} className="text-emerald-500" />
            <h3 className={`text-sm font-semibold ${isDark ? 'text-white' : 'text-gray-900'}`}>
              确认会议任务清单
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="p-1 text-gray-400 hover:text-gray-600 rounded"
          >
            <X size={16} />
          </button>
        </div>

        {/* 单层滚动，避免 overflow 裁切责任人 SearchableSelect 下拉 */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          <div className="px-5 py-3 text-xs text-amber-700 dark:text-amber-300 flex items-start gap-2 bg-amber-50/80 dark:bg-amber-900/20 sticky top-0 z-10">
            <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
            <span>
              责任人、计划开始、计划结束均为必填。责任人支持搜索筛选，项目组成员优先显示在列表上方。
            </span>
          </div>

          <div className="px-5 py-3">
            <table className="w-full text-xs">
              <thead>
                <tr className={`text-left ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
                  <th className="py-2 pr-2 font-medium">任务名称 *</th>
                  <th className="py-2 pr-2 font-medium w-56">责任人 *</th>
                  <th className="py-2 pr-2 font-medium w-32">开始 *</th>
                  <th className="py-2 pr-2 font-medium w-32">结束 *</th>
                  <th className="py-2 pr-2 font-medium w-24">优先级</th>
                  <th className="py-2 w-10" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => {
                  const rowInvalid =
                    !row.name?.trim() || !row.responsibleUserId || !row.plannedStart || !row.plannedEnd;
                  return (
                    <tr
                      key={idx}
                      className={`border-t ${isDark ? 'border-gray-700' : 'border-gray-100'} ${
                        rowInvalid ? 'bg-rose-50/40 dark:bg-rose-900/10' : ''
                      }`}
                    >
                      <td className="py-2 pr-2 align-top">
                        <input
                          className={`w-full px-2 py-1.5 rounded border text-sm ${
                            isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200'
                          }`}
                          value={row.name || ''}
                          onChange={(e) => updateRow(idx, { name: e.target.value })}
                        />
                      </td>
                      <td className="py-2 pr-2 align-top relative z-20">
                        <SearchableSelect
                          value={row.responsibleUserId || ''}
                          onChange={(uid) => {
                            const opt = responsibleOptions.find((x) => x.value === uid);
                            updateRow(idx, {
                              responsibleUserId: uid,
                              responsible: opt?.label || '',
                            });
                          }}
                          options={responsibleOptions}
                          placeholder="搜索/选择责任人"
                          searchPlaceholder="输入姓名或邮箱筛选…"
                          clearLabel="清空责任人"
                          emptyLabel="无匹配用户"
                          isDark={isDark}
                          allowClear
                        />
                      </td>
                      <td className="py-2 pr-2 align-top">
                        <input
                          type="date"
                          className={`w-full px-2 py-1.5 rounded border ${
                            isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200'
                          }`}
                          value={(row.plannedStart || '').slice(0, 10)}
                          onChange={(e) => updateRow(idx, { plannedStart: e.target.value })}
                        />
                      </td>
                      <td className="py-2 pr-2 align-top">
                        <input
                          type="date"
                          className={`w-full px-2 py-1.5 rounded border ${
                            isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200'
                          }`}
                          value={(row.plannedEnd || '').slice(0, 10)}
                          onChange={(e) => updateRow(idx, { plannedEnd: e.target.value })}
                        />
                      </td>
                      <td className="py-2 pr-2 align-top">
                        <select
                          className={`w-full px-2 py-1.5 rounded border ${
                            isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200'
                          }`}
                          value={row.priority || 'Medium'}
                          onChange={(e) => updateRow(idx, { priority: e.target.value })}
                        >
                          <option value="High">高</option>
                          <option value="Medium">中</option>
                          <option value="Low">低</option>
                        </select>
                      </td>
                      <td className="py-2 align-top">
                        <button
                          type="button"
                          onClick={() => removeRow(idx)}
                          className="p-1 text-gray-400 hover:text-rose-500"
                          title="移除"
                        >
                          <X size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {rows.length === 0 && (
              <p className="text-center text-gray-400 py-8">暂无任务，请关闭后重新提取</p>
            )}
          </div>
        </div>

        <div
          className={`flex-shrink-0 flex items-center justify-end gap-3 px-5 py-4 border-t ${
            isDark ? 'border-gray-700' : 'border-gray-200'
          }`}
        >
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className={`px-4 py-2 text-sm rounded-lg border ${
              isDark
                ? 'border-gray-600 text-gray-300 hover:bg-gray-700'
                : 'border-gray-200 text-gray-700 hover:bg-gray-50'
            }`}
          >
            取消
          </button>
          <button
            type="button"
            disabled={submitting || invalid || rows.length === 0}
            onClick={() => void onConfirm(rows, false)}
            className="flex items-center gap-1.5 px-4 py-2 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
          >
            <CheckCircle2 size={14} />
            {submitting ? '处理中…' : '生成任务'}
          </button>
          <button
            type="button"
            disabled={submitting || invalid || rows.length === 0}
            onClick={() => void onConfirm(rows, true)}
            className="flex items-center gap-1.5 px-4 py-2 text-sm bg-sky-600 text-white rounded-lg hover:bg-sky-700 disabled:opacity-50"
          >
            <Send size={14} />
            {submitting ? '处理中…' : '生成任务并发布'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default MeetingTaskConfirmModal;
