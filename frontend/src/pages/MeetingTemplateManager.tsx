import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Copy, Edit3, FileText, Plus, RefreshCw, Save, Search, Trash2, X } from 'lucide-react';
import { toast } from 'react-hot-toast';

import QuillEditor from '../components/QuillEditor';
import { templatesApi, type Template } from '../services/template';
import {
  deriveMeetingTypesFromTemplates,
  getMeetingTemplateData,
} from '../utils/meetingTemplateUtils';

interface MeetingTemplateManagerProps {
  show: boolean;
  onClose: () => void;
  isDark: boolean;
  onChanged?: () => void | Promise<void>;
}

interface MeetingTemplateForm {
  templateNumber: string;
  name: string;
  meetingType: string;
  description: string;
  agendaHtml: string;
  minutesHtml: string;
  taskTemplateId: string;
  isActive: boolean;
}

interface MeetingTemplateData {
  meetingType?: string;
  agendaHtml?: string;
  minutesHtml?: string;
  taskTemplateId?: string;
  taskTemplateName?: string;
}

const EMPTY_FORM: MeetingTemplateForm = {
  templateNumber: '',
  name: '',
  meetingType: '',
  description: '',
  agendaHtml: '',
  minutesHtml: '',
  taskTemplateId: '',
  isActive: true,
};


