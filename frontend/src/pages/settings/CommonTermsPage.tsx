/**
 * CommonTermsPage — 常用词库管理（常用词投喂 AI）
 *
 * 用户维护领域词条，转写润色与 AI 纪要生成时自动注入提示词。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, Plus, RefreshCw, Search, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/client';

interface CommonTerm {
  id: string;
  term: string;
  alias: string | null;
  remark: string | null;
  isEnabled: boolean;
  usageCount: number;
  createdAt: string;
  updatedAt: string;
}

const CommonTermsPage: React.FC = () => {
  const [items, setItems] = useState<CommonTerm[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<{ term: string; alias: string; remark: string }>({
    term: '', alias: '', remark: '',
  });

  const inputCls = (isDark: boolean) =>
    `w-full px-3 py-2 rounded-lg border text-sm ${
      isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-200 text-gray-900'
    } focus:outline-none focus:ring-2 focus:ring-sky-500`;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.get<CommonTerm[]>('/common-terms');
      setItems(list || []);
    } catch (err) {
      console.error('加载常用词失败', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!form.term.trim()) {
      toast.error('请输入词条');
      return;
    }
    setSaving(true);
    try {
      await api.post<CommonTerm>('/common-terms', {
        term: form.term.trim(),
        alias: form.alias.trim() || null,
        remark: form.remark.trim() || null,
        is_enabled: true,
      });
      toast.success('词条已添加');
      setForm({ term: '', alias: '', remark: '' });
      setShowForm(false);
      await load();
    } catch (err: any) {
      toast.error(err?.message || '添加失败');
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (item: CommonTerm) => {
    try {
      await api.patch<CommonTerm>(`/common-terms/${item.id}`, { is_enabled: !item.isEnabled });
      await load();
    } catch (err) {
      toast.error('更新失败');
    }
  };

  const handleDelete = async (item: CommonTerm) => {
    if (!window.confirm(`确认删除词条「${item.term}」？`)) return;
    try {
      await api.delete(`/common-terms/${item.id}`);
      toast.success('已删除');
      await load();
    } catch (err) {
      toast.error('删除失败');
    }
  };

  const isDark = document.documentElement.classList.contains('dark');
  const filtered = items.filter((it) => {
    if (!searchText.trim()) return true;
    const kw = searchText.trim().toLowerCase();
    return (
      it.term.toLowerCase().includes(kw) ||
      (it.alias || '').toLowerCase().includes(kw) ||
      (it.remark || '').toLowerCase().includes(kw)
    );
  });

  return (
    <div className="p-6 space-y-4 max-w-4xl">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-lg font-bold text-gray-900 dark:text-white">常用词库</h1>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            维护领域专有词条；语音转写润色与 AI 会议纪要生成时会自动把这些词条注入提示词，提升识别与整理准确度（常用词投喂 AI）。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => load()}
            className="p-2 rounded-lg border border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
            title="刷新"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={() => setShowForm((v) => !v)}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-sky-600 text-white text-sm hover:bg-sky-700"
          >
            {showForm ? <X size={14} /> : <Plus size={14} />}
            {showForm ? '取消' : '添加词条'}
          </button>
        </div>
      </div>

      {showForm && (
        <div className="rounded-xl border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">词条 *（≤64字）</label>
              <input
                type="text"
                className={inputCls(isDark)}
                value={form.term}
                onChange={(e) => setForm((f) => ({ ...f, term: e.target.value }))}
                placeholder="如：工装夹具 / 客户产品名 / 内部系统名"
                autoFocus
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">同音/易错写法（逗号分隔）</label>
              <input
                type="text"
                className={inputCls(isDark)}
                value={form.alias}
                onChange={(e) => setForm((f) => ({ ...f, alias: e.target.value }))}
                placeholder="如：工装夹俱, 工装加具"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">备注</label>
            <input
              type="text"
              className={inputCls(isDark)}
              value={form.remark}
              onChange={(e) => setForm((f) => ({ ...f, remark: e.target.value }))}
              placeholder="可选：词条用途说明"
            />
          </div>
          <div className="flex justify-end">
            <button
              onClick={handleCreate}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-sky-600 text-white text-sm hover:bg-sky-700 disabled:opacity-60"
            >
              {saving && <Loader2 size={14} className="animate-spin" />}保存
            </button>
          </div>
        </div>
      )}

      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          className={`${inputCls(isDark)} pl-9`}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          placeholder="搜索词条 / 别名 / 备注"
        />
      </div>

      {loading && items.length === 0 ? (
        <div className="flex items-center justify-center py-12 text-gray-400">
          <Loader2 size={20} className="animate-spin mr-2" /> 加载中…
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center py-12 text-gray-400">
          <BookOpenPlaceholder />
          <p className="text-sm">暂无词条</p>
          <p className="text-xs mt-1">点击右上角「添加词条」开始维护常用词库</p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-100 dark:border-gray-700 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
              <tr>
                <th className="px-4 py-2.5 text-left font-medium">词条</th>
                <th className="px-4 py-2.5 text-left font-medium hidden sm:table-cell">同音/易错写法</th>
                <th className="px-4 py-2.5 text-left font-medium hidden md:table-cell">备注</th>
                <th className="px-4 py-2.5 text-center font-medium">状态</th>
                <th className="px-4 py-2.5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-gray-900 divide-y divide-gray-100 dark:divide-gray-800">
              {filtered.map((it) => (
                <tr key={it.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/60">
                  <td className="px-4 py-2.5 text-gray-900 dark:text-gray-100 font-medium">{it.term}</td>
                  <td className="px-4 py-2.5 text-gray-500 dark:text-gray-400 hidden sm:table-cell">{it.alias || '—'}</td>
                  <td className="px-4 py-2.5 text-gray-500 dark:text-gray-400 hidden md:table-cell">{it.remark || '—'}</td>
                  <td className="px-4 py-2.5 text-center">
                    <button
                      onClick={() => handleToggle(it)}
                      className={`px-2 py-0.5 rounded-full text-xs ${
                        it.isEnabled
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                          : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
                      }`}
                    >
                      {it.isEnabled ? '启用' : '停用'}
                    </button>
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => handleDelete(it)}
                      className="p-1.5 text-gray-400 hover:text-red-600 dark:hover:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20"
                      title="删除"
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const BookOpenPlaceholder: React.FC = () => (
  <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="mb-3 opacity-40">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

export default CommonTermsPage;