const MeetingTemplateManager: React.FC<MeetingTemplateManagerProps> = ({
  show,
  onClose,
  isDark,
  onChanged,
}) => {
  const [items, setItems] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [editingItem, setEditingItem] = useState<Template | null>(null);
  const [form, setForm] = useState<MeetingTemplateForm>(EMPTY_FORM);

  const inputCls = `w-full px-3 py-2 rounded-lg border text-sm ${
    isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-300 text-gray-900'
  } focus:outline-none focus:ring-2 focus:ring-sky-500`;
  const labelCls = `block text-xs font-medium mb-1 ${isDark ? 'text-gray-300' : 'text-gray-600'}`;

  const meetingTypes = useMemo(() => deriveMeetingTypesFromTemplates(items), [items]);
  const isEditingSystem = editingItem?.isSystem === true;

  const loadData = useCallback(async () => {
    if (!show) return;
    setLoading(true);
    try {
      const templates = await templatesApi.list({ templateType: 'meeting', limit: 200 });
      setItems(templates || []);
    } catch (error) {
      console.error(error);
      toast.error('加载会议模板失败');
    } finally {
      setLoading(false);
    }
  }, [show]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const filteredItems = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    if (!keyword) return items;
    return items.filter(item => {
      const templateData = getMeetingTemplateData(item);
      return item.name.toLowerCase().includes(keyword)
        || item.templateNumber.toLowerCase().includes(keyword)
        || (item.description || '').toLowerCase().includes(keyword)
        || (templateData.taskTemplateName || '').toLowerCase().includes(keyword);
    });
  }, [items, searchText]);

  const applyTemplateToForm = useCallback((item: Template | null) => {
    if (!item) {
      setForm(EMPTY_FORM);
      return;
    }
    const templateData = getMeetingTemplateData(item);
    setForm({
      templateNumber: item.templateNumber,
      name: item.name,
      meetingType: templateData.meetingType || item.category || '',
      description: item.description || '',
      agendaHtml: templateData.agendaHtml || '',
      minutesHtml: templateData.minutesHtml || '',
      taskTemplateId: templateData.taskTemplateId || '',
      isActive: item.isActive,
    });
  }, []);

  const handleNew = useCallback(async () => {
    setEditingItem(null);
    setForm({ ...EMPTY_FORM, templateNumber: '自动生成' });
  }, []);

  useEffect(() => {
    if (!show) return;
    if (items.length === 0 && !editingItem) return;
    if (!editingItem && form.templateNumber) return;
    if (!editingItem && items.length > 0) {
      setEditingItem(items[0]);
      applyTemplateToForm(items[0]);
    }
  }, [applyTemplateToForm, editingItem, form.templateNumber, items, show]);

  const handleEdit = (item: Template) => {
    setEditingItem(item);
    applyTemplateToForm(item);
  };

  const handleSave = async () => {
    if (isEditingSystem) {
      toast.error('系统预置模板不可直接修改，请使用「复制为新模板」');
      return;
    }
    if (!form.templateNumber.trim()) {
      toast.error('模板编号不能为空');
      return;
    }
    if (!form.name.trim()) {
      toast.error('模板名称不能为空');
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        templateType: 'meeting' as const,
        category: form.meetingType || undefined,
        description: form.description || undefined,
        isActive: form.isActive,
        templateData: {
          meetingType: form.meetingType || undefined,
          agendaHtml: form.agendaHtml || '',
          minutesHtml: form.minutesHtml || '',
        },
      };
      if (editingItem) {
        await templatesApi.update(editingItem.id, payload);
        toast.success('会议模板已更新');
      } else {
        await templatesApi.create({ ...payload, templateNumber: form.templateNumber === '自动生成' ? undefined : form.templateNumber });
        toast.success('会议模板已创建');
      }
      await loadData();
      await onChanged?.();
      if (editingItem) {
        const refreshed = items.find(item => item.id === editingItem.id) || null;
        if (refreshed) {
          setEditingItem(refreshed);
          applyTemplateToForm(refreshed);
        }
      }
      if (!editingItem) {
        await handleNew();
      }
    } catch (error) {
      console.error(error);
      toast.error('保存会议模板失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (item: Template) => {
    if (item.isSystem) {
      toast.error('系统预置模板不可删除');
      return;
    }
    if (!window.confirm(`确认删除会议模板“${item.name}”？`)) return;
    setSaving(true);
    try {
      await templatesApi.delete(item.id);
      toast.success('会议模板已删除');
      await loadData();
      await onChanged?.();
      setEditingItem(null);
      setForm(EMPTY_FORM);
      await handleNew();
    } catch (error) {
      console.error(error);
      toast.error('删除会议模板失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDuplicate = async (item: Template) => {
    setSaving(true);
    try {
      const templateData = getMeetingTemplateData(item);
      const copyName = `${item.name}（副本）`;
      await templatesApi.create({
        name: copyName,
        templateType: 'meeting',
        category: templateData.meetingType || item.category || undefined,
        description: item.description || undefined,
        isActive: true,
        templateData: {
          meetingType: templateData.meetingType || item.category || undefined,
          agendaHtml: templateData.agendaHtml || '',
          minutesHtml: templateData.minutesHtml || '',
        },
      });
      toast.success('已复制为新模板');
      await loadData();
      await onChanged?.();
    } catch (error) {
      console.error(error);
      toast.error('复制模板失败');
    } finally {
      setSaving(false);
    }
  };

  if (!show) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className={`w-full max-w-7xl h-[88vh] rounded-2xl shadow-2xl overflow-hidden ${isDark ? 'bg-gray-800 border border-gray-700' : 'bg-white'}`}>
        <div className={`flex items-center justify-between px-6 py-4 border-b ${isDark ? 'border-gray-700' : 'border-gray-100'}`}>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-sky-500 to-sky-600 flex items-center justify-center">
              <FileText size={16} className="text-white" />
            </div>
            <div>
              <h3 className={`text-base font-bold ${isDark ? 'text-white' : 'text-gray-900'}`}>会议模板管理</h3>
              <p className={`text-xs ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>统一管理议程模板、纪要模板与会议任务模板绑定</p>
            </div>
          </div>
          <button onClick={onClose} className={`p-2 rounded-lg ${isDark ? 'hover:bg-gray-700 text-gray-400' : 'hover:bg-gray-100 text-gray-500'}`}>
            <X size={18} />
          </button>
        </div>

        <div className="grid grid-cols-[320px_minmax(0,1fr)] h-[calc(88vh-73px)] overflow-hidden">
          <div className={`border-r p-4 flex flex-col min-h-0 ${isDark ? 'border-gray-700 bg-gray-900/20' : 'border-gray-100 bg-gray-50/80'}`}>
            <div className="flex items-center gap-2 mb-3">
              <div className="relative flex-1">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input value={searchText} onChange={e => setSearchText(e.target.value)} placeholder="搜索模板名称或编号"
                  className={`${inputCls} pl-9`} />
              </div>
              <button onClick={handleNew} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-sky-600 text-white hover:bg-sky-700">
                <Plus size={14} /> 新建
              </button>
            </div>

            <div className="flex-1 overflow-y-auto space-y-2 pr-1">
              {loading ? (
                <div className="flex items-center justify-center py-10 text-gray-400">
                  <RefreshCw size={18} className="animate-spin mr-2" /> 加载中...
                </div>
              ) : filteredItems.length === 0 ? (
                <div className={`rounded-xl border border-dashed p-6 text-center ${isDark ? 'border-gray-700 text-gray-500' : 'border-gray-200 text-gray-400'}`}>
                  暂无会议模板
                </div>
              ) : filteredItems.map(item => {
                const templateData = getMeetingTemplateData(item);
                const active = editingItem?.id === item.id;
                return (
                  <button key={item.id} onClick={() => handleEdit(item)}
                    className={`w-full text-left p-3 rounded-xl border transition-colors ${
                      active
                        ? 'border-sky-500 bg-sky-50 dark:bg-sky-900/20'
                        : `${isDark ? 'border-gray-700 hover:bg-gray-700/50' : 'border-gray-200 hover:bg-white'}`
                    }`}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className={`text-sm font-semibold truncate ${isDark ? 'text-white' : 'text-gray-900'}`}>{item.name}</p>
                        <p className={`text-[11px] font-mono mt-0.5 ${isDark ? 'text-gray-500' : 'text-gray-400'}`}>{item.templateNumber}</p>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        {item.isSystem && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-sky-100 text-sky-700 dark:bg-sky-900/20 dark:text-sky-300">
                            系统
                          </span>
                        )}
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${item.isActive ? 'bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'}`}>
                          {item.isActive ? '启用' : '停用'}
                        </span>
                      </div>
                    </div>
                    <div className={`mt-2 text-xs ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
                      <p>会议类型：{meetingTypes.find(type => type.value === (templateData.meetingType || item.category))?.label || templateData.meetingType || item.category || '未设置'}</p>
                      <p className="truncate mt-1">任务模板：{templateData.taskTemplateName || '未绑定'}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="p-5 overflow-y-auto min-h-0">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h4 className={`text-base font-semibold ${isDark ? 'text-white' : 'text-gray-900'}`}>
                  {editingItem ? (isEditingSystem ? '查看系统模板' : '编辑会议模板') : '新建会议模板'}
                </h4>
                <p className={`text-xs mt-1 ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
                  {isEditingSystem
                    ? '系统预置模板为只读，如需定制请复制为新模板后再编辑。'
                    : '模板创建后可在新建会议时直接套用议程、纪要模板和会议任务模板。'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {editingItem?.isSystem && (
                  <button
                    onClick={() => void handleDuplicate(editingItem)}
                    disabled={saving}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-sky-600 hover:bg-sky-50 dark:hover:bg-sky-900/20"
                  >
                    <Copy size={14} /> 复制为新模板
                  </button>
                )}
                {editingItem && !editingItem.isSystem && (
                  <button onClick={() => handleDelete(editingItem)} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20">
                    <Trash2 size={14} /> 删除
                  </button>
                )}
              </div>
            </div>

            {isEditingSystem && (
              <div className={`mb-4 rounded-lg border px-3 py-2 text-xs ${isDark ? 'border-sky-800 bg-sky-900/20 text-sky-300' : 'border-sky-200 bg-sky-50 text-sky-700'}`}>
                当前为系统预置模板，内容不可直接修改。
              </div>
            )}

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={labelCls}>模板编号</label>
                  <input
                    value={form.templateNumber}
                    onChange={e => setForm(current => ({ ...current, templateNumber: e.target.value }))}
                    placeholder="系统自动获取，失败时可手动输入"
                    className={inputCls}
                    readOnly={isEditingSystem}
                  />
                </div>
                <div>
                  <label className={labelCls}>会议类型 Code</label>
                  <input
                    list="meeting-type-options"
                    className={inputCls}
                    value={form.meetingType}
                    onChange={e => setForm(current => ({ ...current, meetingType: e.target.value }))}
                    placeholder="如 Kickoff / Regular / CCB"
                    readOnly={isEditingSystem}
                  />
                  <datalist id="meeting-type-options">
                    {meetingTypes.map(type => (
                      <option key={type.value} value={type.value}>{type.label}</option>
                    ))}
                  </datalist>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={labelCls}>模板名称</label>
                  <input value={form.name} onChange={e => setForm(current => ({ ...current, name: e.target.value }))} className={inputCls} placeholder="例如：项目周例会模板" readOnly={isEditingSystem} />
                </div>
              </div>

              <div>
                <label className={labelCls}>模板说明</label>
                <textarea rows={2} value={form.description} onChange={e => setForm(current => ({ ...current, description: e.target.value }))} className={inputCls} placeholder="描述这个会议模板适用于什么场景" readOnly={isEditingSystem} />
              </div>

              <div>
                <label className={labelCls}>会议议程模板</label>
                <QuillEditor value={form.agendaHtml} onChange={value => setForm(current => ({ ...current, agendaHtml: value }))} placeholder="输入会议议程模板..." isDark={isDark} minHeight="180px" readOnly={isEditingSystem} />
              </div>

              <div>
                <label className={labelCls}>会议纪要模板</label>
                <QuillEditor value={form.minutesHtml} onChange={value => setForm(current => ({ ...current, minutesHtml: value }))} placeholder="输入会议纪要模板..." isDark={isDark} minHeight="180px" readOnly={isEditingSystem} />
              </div>

              <label className={`flex items-center gap-2 text-sm ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
                <input type="checkbox" checked={form.isActive} onChange={e => setForm(current => ({ ...current, isActive: e.target.checked }))} className="rounded border-gray-300 text-sky-600" disabled={isEditingSystem} />
                启用模板
              </label>
            </div>

            <div className={`flex items-center justify-end gap-3 pt-5 mt-5 border-t ${isDark ? 'border-gray-700' : 'border-gray-100'}`}>
              {editingItem && (
                <button onClick={() => handleEdit(editingItem)} className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm ${isDark ? 'text-gray-300 hover:bg-gray-700' : 'text-gray-600 hover:bg-gray-100'}`}>
                  <Edit3 size={14} /> 重新载入
                </button>
              )}
              {!isEditingSystem && (
                <button onClick={handleSave} disabled={saving} className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-medium bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-60">
                  {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
                  {saving ? '保存中...' : editingItem ? '保存修改' : '创建模板'}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MeetingTemplateManager;