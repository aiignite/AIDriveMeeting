/**
 * MeetingsPage – 会议管理主页（独立应用）
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Plus, Search, ChevronRight, Calendar, MapPin, Link2,
  Clock, CheckCircle2, Circle, Trash2,
  Save, Edit3, RefreshCw, AlertCircle, X, FileText,
  ListTodo, Info, Download, Send, Users, Mic, Loader2, AudioLines, Wand2,
  ListChecks, Maximize2, Minimize2,
} from 'lucide-react';
import { meetingsApi, type MeetingRecording, type MeetingVote, type MeetingDecision } from '../services/meeting';
import { systemApi } from '../services/system';
import { aiApi } from '../services/ai/ai';
import { useApp } from '../contexts/AppContext';
import { useMeetingRecording } from '../hooks/useMeetingRecording';
import { useMeetingTemplates } from '../hooks/useMeetingTemplates';
import { getMeetingTemplateData } from '../utils/meetingTemplateUtils';
import toast from 'react-hot-toast';
import { sanitizeRichText } from '../utils/sanitize';
import QuillEditor from '../components/QuillEditor';
import MeetingTemplateManager from './MeetingTemplateManager';
import AudioRecorder from '../components/meeting/AudioRecorder';
import AudioUploader from '../components/meeting/AudioUploader';
import AudioPlayer from '../components/meeting/AudioPlayer';
import RecordingStatusBadge from '../components/meeting/RecordingStatusBadge';
import TranscriptView from '../components/meeting/TranscriptView';
import MeetingTaskConfirmModal, {
  type MeetingTaskCandidate,
} from '../components/meeting/MeetingTaskConfirmModal';
// 「生成会议纪要」追加到富文本并自动落库；任务清单经确认弹窗写入会议任务。

const formatTranscriptSegmentCount = (count: number): string => {
  if (count <= 0) return '';
  if (count <= 20) return `${count} 段`;
  return `${count} 段 · 段落较碎，建议点击「润色转写」合并`;
};

interface Meeting {
  id: string;
  title: string;
  meetingType: string | null;
  templateKey: string | null;
  location: string | null;
  onlineLink: string | null;
  scheduledStart: string;
  scheduledEnd: string | null;
  agenda: string | null;
  status: string;
  recordingUrl: string | null;
  organizerId: string | null;
  attendeesIds: string[];
  ccList?: string[];
  ccNote?: string | null;
  ccAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

interface MeetingMinutes {
  id: string;
  meetingId: string;
  content: string;
  decisions: Record<string, unknown> | null;
  recordedBy: string | null;
  status?: string | null;
  confirmedAt?: string | null;
  confirmedById?: string | null;
  createdAt: string;
  updatedAt: string;
}

interface ActionItem {
  id: string;
  meetingId: string;
  description: string;
  assigneeId: string | null;
  dueDate: string | null;
  status: string;
  priority: string;
  notes: string | null;
  completedAt: string | null;
}

const MEETING_STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  scheduled:   { label: '待召开', color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  in_progress: { label: '进行中', color: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' },
  completed:   { label: '已完成', color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
  cancelled:   { label: '已取消', color: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400' },
};

const ACTION_STATUS_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  Open:       { label: '待处理', color: 'text-blue-600 dark:text-blue-400',    icon: <Circle size={14} /> },
  InProgress: { label: '进行中', color: 'text-yellow-600 dark:text-yellow-400', icon: <Clock size={14} /> },
  Completed:  { label: '已完成', color: 'text-green-600 dark:text-green-400',   icon: <CheckCircle2 size={14} /> },
  Cancelled:  { label: '已取消', color: 'text-gray-500 dark:text-gray-400',     icon: <X size={14} /> },
};

const PRIORITY_CONFIG: Record<string, { label: string; color: string }> = {
  Low:      { label: '低',  color: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300' },
  Medium:   { label: '中',  color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  High:     { label: '高',  color: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' },
  Critical: { label: '紧急', color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
};

// ── CCB 投票/决议选项配置（对齐后端 mtg_meeting_votes / mtg_meeting_decisions 表） ──

/** CCB 投票选项（vote 字段，对应 chg_meeting_attendees.vote 枚举）。 */
const VOTE_OPTIONS: Array<{ value: string; label: string; color: string }> = [
  { value: 'Approve',            label: '赞成',  color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
  { value: 'Reject',             label: '反对',  color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  { value: 'Abstain',            label: '弃权',  color: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300' },
  { value: 'Conditional_Approve',label: '有条件赞成', color: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' },
];

/** 参会人出席状态选项（attendance 字段）。 */
const ATTENDANCE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'Invited',    label: '已邀请' },
  { value: 'Confirmed',  label: '已确认' },
  { value: 'Attended',   label: '已出席' },
  { value: 'Absent',     label: '缺席' },
  { value: 'Proxy',      label: '代理出席' },
];

/** CCB 决议类型选项（decision_type 字段）。 */
const DECISION_TYPE_OPTIONS: Array<{ value: string; label: string; color: string }> = [
  { value: 'Approved',                 label: '批准',         color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
  { value: 'Approved_With_Conditions', label: '有条件批准',   color: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' },
  { value: 'Rejected',                 label: '驳回',         color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  { value: 'Deferred',                 label: '暂缓',         color: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300' },
];

/** 决议附带行动项状态选项（action_status 字段，对齐 mtg_action_items.status）。 */
const ACTION_STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'Open',       label: '待处理' },
  { value: 'InProgress', label: '进行中' },
  { value: 'Completed',  label: '已完成' },
  { value: 'Cancelled',  label: '已取消' },
];

/** 根据 vote 值获取配置（用于显示中文 label 与颜色）。 */
const getVoteConfig = (vote: string | null) =>
  VOTE_OPTIONS.find(o => o.value === vote) ?? { value: vote ?? '', label: vote ?? '未投票', color: 'bg-gray-100 text-gray-500' };

/** 根据 decision_type 值获取配置。 */
const getDecisionConfig = (dt: string) =>
  DECISION_TYPE_OPTIONS.find(o => o.value === dt) ?? { value: dt, label: dt, color: 'bg-gray-100 text-gray-500' };

const fmtDateTime = (dt: string | null): string => {
  if (!dt) return '-';
  const d = new Date(dt);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

const fmtDate = (dt: string | null): string => {
  if (!dt) return '-';
  const d = new Date(dt);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
};

const localISOForInput = (dt: string | null | undefined): string => {
  if (!dt) return '';
  const d = new Date(dt);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

type InfoFormType = {
  title?: string;
  meetingType?: string;
  location?: string;
  onlineLink?: string;
  scheduledStart?: string;
  scheduledEnd?: string;
  agenda?: string;
  status?: string;
};

const MeetingsPage: React.FC = () => {
  const { theme, registerPageAI, unregisterPageAI } = useApp();
  const isDark = theme === 'dark';

  const [detailMode,      setDetailMode]      = useState(false);
  const [selectedMeeting, setSelectedMeeting] = useState<Meeting | null>(null);
  const [activeTab,       setActiveTab]       = useState<'info'|'recording'|'minutes'|'tasks'|'ccb'>('info');

  const [meetings,     setMeetings]     = useState<Meeting[]>([]);
  const [loading,      setLoading]      = useState(false);
  const [searchText,   setSearchText]   = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter,   setTypeFilter]   = useState('all');

  const [showTemplateManager, setShowTemplateManager] = useState(false);
  const {
    meetingTemplates,
    meetingTypes,
    loadMeetingTemplates,
    ensureMeetingTemplates,
    getTemplateData,
    getDefaultTemplateByType,
    getTemplatesForType,
    resolveMinutes,
    getMeetingTypeLabel,
  } = useMeetingTemplates({ autoLoad: false });

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [createForm, setCreateForm] = useState({
    templateId: '',
    title: '', meetingType: '', location: '', onlineLink: '',
    scheduledStart: '', scheduledEnd: '', agenda: '',
  });
  const [creating, setCreating] = useState(false);

  const [minutesList,    setMinutesList]    = useState<MeetingMinutes[]>([]);
  const [minutesContent, setMinutesContent] = useState('');
  const [savingMinutes,  setSavingMinutes]  = useState(false);
  const [minutesSaved,   setMinutesSaved]   = useState(false);

  // ── 阶段五：录音 / 转写 / AI 草稿 ───────────────────────────────
  // 录音相关的 state / effect 全部抽到 useMeetingRecording hook
  // 任务2: 「生成会议纪要」按钮状态
  const [generatingMinutes, setGeneratingMinutes] = useState(false);
  // 会议纪要生成进度（0-100，模拟 0→95 平滑爬升，请求成功直接跳 100）
  const [minutesGenProgress, setMinutesGenProgress] = useState(0);
  const [aiModels, setAiModels] = useState<Array<{ id: string; name: string; modelId: string; popularity?: number }>>([]);
  const [selectedMinutesModel, setSelectedMinutesModel] = useState('');
  const [extractingTasks, setExtractingTasks] = useState(false);
  const [taskConfirmOpen, setTaskConfirmOpen] = useState(false);
  const [taskCandidates, setTaskCandidates] = useState<MeetingTaskCandidate[]>([]);
  const [confirmingTasks, setConfirmingTasks] = useState(false);
  const uploadPickerRef = useRef<(() => void) | null>(null);

  const [editingInfo, setEditingInfo] = useState(false);
  const [infoForm,    setInfoForm]    = useState<InfoFormType>({});
  const [savingInfo,  setSavingInfo]  = useState(false);

  const [actionItems, setActionItems] = useState<ActionItem[]>([]);

  // ── CCB 投票/决议状态 ──
  const isCCBContext = true;
  const [votes, setVotes] = useState<MeetingVote[]>([]);
  const [decisions, setDecisions] = useState<MeetingDecision[]>([]);
  const [votesLoading, setVotesLoading] = useState(false);
  const [decisionsLoading, setDecisionsLoading] = useState(false);
  const [voteSaving, setVoteSaving] = useState(false);
  const [decisionSaving, setDecisionSaving] = useState(false);
  // 新增投票表单（参会人 + 角色）
  const [showAddVote, setShowAddVote] = useState(false);
  const [voteForm, setVoteForm] = useState<{ userId: string; role: string; attendance: string }>({
    userId: '', role: 'reviewer', attendance: 'Invited',
  });
  // 新增决议表单
  const [showAddDecision, setShowAddDecision] = useState(false);
  const [decisionForm, setDecisionForm] = useState<{
    decisionType: string; conditions: string; actionItem: string;
    actionAssigneeId: string; actionDueDate: string; actionStatus: string;
  }>({
    decisionType: 'Approved', conditions: '', actionItem: '',
    actionAssigneeId: '', actionDueDate: '', actionStatus: 'Open',
  });

  // ── CC (抄送) 状态 ──
  const [showCCModal,    setShowCCModal]    = useState(false);
  const [ccUserIds,      setCCUserIds]      = useState<string[]>([]);
  const [ccMessage,      setCCMessage]      = useState('');
  const [ccSearch,       setCCSearch]       = useState('');
  const [allUsers,       setAllUsers]       = useState<any[]>([]);
  const [ccSending,      setCCSending]      = useState(false);

  // ── 导出状态 ──
  const [exportLoading,  setExportLoading]  = useState(false);

  // AI 模型仅在纪要 Tab 需要，避免列表首屏额外请求
  useEffect(() => {
    if (!detailMode || activeTab !== 'minutes' || aiModels.length > 0) return;
    void aiApi.listModels()
      .then((rows: unknown) => {
        const list = (Array.isArray(rows) ? rows : []).map((m: any) => ({
          id: String(m.id),
          name: String(m.name || m.modelId || m.model_id || ''),
          modelId: String(m.modelId || m.model_id || m.name || ''),
          popularity: Number(m.popularity ?? 0),
        }));
        list.sort((a, b) => (b.popularity || 0) - (a.popularity || 0));
        setAiModels(list);
      })
      .catch(() => setAiModels([]));
  }, [detailMode, activeTab, aiModels.length]);

  // 录音仅在进入录音 Tab 后加载，避免打开详情就拉转写
  const recording = useMeetingRecording(
    selectedMeeting?.id ?? null,
    { enabled: detailMode && activeTab === 'recording' },
  );
  const {
    recordings, activeRecording, transcriptSegments,
    isRecordingsLoading, isTranscriptLoading,
    isUploading: uploadingRecording, error: recordingError,
    retranscribingId, refiningId,
  } = recording;

  const loadMeetings = useCallback(async () => {
    setLoading(true);
    try {
      const list = await meetingsApi.list({ activeOnly: false });
      setMeetings(list || []);
    } catch (err) { console.error('加载会议列表失败', err); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadMeetings(); }, [loadMeetings]);

  const loadMinutes = useCallback(async (
    meetingId: string,
    templateKey?: string | null,
    meetingType?: string | null,
  ) => {
    try {
      const list = await meetingsApi.listMinutes(meetingId);
      setMinutesList(list || []);
      const existing = (list as MeetingMinutes[])?.[0]?.content;
      if (existing) {
        setMinutesContent(existing);
      } else {
        await ensureMeetingTemplates();
        setMinutesContent(resolveMinutes(templateKey, meetingType) || '');
      }
    } catch (err) { console.error('加载会议纪要失败', err); }
  }, [ensureMeetingTemplates, resolveMinutes]);

  const openMeetingDetail = (meeting: Meeting) => {
    setSelectedMeeting(meeting);
    setActiveTab('info');
    setInfoForm({});
    setEditingInfo(false);
    setMinutesList([]);
    setMinutesContent('');
    setActionItems([]);
    // 重置 CCB 投票/决议状态（切到 'ccb' Tab 时再懒加载）
    setVotes([]);
    setDecisions([]);
    setShowAddVote(false);
    setShowAddDecision(false);
    // 纪要 / 行动项 / 录音按 Tab 懒加载，避免打开详情抢首屏
    setDetailMode(true);
  };

  // ── 导出 PDF ──
  const exportPDF = async () => {
    if (!selectedMeeting) return;
    setExportLoading(true);
    try {
      // 导出前按需补齐纪要与行动项（详情默认不再预加载）
      let minutesForExport = minutesList;
      let actionsForExport = actionItems;
      if (minutesForExport.length === 0) {
        try {
          const list = await meetingsApi.listMinutes(selectedMeeting.id);
          minutesForExport = (list as MeetingMinutes[]) || [];
          setMinutesList(minutesForExport);
          if (minutesForExport[0]?.content) setMinutesContent(minutesForExport[0].content);
        } catch { /* ignore */ }
      }
      if (actionsForExport.length === 0) {
        try {
          const list = await meetingsApi.listActionItems(selectedMeeting.id);
          actionsForExport = (list as ActionItem[]) || [];
          setActionItems(actionsForExport);
        } catch { /* ignore */ }
      }

      const { default: jsPDF } = await import('jspdf');
      const { default: autoTable } = await import('jspdf-autotable');
      const { loadChineseFontBase64, registerChineseFont, setChineseFont } = await import('../utils/pdfFonts');

      const doc = new jsPDF();

      // 加载并注册中文字体
      const fontBase64 = await loadChineseFontBase64();
      registerChineseFont(doc, fontBase64);

      // 标题
      setChineseFont(doc);
      doc.setFontSize(16);
      doc.text(selectedMeeting.title, 14, 20);

      // 基本信息
      doc.setFontSize(10);
      setChineseFont(doc);
      doc.text(`会议时间: ${fmtDateTime(selectedMeeting.scheduledStart)}`, 14, 30);
      if (selectedMeeting.location) doc.text(`地点: ${selectedMeeting.location}`, 14, 36);
      const statusY = selectedMeeting.location ? 42 : 36;
      doc.text(`状态: ${MEETING_STATUS_CONFIG[selectedMeeting.status]?.label || selectedMeeting.status}`, 14, statusY);
      doc.text(`类型: ${getMeetingTypeLabel(selectedMeeting.meetingType)}`, 14, statusY + 6);

      let yPos = statusY + 16;

      // 会议纪要
      if (minutesForExport.length > 0 && minutesForExport[0]?.content) {
        doc.setFontSize(12);
        setChineseFont(doc);
        doc.text('会议纪要', 14, yPos);
        yPos += 8;
        doc.setFontSize(9);
        // 处理富文本内容：去除HTML标签和特殊字符
        const cleanContent = minutesForExport[0].content
          // 替换常见HTML标签为可读文本
          .replace(/<li>/g, '• ')
          .replace(/<\/li>/g, '\n')
          .replace(/<br\s*\/?>/gi, '\n')
          .replace(/<\/p>/gi, '\n\n')
          .replace(/<h[1-6]>/gi, '\n\n')
          .replace(/<\/h[1-6]>/gi, '\n')
          // 移除其他HTML标签
          .replace(/<[^>]+>/g, '')
          // 处理HTML实体
          .replace(/&nbsp;/g, ' ')
          .replace(/&lt;/g, '<')
          .replace(/&gt;/g, '>')
          .replace(/&amp;/g, '&')
          .replace(/&quot;/g, '"')
          .replace(/&#39;/g, "'")
          // 清理多余空白
          .replace(/[ \t]+/g, ' ')
          .replace(/\n{3,}/g, '\n\n')
          .trim();
        const lines = doc.splitTextToSize(cleanContent, 180);
        const maxLines = Math.min(lines.length, 60);
        setChineseFont(doc);
        doc.text(lines.slice(0, maxLines), 14, yPos);
        yPos += maxLines * 5 + 10;
        if (yPos > 270) { doc.addPage(); yPos = 20; }
      }

      // 会议任务
      if (actionsForExport.length > 0) {
        if (yPos > 240) { doc.addPage(); yPos = 20; }
        doc.setFontSize(12);
        setChineseFont(doc);
        doc.text('行动项', 14, yPos);
        yPos += 6;
        autoTable(doc, {
          startY: yPos,
          head: [['任务描述', '状态', '优先级', '截止日期', '备注']],
          body: actionsForExport.map(ai => [
            ai.description,
            ACTION_STATUS_CONFIG[ai.status]?.label || ai.status,
            PRIORITY_CONFIG[ai.priority]?.label || ai.priority,
            fmtDate(ai.dueDate),
            ai.notes || '',
          ]),
          styles: {
            fontSize: 8,
            font: 'NotoSansSC',
            cellPadding: 2,
            lineColor: [200, 200, 200],
            lineWidth: 0.1,
          },
          headStyles: {
            fillColor: [14, 165, 233],
            font: 'NotoSansSC',
            fontSize: 9,
            fontStyle: 'bold',
            textColor: [255, 255, 255],
            halign: 'center',
            cellPadding: 3,
          },
          bodyStyles: {
            font: 'NotoSansSC',
            valign: 'middle',
          },
          columnStyles: {
            0: { cellWidth: 60 },
            1: { cellWidth: 20, halign: 'center' },
            2: { cellWidth: 15, halign: 'center' },
            3: { cellWidth: 25, halign: 'center' },
            4: { cellWidth: 'auto' },
          },
          didParseCell: function(data) {
            // 确保每个单元格都使用中文字体
            data.cell.styles.font = 'NotoSansSC';
          },
        });
      }

      // 生成文件名并下载
      const filename = `会议纪要_${selectedMeeting.title.replace(/[^\w\u4e00-\u9fa5]/g, '_')}_${new Date().toISOString().slice(0, 10)}.pdf`;
      doc.save(filename);
    } catch (err) { console.error('导出PDF失败', err); }
    finally { setExportLoading(false); }
  };

  // ── 导出 Excel ──
  const exportExcel = async () => {
    if (!selectedMeeting) return;
    setExportLoading(true);
    try {
      let minutesForExport = minutesList;
      let actionsForExport = actionItems;
      if (minutesForExport.length === 0) {
        try {
          const list = await meetingsApi.listMinutes(selectedMeeting.id);
          minutesForExport = (list as MeetingMinutes[]) || [];
          setMinutesList(minutesForExport);
        } catch { /* ignore */ }
      }
      if (actionsForExport.length === 0) {
        try {
          const list = await meetingsApi.listActionItems(selectedMeeting.id);
          actionsForExport = (list as ActionItem[]) || [];
          setActionItems(actionsForExport);
        } catch { /* ignore */ }
      }

      const XLSX = await import('xlsx');
      const wb = XLSX.utils.book_new();

      // Sheet 1: 会议信息
      const infoData = [
        ['字段', '内容'],
        ['会议标题', selectedMeeting.title],
        ['会议类型', getMeetingTypeLabel(selectedMeeting.meetingType)],
        ['开始时间', fmtDateTime(selectedMeeting.scheduledStart)],
        ['结束时间', fmtDateTime(selectedMeeting.scheduledEnd)],
        ['地点', selectedMeeting.location || ''],
        ['状态', MEETING_STATUS_CONFIG[selectedMeeting.status]?.label || selectedMeeting.status],
        ['会议纪要', minutesForExport[0]?.content?.replace(/<[^>]+>/g, ' ').trim() || '暂无'],
      ];
      const ws1 = XLSX.utils.aoa_to_sheet(infoData);
      ws1['!cols'] = [{ wch: 14 }, { wch: 60 }];
      XLSX.utils.book_append_sheet(wb, ws1, '会议信息');

      // Sheet 2: 会议任务
      if (actionsForExport.length > 0) {
        const actionData = [
          ['任务描述', '状态', '优先级', '截止日期', '备注'],
          ...actionsForExport.map(ai => [
            ai.description,
            ACTION_STATUS_CONFIG[ai.status]?.label || ai.status,
            PRIORITY_CONFIG[ai.priority]?.label || ai.priority,
            fmtDate(ai.dueDate),
            ai.notes || '',
          ]),
        ];
        const ws2 = XLSX.utils.aoa_to_sheet(actionData);
        ws2['!cols'] = [{ wch: 40 }, { wch: 10 }, { wch: 8 }, { wch: 12 }, { wch: 30 }];
        XLSX.utils.book_append_sheet(wb, ws2, '会议任务');
      }

      const filename = `会议纪要_${selectedMeeting.title.replace(/[^\w\u4e00-\u9fa5]/g, '_')}_${new Date().toISOString().slice(0, 10)}.xlsx`;
      XLSX.writeFile(wb, filename);
    } catch (err) { console.error('导出Excel失败', err); }
    finally { setExportLoading(false); }
  };

  // ── 打开抄送弹窗，同时加载用户列表 ──
  const openCCModal = async () => {
    setCCUserIds([]); setCCMessage(''); setCCSearch('');
    setShowCCModal(true);
    if (allUsers.length === 0) {
      try {
        const users = await systemApi.users();
        setAllUsers(users || []);
      } catch { /* ignore */ }
    }
  };

  // ── 发送抄送 ──
  const handleSendCC = async () => {
    if (!selectedMeeting || ccUserIds.length === 0) return;
    setCCSending(true);
    try {
      const res = await meetingsApi.cc(selectedMeeting.id, {
        user_ids: ccUserIds,
        message: ccMessage.trim() || undefined,
      });
      alert(`已成功抄送给 ${res.sent} 位用户`);
      setShowCCModal(false);
    } catch (err) { console.error('抄送失败', err); alert('抄送失败，请重试'); }
    finally { setCCSending(false); }
  };

  const goBackToList = () => {
    setDetailMode(false);
    setSelectedMeeting(null);
    setEditingInfo(false);
    recording.reset();
    setMinutesList([]);
    setMinutesContent('');
    setActionItems([]);
    // 清理 CCB 投票/决议状态
    setVotes([]);
    setDecisions([]);
    setShowAddVote(false);
    setShowAddDecision(false);
  };

  // ── 录音 / 转写 / 草稿 操作 ─────────────────────────────────────
  // 状态/副作用统一来自 useMeetingRecording；这里只做组件内的事件转发

  const handleTabChange = (tab: 'info'|'recording'|'minutes'|'tasks'|'ccb') => {
    setActiveTab(tab);
    if (selectedMeeting) {
      if (tab === 'minutes')   void loadMinutes(selectedMeeting.id, selectedMeeting.templateKey, selectedMeeting.meetingType);
      if (tab === 'tasks')     void loadActionItems(selectedMeeting.id);
      // recording tab：hook enabled 变 true 后会自动加载；此处再强制刷新一次
      if (tab === 'recording') void recording.loadRecordings(selectedMeeting.id);
      // ccb tab：懒加载 votes/decisions + 补齐 users 列表
      if (tab === 'ccb') {
        void ensureUsersLoaded();
        void loadVotes(selectedMeeting.id);
        void loadDecisions(selectedMeeting.id);
      }
    }
  };

  // 阶段五：把会议上下文注入 AI 助手抽屉
  useEffect(() => {
    if (!selectedMeeting) {
      unregisterPageAI('meetings');
      return;
    }
    const transcriptText = transcriptSegments
      .slice()
      .sort((a, b) => a.segmentIndex - b.segmentIndex)
      .map((s) => `[${Math.floor(s.startTime / 60).toString().padStart(2, '0')}:${(s.startTime % 60).toFixed(0).padStart(2, '0')}] ${s.text}`)
      .join('\n');
    const minutes = minutesList[0];
    const contextHint = [
      `当前会议：${selectedMeeting.title}`,
      `状态：${selectedMeeting.status}`,
      recordings.length > 0
        ? `录音：${recordings.length} 条，最新状态 ${activeRecording?.status || '-'}`
        : '尚无录音',
      transcriptText ? `已转写：${formatTranscriptSegmentCount(transcriptSegments.length)}` : '尚无转写',
      minutes ? `纪要状态：${minutes.status || 'Draft'}` : '尚无纪要',
    ].join('；');

    // 任务2精简：不再监听 AI 助手抽屉返回的草稿事件（已无 AI 卡片），
    // 保留 registerPageAI 以便抽屉仍可被呼出。
    registerPageAI({
      pageName: 'meetings',
      moduleName: 'meeting',
      meetingType: selectedMeeting.meetingType || undefined,
      meetingTitle: selectedMeeting.title,
      recommendedAssistant: '会议纪要助手',
      recommendedSkills: ['meeting_minutes', 'meeting_transcript'],
      contextHint,
      selectedEntities: [
        {
          type: 'meeting',
          id: selectedMeeting.id,
          name: selectedMeeting.title,
        },
      ],
      capabilities: [
        '查询会议转写与纪要',
        '搜索历史会议',
        '生成/完善会议纪要',
        '关联会议任务',
      ],
      quickActions: [
        { label: '查询转写',     prompt: '请调用 get_meeting_transcript 获取本次会议最新一条录音的转写' },
        { label: '搜索历史会议', prompt: '请调用 search_meetings 查询历史会议' },
      ],
    });
    return () => {
      unregisterPageAI('meetings');
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedMeeting, recordings, activeRecording, transcriptSegments, minutesList]);

  // 用 hook 暴露的方法，类型由 hook 保证
  const handleUploadRecording = recording.uploadRecording;
  const handleRetranscribe    = recording.retranscribe;
  const handleRefine          = recording.refine;
  const handleDeleteRecording = (rec: MeetingRecording) => {
    if (window.confirm(`确定删除录音「${rec.fileName}」？该操作不可恢复。`)) {
      void recording.remove(rec);
    }
  };
  const selectRecording = recording.selectRecording;

  /** 加载指定会议的行动项清单（会议任务 Tab）。 */
  const loadActionItems = useCallback(async (meetingId: string) => {
    try {
      const list = await meetingsApi.listActionItems(meetingId);
      setActionItems((list as ActionItem[]) || []);
    } catch (err) {
      console.error('加载会议任务失败', err);
      setActionItems([]);
    }
  }, []);

  // ── CCB 投票/决议加载与 CRUD ───────────────────────────────────────
  // CCB Tab 懒加载：切到 'ccb' Tab 时拉取 votes + decisions，并补齐 users 列表用于显示姓名

  /** 加载指定会议的全部投票记录。 */
  const loadVotes = useCallback(async (meetingId: string) => {
    setVotesLoading(true);
    try {
      const list = await meetingsApi.votes.list(meetingId);
      setVotes(list || []);
    } catch (err) {
      console.error('加载 CCB 投票失败', err);
      setVotes([]);
    } finally {
      setVotesLoading(false);
    }
  }, []);

  /** 加载指定会议的全部结构化决议。 */
  const loadDecisions = useCallback(async (meetingId: string) => {
    setDecisionsLoading(true);
    try {
      const list = await meetingsApi.decisions.list(meetingId);
      setDecisions(list || []);
    } catch (err) {
      console.error('加载 CCB 决议失败', err);
      setDecisions([]);
    } finally {
      setDecisionsLoading(false);
    }
  }, []);

  /** 确保 allUsers 已加载（用于投票/决议中选择责任人/参会人）。 */
  const ensureUsersLoaded = useCallback(async () => {
    if (allUsers.length > 0) return;
    try {
      const users = await systemApi.users();
      setAllUsers(users || []);
    } catch { /* ignore */ }
  }, [allUsers.length]);

  /** 新增一条 CCB 投票记录（参会人 + 角色 + 出席状态，vote 初始为空待确认）。 */
  const handleAddVote = async () => {
    if (!selectedMeeting) return;
    if (!voteForm.role.trim()) {
      toast.error('请输入参会人角色');
      return;
    }
    setVoteSaving(true);
    try {
      await meetingsApi.votes.create(selectedMeeting.id, {
        user_id: voteForm.userId || null,
        role: voteForm.role.trim(),
        attendance: voteForm.attendance,
        vote: null,
        vote_comment: null,
      });
      setShowAddVote(false);
      setVoteForm({ userId: '', role: 'reviewer', attendance: 'Invited' });
      await loadVotes(selectedMeeting.id);
      toast.success('已添加参会人');
    } catch (err: any) {
      toast.error(`添加参会人失败：${err?.message || String(err)}`);
    } finally {
      setVoteSaving(false);
    }
  };

  /** 行内更新某条投票记录的 attendance / vote / vote_comment。 */
  const handleUpdateVote = async (voteId: string, patch: { attendance?: string; vote?: string | null; vote_comment?: string | null }) => {
    if (!selectedMeeting) return;
    try {
      await meetingsApi.votes.update(selectedMeeting.id, voteId, patch);
      setVotes(prev => prev.map(v => v.id === voteId ? {
        ...v,
        attendance: patch.attendance ?? v.attendance,
        vote: patch.vote !== undefined ? patch.vote : v.vote,
        voteComment: patch.vote_comment !== undefined ? patch.vote_comment : v.voteComment,
      } : v));
    } catch (err) {
      console.error('更新投票失败', err);
      toast.error('更新投票失败');
    }
  };

  /** 删除某条投票记录。 */
  const handleDeleteVote = async (voteId: string) => {
    if (!selectedMeeting) return;
    if (!window.confirm('确认移除该参会人？')) return;
    try {
      await meetingsApi.votes.delete(selectedMeeting.id, voteId);
      setVotes(prev => prev.filter(v => v.id !== voteId));
    } catch (err) {
      console.error('删除投票失败', err);
      toast.error('删除投票失败');
    }
  };

  /** 新增一条 CCB 结构化决议。 */
  const handleAddDecision = async () => {
    if (!selectedMeeting) return;
    if (!decisionForm.decisionType.trim()) {
      toast.error('请选择决议类型');
      return;
    }
    setDecisionSaving(true);
    try {
      await meetingsApi.decisions.create(selectedMeeting.id, {
        decision_type: decisionForm.decisionType,
        conditions: decisionForm.conditions.trim() || null,
        action_item: decisionForm.actionItem.trim() || null,
        action_assignee_id: decisionForm.actionAssigneeId || null,
        action_due_date: decisionForm.actionDueDate || null,
        action_status: decisionForm.actionStatus || null,
      });
      setShowAddDecision(false);
      setDecisionForm({
        decisionType: 'Approved', conditions: '', actionItem: '',
        actionAssigneeId: '', actionDueDate: '', actionStatus: 'Open',
      });
      await loadDecisions(selectedMeeting.id);
      toast.success('已添加决议');
    } catch (err: any) {
      toast.error(`添加决议失败：${err?.message || String(err)}`);
    } finally {
      setDecisionSaving(false);
    }
  };

  /** 行内更新某条决议的字段。 */
  const handleUpdateDecision = async (decisionId: string, patch: {
    decisionType?: string; conditions?: string | null; actionItem?: string | null;
    actionAssigneeId?: string | null; actionDueDate?: string | null; actionStatus?: string | null;
  }) => {
    if (!selectedMeeting) return;
    try {
      // camelCase → snake_case 转换
      const payload: Record<string, unknown> = {};
      if (patch.decisionType !== undefined)     payload.decision_type = patch.decisionType;
      if (patch.conditions !== undefined)       payload.conditions = patch.conditions;
      if (patch.actionItem !== undefined)       payload.action_item = patch.actionItem;
      if (patch.actionAssigneeId !== undefined) payload.action_assignee_id = patch.actionAssigneeId;
      if (patch.actionDueDate !== undefined)    payload.action_due_date = patch.actionDueDate;
      if (patch.actionStatus !== undefined)     payload.action_status = patch.actionStatus;
      await meetingsApi.decisions.update(selectedMeeting.id, decisionId, payload);
      setDecisions(prev => prev.map(d => d.id === decisionId ? {
        ...d,
        decisionType: patch.decisionType ?? d.decisionType,
        conditions: patch.conditions !== undefined ? patch.conditions : d.conditions,
        actionItem: patch.actionItem !== undefined ? patch.actionItem : d.actionItem,
        actionAssigneeId: patch.actionAssigneeId !== undefined ? patch.actionAssigneeId : d.actionAssigneeId,
        actionDueDate: patch.actionDueDate !== undefined ? patch.actionDueDate : d.actionDueDate,
        actionStatus: patch.actionStatus !== undefined ? patch.actionStatus : d.actionStatus,
      } : d));
    } catch (err) {
      console.error('更新决议失败', err);
      toast.error('更新决议失败');
    }
  };

  /** 删除某条决议。 */
  const handleDeleteDecision = async (decisionId: string) => {
    if (!selectedMeeting) return;
    if (!window.confirm('确认删除该决议？')) return;
    try {
      await meetingsApi.decisions.delete(selectedMeeting.id, decisionId);
      setDecisions(prev => prev.filter(d => d.id !== decisionId));
    } catch (err) {
      console.error('删除决议失败', err);
      toast.error('删除决议失败');
    }
  };

  /** 根据 user_id 查找用户显示名（用于投票/决议列表中展示姓名）。 */
  const getUserLabel = (userId: string | null): string => {
    if (!userId) return '-';
    const u = allUsers.find(x => String(x.id) === String(userId));
    if (!u) return userId.slice(0, 8);
    return String(u.realName || u.real_name || u.name || u.email || userId);
  };

  const handleTypeChange = (typeValue: string) => {
    const defaultTemplate = typeValue ? getDefaultTemplateByType(typeValue) : undefined;
    const templateData = getMeetingTemplateData(defaultTemplate);
    setCreateForm(f => ({
      ...f,
      meetingType: typeValue,
      templateId: defaultTemplate?.id || '',
      agenda: templateData.agendaHtml || (typeValue ? '' : f.agenda),
      title: defaultTemplate?.name && !f.title ? defaultTemplate.name : f.title,
    }));
  };

  const handleTemplateChange = (templateId: string) => {
    const templateData = getTemplateData(templateId);
    setCreateForm(current => ({
      ...current,
      templateId,
      meetingType: templateData.meetingType || current.meetingType,
      agenda: templateData.agendaHtml || current.agenda,
    }));
  };

  const createModalTemplates = useMemo(() => {
    if (!createForm.meetingType) return meetingTemplates;
    return getTemplatesForType(createForm.meetingType);
  }, [createForm.meetingType, getTemplatesForType, meetingTemplates]);

  const handleCreate = async () => {
    if (!createForm.title.trim()) {
      toast.error('请输入会议标题');
      return;
    }
    if (!createForm.scheduledStart) {
      toast.error('请选择开始时间');
      return;
    }
    setCreating(true);
    try {
      await meetingsApi.create({
        title: createForm.title.trim(),
        meeting_type: createForm.meetingType || null,
        template_key: createForm.templateId || null,
        location: createForm.location || null,
        online_link: createForm.onlineLink || null,
        scheduled_start: createForm.scheduledStart,
        scheduled_end: createForm.scheduledEnd || null,
        agenda: createForm.agenda || null,
      });
      setShowCreateModal(false);
      setCreateForm({
        templateId: '',
        title: '',
        meetingType: '',
        location: '',
        onlineLink: '',
        scheduledStart: '',
        scheduledEnd: '',
        agenda: '',
      });
      loadMeetings();
    } catch (err: any) {
      const msg = err?.message || String(err);
      toast.error(`创建会议失败：${msg}`);
      console.error('创建会议失败', err);
    }
    finally { setCreating(false); }
  };

  const handleSaveInfo = async () => {
    if (!selectedMeeting) return;
    setSavingInfo(true);
    try {
      const updated = await meetingsApi.update(selectedMeeting.id, {
        title:           infoForm.title         ?? selectedMeeting.title,
        meeting_type:    infoForm.meetingType   ?? selectedMeeting.meetingType,
        location:        infoForm.location      ?? selectedMeeting.location,
        online_link:     infoForm.onlineLink    ?? selectedMeeting.onlineLink,
        scheduled_start: infoForm.scheduledStart ?? selectedMeeting.scheduledStart,
        scheduled_end:   infoForm.scheduledEnd  ?? selectedMeeting.scheduledEnd,
        agenda:          infoForm.agenda        ?? selectedMeeting.agenda,
        status:          infoForm.status        ?? selectedMeeting.status,
      });
      setSelectedMeeting(updated);
      setEditingInfo(false);
      setInfoForm({});
      loadMeetings();
    } catch (err) { console.error('保存会议信息失败', err); }
    finally { setSavingInfo(false); }
  };

  const handleSaveMinutes = async () => {
    if (!selectedMeeting) return;
    setSavingMinutes(true);
    try {
      if (minutesList.length > 0) {
        await meetingsApi.updateMinutes(selectedMeeting.id, minutesList[0].id, { content: minutesContent });
      } else {
        const created = await meetingsApi.createMinutes(selectedMeeting.id, { content: minutesContent });
        setMinutesList([created as MeetingMinutes]);
      }
      setMinutesSaved(true);
      setTimeout(() => setMinutesSaved(false), 2500);
    } catch (err) { console.error('保存会议纪要失败', err); }
    finally { setSavingMinutes(false); }
  };

  // ── 基于全部已转写录音生成会议纪要，并自动落库 ──
  const handleGenerateMinutesFromTranscript = async () => {
    if (!selectedMeeting) return;
    const hasTranscribed = recordings.some((r) => r.status === 'Transcribed');
    if (!hasTranscribed && transcriptSegments.length === 0) {
      toast.error('会议下尚无已完成转写的录音，请先录音/上传并等待转写完成。');
      return;
    }
    setGeneratingMinutes(true);
    setMinutesGenProgress(0);
    const startedAt = Date.now();
    const tickMs = 200;
    const ESTIMATED_MS = Math.min(
      240_000,
      Math.max(90_000, Math.max(transcriptSegments.length, 20) * 2_500),
    );
    const tick = window.setInterval(() => {
      const elapsed = Date.now() - startedAt;
      const ratio = Math.min(0.95, elapsed / ESTIMATED_MS);
      const eased = 1 - Math.pow(1 - ratio, 2);
      setMinutesGenProgress(Math.floor(eased * 95));
    }, tickMs);

    try {
      const data = await meetingsApi.generateMinutesFromTranscript(
        selectedMeeting.id,
        {
          mergeAllRecordings: true,
          model: selectedMinutesModel || undefined,
          baseContent: minutesContent || undefined,
        },
      );
      const draft = (data.draft || data) as Record<string, unknown>;
      const summary =
        (data.summaryHtml as string | undefined) ||
        (draft.summaryHtml as string | undefined) ||
        (data.summaryMarkdown as string | undefined) ||
        (draft.summaryMarkdown as string | undefined) ||
        (draft.rawText as string | undefined) ||
        (draft.summary as string | undefined) ||
        '';
      if (!summary) {
        toast.error('大模型未返回有效纪要内容，请稍后重试。');
        return;
      }
      const savedContent =
        (data.savedContent as string | undefined) ||
        (minutesContent.trim() ? `${minutesContent}<hr/>${summary}` : summary);
      setMinutesContent(savedContent);
      if (data.minutesId) {
        const refreshed = await meetingsApi.listMinutes(selectedMeeting.id);
        setMinutesList(Array.isArray(refreshed) ? refreshed : []);
      }
      setMinutesGenProgress(100);
      setMinutesSaved(true);
      setTimeout(() => setMinutesSaved(false), 2500);
      toast.success(
        data.modelUsed
          ? `会议纪要已生成并保存（模型：${data.modelUsed}）`
          : '会议纪要已生成并自动保存',
      );
    } catch (e) {
      console.error('生成纪要失败', e);
      toast.error(`生成纪要失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      window.clearInterval(tick);
      window.setTimeout(() => {
        setGeneratingMinutes(false);
        setMinutesGenProgress(0);
      }, 400);
    }
  };

  const handleExtractTasksFromMinutes = async () => {
    if (!selectedMeeting) return;
    if (!minutesContent.trim() && minutesList.length === 0) {
      toast.error('请先生成或填写会议纪要');
      return;
    }
    setExtractingTasks(true);
    try {
      if (allUsers.length === 0) {
        try {
          const users = await systemApi.users();
          setAllUsers(users || []);
        } catch { /* ignore */ }
      }
      // 若有未保存编辑，先落库再抽取
      if (minutesList.length > 0) {
        await meetingsApi.updateMinutes(
          selectedMeeting.id,
          minutesList[0].id,
          { content: minutesContent },
        );
      } else if (minutesContent.trim()) {
        const created = await meetingsApi.createMinutes(
          selectedMeeting.id,
          { content: minutesContent },
        );
        setMinutesList([created as MeetingMinutes]);
      }
      const result = await meetingsApi.extractTasksFromMinutes(
        selectedMeeting.id,
        { model: selectedMinutesModel || undefined },
      );
      const items = (result.items || []) as MeetingTaskCandidate[];
      if (items.length === 0) {
        toast.error('未从纪要中提取到任务，请检查纪要内容后重试');
        return;
      }
      setTaskCandidates(items);
      setTaskConfirmOpen(true);
    } catch (e) {
      toast.error(`提取任务失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExtractingTasks(false);
    }
  };

  const handleConfirmMeetingTasks = async (
    items: MeetingTaskCandidate[],
    publish: boolean,
  ) => {
    if (!selectedMeeting) return;
    setConfirmingTasks(true);
    try {
      const result = await meetingsApi.confirmMeetingTasks(
        selectedMeeting.id,
        { items, publish },
      );
      setTaskConfirmOpen(false);
      setTaskCandidates([]);
      toast.success(
        publish
          ? `已生成并发布 ${result.created ?? items.length} 条任务`
          : `已生成 ${result.created ?? items.length} 条任务（草稿）`,
      );
      toast.success('已写入会议任务清单');
    } catch (e) {
      toast.error(`写入任务失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setConfirmingTasks(false);
    }
  };

  const handleDeleteMeeting = async (meetingId: string) => {
    if (!window.confirm('确认删除此会议？此操作不可恢复。')) return;
    try {
      await meetingsApi.delete(meetingId);
      loadMeetings();
    } catch (err) { console.error('删除会议失败', err); }
  };

  const filteredMeetings = useMemo(() => meetings.filter(m => {
    const matchSearch = !searchText || m.title.toLowerCase().includes(searchText.toLowerCase());
    const matchStatus = statusFilter === 'all' || m.status === statusFilter;
    const matchType   = typeFilter   === 'all' || m.meetingType === typeFilter;
    return matchSearch && matchStatus && matchType;
  }), [meetings, searchText, statusFilter, typeFilter]);

  // 模板未加载时，用列表里已有的 meetingType 填充筛选项
  const listTypeOptions = useMemo(() => {
    if (meetingTypes.length > 0) return meetingTypes;
    const values = [...new Set(
      meetings.map(m => m.meetingType).filter((v): v is string => !!v),
    )];
    return values.map(value => ({ value, label: value }));
  }, [meetingTypes, meetings]);

  const openCreateModal = () => {
    void (async () => {
      await ensureMeetingTemplates();
      setCreateForm((f) => ({ ...f }));
      setShowCreateModal(true);
    })();
  };

  const openTemplateManager = () => {
    void ensureMeetingTemplates();
    setShowTemplateManager(true);
  };

  const inputCls = `w-full px-3 py-2 rounded-lg border text-sm ${isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-200 text-gray-900'} focus:outline-none focus:ring-2 focus:ring-sky-500`;
  const labelCls = 'block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1';
  const fullscreenShellCls = isFullscreen
    ? 'fixed inset-0 z-[60] bg-gray-50 dark:bg-gray-900 p-4 overflow-auto'
    : '';
  const toggleFullscreenBtn = (
    <button
      type="button"
      onClick={() => setIsFullscreen((f) => !f)}
      title={isFullscreen ? '退出最大化' : '最大化'}
      className="p-1.5 text-gray-400 hover:text-sky-600 dark:hover:text-sky-400 hover:bg-sky-50 dark:hover:bg-sky-900/20 rounded-lg transition-colors"
    >
      {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
    </button>
  );

  // ─────────────────── DETAIL VIEW ───────────────────
  if (detailMode && selectedMeeting) {
    const sCfg = MEETING_STATUS_CONFIG[selectedMeeting.status] ?? { label: selectedMeeting.status, color: 'bg-gray-100 text-gray-600' };

    return (
      <div className={`flex flex-col h-full min-h-0 ${fullscreenShellCls}`}>
        {/* 面包屑 */}
        <div className="flex-shrink-0 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm px-5 py-3 flex items-center gap-2 flex-wrap">
          <button onClick={goBackToList} className="text-sm text-sky-600 dark:text-sky-400 hover:underline font-medium">
            会议列表
          </button>
          <ChevronRight size={14} className="text-gray-300" />
          <span className="text-sm font-medium text-gray-700 dark:text-gray-300 truncate max-w-xs">
            {selectedMeeting.title}
          </span>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${sCfg.color}`}>{sCfg.label}</span>
        </div>

        {/* 会议标题栏 */}
        <div className="flex-shrink-0 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-sky-500 to-sky-600 flex items-center justify-center flex-shrink-0">
                <Calendar size={18} className="text-white" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-gray-900 dark:text-white">{selectedMeeting.title}</h2>
                <div className="flex items-center gap-3 mt-1 text-sm text-gray-500 dark:text-gray-400 flex-wrap">
                  {selectedMeeting.meetingType && (
                    <span className="flex items-center gap-1"><FileText size={13} />{getMeetingTypeLabel(selectedMeeting.meetingType)}</span>
                  )}
                  <span className="flex items-center gap-1"><Clock size={13} />{fmtDateTime(selectedMeeting.scheduledStart)}</span>
                  {selectedMeeting.location && <span className="flex items-center gap-1"><MapPin size={13} />{selectedMeeting.location}</span>}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              {/* 导出 PDF */}
              <button
                onClick={exportPDF}
                disabled={exportLoading}
                title="导出会议纪要 PDF"
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-sky-50 dark:hover:bg-sky-900/20 hover:border-sky-300 hover:text-sky-600 dark:hover:text-sky-400 transition-colors disabled:opacity-50"
              >
                <Download size={13} />PDF
              </button>
              {/* 导出 Excel */}
              <button
                onClick={exportExcel}
                disabled={exportLoading}
                title="导出会议纪要 Excel"
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-green-50 dark:hover:bg-green-900/20 hover:border-green-300 hover:text-green-600 dark:hover:text-green-400 transition-colors disabled:opacity-50"
              >
                <Download size={13} />Excel
              </button>
              {/* 抄送 CC */}
              <button
                onClick={openCCModal}
                title="抄送会议纪要"
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-orange-50 dark:hover:bg-orange-900/20 hover:border-orange-300 hover:text-orange-600 dark:hover:text-orange-400 transition-colors"
              >
                <Send size={13} />抄送
              </button>
              {toggleFullscreenBtn}
              {/* 关闭 */}
              <button onClick={goBackToList} className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
                <X size={18} />
              </button>
            </div>
          </div>
        </div>

        {/* Tab 区域 */}
        <div className="flex-1 min-h-0 flex flex-col bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
          <div className="flex-shrink-0 flex border-b border-gray-100 dark:border-gray-700 px-4 pt-3">
            {[
              { key: 'info',     label: '基本信息',   icon: <Info       size={14} /> },
              { key: 'minutes',  label: '会议纪要',   icon: <FileText   size={14} /> },
              { key: 'tasks',    label: '会议任务',   icon: <ListTodo   size={14} /> },
              { key: 'recording',label: '录音与转写', icon: <Mic        size={14} /> },
              // CCB Tab：参会人投票 + 结构化决议
              ...(isCCBContext ? [{ key: 'ccb', label: 'CCB 投票/决议', icon: <Users size={14} /> }] : []),
            ].map(tab => (
              <button
                key={tab.key}
                onClick={() => handleTabChange(tab.key as 'info'|'recording'|'minutes'|'tasks'|'ccb')}
                className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px mr-1 ${
                  activeTab === tab.key
                    ? 'border-sky-500 text-sky-600 dark:text-sky-400'
                    : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                }`}
              >
                {tab.icon}{tab.label}
              </button>
            ))}
          </div>

          {/* Tab: 基本信息 */}
          {activeTab === 'info' && (
            <div className="flex-1 min-h-0 overflow-y-auto p-5 space-y-5">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">会议信息</h3>
                {!editingInfo ? (
                  <button
                    onClick={() => {
                      setInfoForm({
                        title:         selectedMeeting.title,
                        meetingType:   selectedMeeting.meetingType ?? '',
                        location:      selectedMeeting.location    ?? '',
                        onlineLink:    selectedMeeting.onlineLink  ?? '',
                        scheduledStart: localISOForInput(selectedMeeting.scheduledStart),
                        scheduledEnd:   localISOForInput(selectedMeeting.scheduledEnd),
                        agenda: selectedMeeting.agenda ?? '',
                        status: selectedMeeting.status,
                      });
                      setEditingInfo(true);
                    }}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-sky-600 dark:text-sky-400 border border-sky-200 dark:border-sky-700 rounded-lg hover:bg-sky-50 dark:hover:bg-sky-900/20 transition-colors"
                  >
                    <Edit3 size={14} /> 编辑
                  </button>
                ) : (
                  <div className="flex items-center gap-2">
                    <button onClick={() => { setEditingInfo(false); setInfoForm({}); }} className="px-3 py-1.5 text-sm text-gray-500 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">取消</button>
                    <button onClick={handleSaveInfo} disabled={savingInfo} className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-sky-600 text-white rounded-lg hover:bg-sky-700 transition-colors disabled:opacity-60">
                      <Save size={14} />{savingInfo ? '保存中...' : '保存'}
                    </button>
                  </div>
                )}
              </div>

              {!editingInfo ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {[
                    { label: '会议标题', value: selectedMeeting.title },
                    { label: '会议类型', value: getMeetingTypeLabel(selectedMeeting.meetingType) },
                    { label: '会议状态', value: sCfg.label },
                    { label: '会议地点', value: selectedMeeting.location || '-' },
                    { label: '开始时间', value: fmtDateTime(selectedMeeting.scheduledStart) },
                    { label: '结束时间', value: fmtDateTime(selectedMeeting.scheduledEnd) },
                    { label: '在线链接', value: selectedMeeting.onlineLink || '-' },
                    { label: '参会人数', value: `${selectedMeeting.attendeesIds?.length ?? 0} 人` },
                  ].map(({ label, value }) => (
                    <div key={label} className={`p-3 rounded-lg ${isDark ? 'bg-gray-700/50' : 'bg-gray-50'}`}>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{label}</p>
                      <p className="text-sm font-medium text-gray-900 dark:text-white">{value}</p>
                    </div>
                  ))}
                  {selectedMeeting.agenda && (
                    <div className={`col-span-full p-3 rounded-lg ${isDark ? 'bg-gray-700/50' : 'bg-gray-50'}`}>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">会议议程</p>
                      <div
                        className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed"
                        dangerouslySetInnerHTML={{ __html: sanitizeRichText(selectedMeeting.agenda) }}
                      />
                    </div>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="col-span-full">
                    <label className={labelCls}>会议标题 *</label>
                    <input type="text" className={inputCls} value={infoForm.title ?? ''} onChange={e => setInfoForm(f => ({ ...f, title: e.target.value }))} />
                  </div>
                  <div>
                    <label className={labelCls}>会议类型</label>
                    <select className={inputCls} value={infoForm.meetingType ?? ''} onChange={e => setInfoForm(f => ({ ...f, meetingType: e.target.value }))}>
                      <option value="">请选择</option>
                      {meetingTypes.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelCls}>会议状态</label>
                    <select className={inputCls} value={infoForm.status ?? ''} onChange={e => setInfoForm(f => ({ ...f, status: e.target.value }))}>
                      {Object.entries(MEETING_STATUS_CONFIG).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelCls}>会议地点</label>
                    <input type="text" className={inputCls} value={infoForm.location ?? ''} onChange={e => setInfoForm(f => ({ ...f, location: e.target.value }))} />
                  </div>
                  <div>
                    <label className={labelCls}>在线链接</label>
                    <input type="url" className={inputCls} value={infoForm.onlineLink ?? ''} onChange={e => setInfoForm(f => ({ ...f, onlineLink: e.target.value }))} />
                  </div>
                  <div>
                    <label className={labelCls}>开始时间 *</label>
                    <input type="datetime-local" className={inputCls} value={infoForm.scheduledStart ?? ''} onChange={e => setInfoForm(f => ({ ...f, scheduledStart: e.target.value }))} />
                  </div>
                  <div>
                    <label className={labelCls}>结束时间</label>
                    <input type="datetime-local" className={inputCls} value={infoForm.scheduledEnd ?? ''} onChange={e => setInfoForm(f => ({ ...f, scheduledEnd: e.target.value }))} />
                  </div>
                  <div className="col-span-full">
                    <label className={labelCls}>会议议程</label>
                    <textarea rows={4} className={inputCls} value={infoForm.agenda ?? ''} onChange={e => setInfoForm(f => ({ ...f, agenda: e.target.value }))} />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Tab: 录音与转写 — 上传 + 历史查询 */}
          {activeTab === 'recording' && (
            <div className="flex-1 min-h-0 overflow-y-auto p-5 space-y-5">
              <div className="rounded-lg border border-sky-200 dark:border-sky-700/40 bg-sky-50 dark:bg-sky-900/20 text-sky-800 dark:text-sky-300 text-xs px-3 py-2">
                可在此上传音频，或在「会议纪要」Tab 录音/上传。HTTP 内网无法浏览器录音时请上传文件；启用 HTTPS 后可直接录音。
              </div>

              <AudioUploader
                disabled={uploadingRecording}
                onSelected={(file) => void handleUploadRecording(file)}
              />

              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-2">
                  <AudioLines size={14} /> 录音与转写
                </h3>
                <div className="flex items-center gap-2 text-[11px] text-gray-500">
                  {activeRecording && (
                    <RecordingStatusBadge
                      status={activeRecording.status}
                      progressPct={activeRecording.progressPct}
                      errorMessage={activeRecording.errorMessage}
                    />
                  )}
                </div>
              </div>

              {recordingError && (
                <div className="rounded-md border border-rose-200 bg-rose-50 text-rose-700 text-xs px-3 py-2">
                  {recordingError}
                </div>
              )}

              {/* 录音列表 + 详情（仅历史查询；录音/上传入口已迁移到「会议纪要」Tab） */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="md:col-span-1 border border-gray-100 dark:border-gray-700 rounded-lg overflow-hidden">
                  <div className="px-3 py-2 text-xs font-semibold text-gray-600 border-b border-gray-100 dark:border-gray-700">
                    录音列表（{recordings.length}）
                  </div>
                  {isRecordingsLoading ? (
                    <p className="text-xs text-gray-400 p-3">加载中…</p>
                  ) : recordings.length === 0 ? (
                    <p className="text-xs text-gray-400 p-3">尚未上传录音</p>
                  ) : (
                    <ul className="divide-y divide-gray-100 dark:divide-gray-700 max-h-80 overflow-y-auto">
                      {recordings.map((r) => (
                        <li
                          key={r.id}
                          className={`px-3 py-2 cursor-pointer hover:bg-sky-50/40 ${
                            activeRecording?.id === r.id ? 'bg-sky-50' : ''
                          }`}
                          onClick={() => selectRecording(r)}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs text-gray-800 dark:text-gray-100 truncate">
                              {r.fileName}
                            </span>
                            <button
                              type="button"
                              title="删除"
                              onClick={(e) => { e.stopPropagation(); handleDeleteRecording(r); }}
                              className="text-gray-300 hover:text-rose-500"
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                          <div className="mt-1 flex items-center justify-between">
                            <RecordingStatusBadge
                              status={r.status}
                              progressPct={r.progressPct}
                              errorMessage={r.errorMessage}
                            />
                            <span className="text-[10px] text-gray-400">
                              {r.fileSize > 0 ? `${(r.fileSize / 1024 / 1024).toFixed(1)}MB` : '—'}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="md:col-span-2 space-y-3">
                  {!activeRecording ? (
                    <div className="border border-dashed border-gray-200 dark:border-gray-700 rounded-lg p-6 text-center text-xs text-gray-400">
                      请选择左侧录音以查看转写
                    </div>
                  ) : (
                    <div className="border border-gray-100 dark:border-gray-700 rounded-lg p-3 space-y-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-semibold text-gray-700">
                          {activeRecording.fileName}
                        </span>
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => handleRetranscribe(activeRecording)}
                            disabled={retranscribingId === activeRecording.id}
                            className="text-[11px] inline-flex items-center gap-1 px-2 py-0.5 border border-gray-200 rounded hover:border-sky-300 hover:text-sky-600 disabled:opacity-60 disabled:cursor-not-allowed"
                          >
                            {retranscribingId === activeRecording.id ? (
                              <Loader2 size={10} className="animate-spin" />
                            ) : (
                              <RefreshCw size={10} />
                            )}
                            {retranscribingId === activeRecording.id ? '重转写中…' : '重转写'}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleRefine(activeRecording)}
                            disabled={refiningId === activeRecording.id || activeRecording.status !== 'Transcribed'}
                            className="text-[11px] inline-flex items-center gap-1 px-2 py-0.5 border border-violet-200 text-violet-700 rounded hover:border-violet-400 hover:text-violet-900 disabled:opacity-60 disabled:cursor-not-allowed"
                            title="调 LLM 修正错别字、加标点、按停顿分段"
                          >
                            {refiningId === activeRecording.id ? (
                              <Loader2 size={10} className="animate-spin" />
                            ) : (
                              <Wand2 size={10} />
                            )}
                            {refiningId === activeRecording.id ? '润色中…' : '润色转写'}
                          </button>
                        </div>
                      </div>
                      {/* storagePath 形如 "static/uploads/meetings/..."，相对 backend 根。
                          子路径部署（VITE_BASE_PATH=/meeting/）下须拼上 BASE_URL，
                          否则浏览器请求 /static/* 会被外层 nginx 判 404 */}
                      <AudioPlayer
                        src={`${(import.meta.env.BASE_URL ?? '/').replace(/\/+$/, '')}/${activeRecording.storagePath}`}
                        mimeType={activeRecording.mimeType || undefined}
                      />
                      <div className="mt-2">
                        <div className="flex items-center justify-between mb-1">
                          <h5 className="text-[11px] font-semibold text-gray-500">转写全文</h5>
                          {transcriptSegments.length > 0 && (
                            <span className="text-[10px] text-gray-400">
                              {formatTranscriptSegmentCount(transcriptSegments.length)}
                            </span>
                          )}
                        </div>
                        {isTranscriptLoading ? (
                          <p className="text-xs text-gray-400">加载转写中…</p>
                        ) : (
                          <TranscriptView
                            segments={transcriptSegments}
                            emptyHint={
                              activeRecording.status === 'Transcribing'
                                ? '正在转写中…'
                                : activeRecording.status === 'Failed'
                                  ? '转写失败：' + (activeRecording.errorMessage || '未知错误')
                                  : '暂无转写内容'
                            }
                          />
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Tab: 会议纪要 */}
          {activeTab === 'minutes' && (
            <div className="flex-1 min-h-0 flex flex-col p-5 space-y-4">
              <div className="flex-shrink-0 flex items-center justify-between gap-3 flex-wrap">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-2">
                  <Mic size={14} className="text-sky-500" /> 会议纪要
                </h3>
                <div className="flex items-center gap-2 flex-wrap">
                  <select
                    value={selectedMinutesModel}
                    onChange={(e) => setSelectedMinutesModel(e.target.value)}
                    className="text-xs border border-gray-200 dark:border-gray-600 rounded-lg px-2 py-2 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 max-w-[180px]"
                    title="从大模型清单选择；留空则按系统优先级自动选择"
                  >
                    <option value="">模型：自动（按优先级）</option>
                    {aiModels.map((m) => (
                      <option key={m.id} value={m.modelId || m.name}>
                        {m.name || m.modelId}
                      </option>
                    ))}
                  </select>
                  <AudioRecorder
                    variant="compact"
                    iconOnly
                    disabled={uploadingRecording}
                    onCaptured={(blob, fileName) => handleUploadRecording(blob, fileName)}
                    onFallbackUpload={() => uploadPickerRef.current?.()}
                    className="!w-auto"
                  />
                  <AudioUploader
                    variant="compact"
                    iconOnly
                    disabled={uploadingRecording}
                    openRef={uploadPickerRef}
                    onSelected={(file) => void handleUploadRecording(file)}
                  />
                  <button
                    onClick={handleGenerateMinutesFromTranscript}
                    disabled={
                      generatingMinutes ||
                      (recordings.length === 0 && transcriptSegments.length === 0)
                    }
                    title={
                      recordings.length === 0
                        ? '请先录音或上传音频'
                        : generatingMinutes
                          ? `正在生成会议纪要 ${minutesGenProgress}%`
                          : '合并全部已转写录音，用大模型生成会议纪要并自动保存'
                    }
                    aria-label={generatingMinutes ? `生成中 ${minutesGenProgress}%` : '生成会议纪要'}
                    className="relative inline-flex items-center justify-center p-2 bg-gradient-to-r from-violet-600 to-indigo-600 text-white rounded-lg hover:from-violet-700 hover:to-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-sm overflow-hidden"
                  >
                    {generatingMinutes && (
                      <span
                        className="absolute inset-0 bg-white/20 transition-all duration-200 ease-out"
                        style={{ width: `${minutesGenProgress}%` }}
                      />
                    )}
                    <span className="relative">
                      {generatingMinutes
                        ? <Loader2 size={14} className="animate-spin" />
                        : <Wand2 size={14} />}
                    </span>
                  </button>
                  <button
                    onClick={() => void handleExtractTasksFromMinutes()}
                    disabled={extractingTasks || (!minutesContent.trim() && minutesList.length === 0)}
                    title={extractingTasks ? '正在提取任务清单…' : '根据会议纪要内容提取任务清单，确认后写入会议任务'}
                    aria-label={extractingTasks ? '提取中' : '生成任务清单'}
                    className="inline-flex items-center justify-center p-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 transition-colors disabled:opacity-50 shadow-sm"
                  >
                    {extractingTasks ? <Loader2 size={14} className="animate-spin" /> : <ListChecks size={14} />}
                  </button>
                  <button
                    onClick={handleSaveMinutes}
                    disabled={savingMinutes}
                    title={savingMinutes ? '保存中…' : minutesSaved ? '已保存' : '保存纪要'}
                    aria-label={savingMinutes ? '保存中' : minutesSaved ? '已保存' : '保存纪要'}
                    className="inline-flex items-center justify-center p-2 bg-sky-600 text-white rounded-lg hover:bg-sky-700 transition-colors disabled:opacity-60 shadow-sm"
                  >
                    {savingMinutes ? <RefreshCw size={14} className="animate-spin" /> : minutesSaved ? <CheckCircle2 size={14} /> : <Save size={14} />}
                  </button>
                </div>
              </div>

              {uploadingRecording && (
                <p className="text-xs text-sky-600 flex items-center gap-1.5">
                  <Loader2 size={12} className="animate-spin" /> 上传中…
                </p>
              )}

              {/* 当前录音状态卡片：让用户看到转写进度 */}
              {activeRecording && (
                <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-lg border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800">
                  <div className="flex items-center gap-2 min-w-0">
                    <AudioLines size={14} className="text-sky-500 flex-shrink-0" />
                    <span className="text-xs font-medium text-gray-700 dark:text-gray-300 truncate max-w-[260px]">
                      {activeRecording.fileName}
                    </span>
                    <RecordingStatusBadge
                      status={activeRecording.status}
                      progressPct={activeRecording.progressPct}
                      errorMessage={activeRecording.errorMessage}
                    />
                  </div>
                  <span className="text-[10px] text-gray-400">
                    {transcriptSegments.length > 0
                      ? `已转写 ${formatTranscriptSegmentCount(transcriptSegments.length)}`
                      : activeRecording.status === 'Transcribing'
                        ? '转写中…'
                        : activeRecording.status === 'Failed'
                          ? '转写失败'
                          : '等待转写'}
                  </span>
                </div>
              )}

              {generatingMinutes && (
                <div className="flex-shrink-0 rounded-lg border border-violet-200 dark:border-violet-700/40 bg-violet-50/60 dark:bg-violet-900/10 px-3 py-2 space-y-1.5">
                  <p className="text-xs text-violet-700 dark:text-violet-300 flex items-center gap-1.5">
                    <Loader2 size={12} className="animate-spin" />
                    正在整理结构化会议纪要（分主题讨论 + 行动项），长录音可能需要 1~3 分钟…
                  </p>
                  {/* 进度条 + 百分比 */}
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-1.5 rounded-full bg-violet-200/60 dark:bg-violet-900/40 overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-violet-500 to-indigo-500 transition-all duration-200 ease-out"
                        style={{ width: `${minutesGenProgress}%` }}
                      />
                    </div>
                    <span className="text-[11px] font-medium text-violet-600 dark:text-violet-400 tabular-nums w-9 text-right">
                      {minutesGenProgress}%
                    </span>
                  </div>
                </div>
              )}

            <div className="flex-1 min-h-0 overflow-hidden">
              <QuillEditor
                  value={minutesContent}
                  onChange={setMinutesContent}
                  placeholder="请输入会议纪要内容，或在录音完成后点击「生成会议纪要」自动整理…"
                  isDark={isDark}
                  fillHeight
                />
              </div>
            </div>
          )}

          {/* Tab: 会议任务 — 首次 ensure 后保持挂载，切走时用 hidden，避免反复重载整页计划 */}
          {activeTab === 'tasks' && (
            <div className="flex-1 min-h-0 overflow-y-auto p-5">
              {actionItems.length === 0 ? (
                <div className="flex flex-col items-center py-12 text-gray-400">
                  <ListTodo size={36} className="mb-3 opacity-40" />
                  <p className="text-sm">暂无会议任务</p>
                  <p className="text-xs mt-1 text-gray-400 dark:text-gray-500">可在「会议纪要」Tab 中点击「生成任务清单」按钮，从纪要提取任务并确认写入</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {actionItems.map((item) => {
                    const cfg = ACTION_STATUS_CONFIG[item.status] || ACTION_STATUS_CONFIG.Open;
                    const pr = PRIORITY_CONFIG[item.priority] || PRIORITY_CONFIG.Medium;
                    return (
                      <div key={item.id} className="rounded-xl border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800 px-4 py-3 flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm text-gray-800 dark:text-gray-200 flex items-center gap-2">
                            <span className={cfg.color}>{cfg.icon}</span>
                            {item.description}
                          </p>
                          <p className="text-xs text-gray-400 mt-1 flex items-center gap-3 flex-wrap">
                            {item.dueDate && <span>截止：{item.dueDate.slice(0, 10)}</span>}
                            <span className={`px-1.5 py-0.5 rounded ${pr.color}`}>{pr.label}</span>
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Tab: CCB 投票/决议 — CCB 会议专属结构化能力（参会人投票 + 评审结论） */}
          {activeTab === 'ccb' && (
            <div className="flex-1 min-h-0 overflow-y-auto p-5 space-y-6">
              {/* 投票汇总卡片 */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {VOTE_OPTIONS.map(opt => {
                  const count = votes.filter(v => v.vote === opt.value).length;
                  return (
                    <div key={opt.value} className={`rounded-lg p-3 text-center ${opt.color}`}>
                      <p className="text-2xl font-bold">{count}</p>
                      <p className="text-xs mt-0.5">{opt.label}</p>
                    </div>
                  );
                })}
                <div className="rounded-lg p-3 text-center bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                  <p className="text-2xl font-bold">{votes.filter(v => !v.vote).length}</p>
                  <p className="text-xs mt-0.5">待投票</p>
                </div>
              </div>

              {/* 参会人投票列表 */}
              <div className="rounded-xl border border-gray-100 dark:border-gray-700 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-700">
                  <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-2">
                    <Users size={14} /> 参会人投票 ({votes.length})
                  </h3>
                  <button
                    onClick={() => { setShowAddVote(v => !v); }}
                    className="flex items-center gap-1 px-2.5 py-1 text-xs text-sky-600 dark:text-sky-400 border border-sky-200 dark:border-sky-700 rounded-lg hover:bg-sky-50 dark:hover:bg-sky-900/20"
                  >
                    <Plus size={12} /> 添加参会人
                  </button>
                </div>

                {showAddVote && (
                  <div className="px-4 py-3 bg-sky-50 dark:bg-sky-900/10 border-b border-sky-100 dark:border-sky-800/30">
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-2">
                      <div>
                        <label className={labelCls}>参会人</label>
                        <select className={inputCls} value={voteForm.userId} onChange={e => setVoteForm(f => ({ ...f, userId: e.target.value }))}>
                          <option value="">不指定具体用户（角色占位）</option>
                          {allUsers.map(u => {
                            const uid = String(u.id);
                            const display = u.realName || u.real_name || u.name || u.email || uid;
                            return <option key={uid} value={uid}>{display}</option>;
                          })}
                        </select>
                      </div>
                      <div>
                        <label className={labelCls}>角色 *</label>
                        <input type="text" className={inputCls} value={voteForm.role}
                          onChange={e => setVoteForm(f => ({ ...f, role: e.target.value }))}
                          placeholder="如 chairperson / quality_manager / 工程经理" />
                      </div>
                      <div>
                        <label className={labelCls}>出席状态</label>
                        <select className={inputCls} value={voteForm.attendance}
                          onChange={e => setVoteForm(f => ({ ...f, attendance: e.target.value }))}>
                          {ATTENDANCE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      </div>
                    </div>
                    <div className="flex justify-end gap-2">
                      <button onClick={() => setShowAddVote(false)} className="px-3 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600">取消</button>
                      <button onClick={handleAddVote} disabled={voteSaving}
                        className="px-3 py-1.5 text-xs rounded-lg bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-60 flex items-center gap-1">
                        {voteSaving ? <RefreshCw size={12} className="animate-spin" /> : <Plus size={12} />} 添加
                      </button>
                    </div>
                  </div>
                )}

                {votesLoading ? (
                  <div className="flex items-center justify-center py-8 text-gray-400">
                    <RefreshCw size={18} className="animate-spin mr-2" /> 加载中...
                  </div>
                ) : votes.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-8">暂无参会人，点击「添加参会人」开始记录投票</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className={`text-xs font-medium uppercase tracking-wide ${isDark ? 'bg-gray-700/50 text-gray-400' : 'bg-gray-50 text-gray-500'}`}>
                          <th className="px-3 py-2 text-left">参会人</th>
                          <th className="px-3 py-2 text-left">角色</th>
                          <th className="px-3 py-2 text-left">出席状态</th>
                          <th className="px-3 py-2 text-left">投票</th>
                          <th className="px-3 py-2 text-left">评论</th>
                          <th className="px-3 py-2 text-right">操作</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50 dark:divide-gray-700">
                        {votes.map(v => {
                          const vCfg = getVoteConfig(v.vote);
                          return (
                            <tr key={v.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/40">
                              <td className="px-3 py-2 text-gray-700 dark:text-gray-200">{getUserLabel(v.userId)}</td>
                              <td className="px-3 py-2 text-gray-700 dark:text-gray-200">{v.role}</td>
                              <td className="px-3 py-2">
                                <select
                                  value={v.attendance}
                                  onChange={e => handleUpdateVote(v.id, { attendance: e.target.value })}
                                  className={`text-xs px-2 py-1 rounded border ${isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-1 focus:ring-sky-500`}
                                >
                                  {ATTENDANCE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                </select>
                              </td>
                              <td className="px-3 py-2">
                                <select
                                  value={v.vote || ''}
                                  onChange={e => handleUpdateVote(v.id, { vote: e.target.value || null })}
                                  className={`text-xs px-2 py-1 rounded border ${isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-1 focus:ring-sky-500`}
                                >
                                  <option value="">未投票</option>
                                  {VOTE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                </select>
                                <span className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full ${vCfg.color}`}>{vCfg.label}</span>
                              </td>
                              <td className="px-3 py-2 min-w-[200px]">
                                <input
                                  type="text"
                                  defaultValue={v.voteComment ?? ''}
                                  onBlur={e => {
                                    const val = e.target.value.trim();
                                    if (val !== (v.voteComment ?? '')) handleUpdateVote(v.id, { vote_comment: val || null });
                                  }}
                                  placeholder="投票评论..."
                                  className={`w-full text-xs px-2 py-1 rounded border ${isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-1 focus:ring-sky-500`}
                                />
                              </td>
                              <td className="px-3 py-2 text-right">
                                <button onClick={() => handleDeleteVote(v.id)}
                                  className="p-1 text-gray-300 hover:text-red-500 dark:hover:text-red-400 rounded transition-colors" title="移除">
                                  <Trash2 size={13} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* 结构化决议列表 */}
              <div className="rounded-xl border border-gray-100 dark:border-gray-700 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-700">
                  <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-2">
                    <ListChecks size={14} /> 评审决议 ({decisions.length})
                  </h3>
                  <button
                    onClick={() => { setShowAddDecision(v => !v); }}
                    className="flex items-center gap-1 px-2.5 py-1 text-xs text-sky-600 dark:text-sky-400 border border-sky-200 dark:border-sky-700 rounded-lg hover:bg-sky-50 dark:hover:bg-sky-900/20"
                  >
                    <Plus size={12} /> 添加决议
                  </button>
                </div>

                {showAddDecision && (
                  <div className="px-4 py-3 bg-sky-50 dark:bg-sky-900/10 border-b border-sky-100 dark:border-sky-800/30 space-y-2">
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                      <div>
                        <label className={labelCls}>决议类型 *</label>
                        <select className={inputCls} value={decisionForm.decisionType}
                          onChange={e => setDecisionForm(f => ({ ...f, decisionType: e.target.value }))}>
                          {DECISION_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className={labelCls}>行动责任人</label>
                        <select className={inputCls} value={decisionForm.actionAssigneeId}
                          onChange={e => setDecisionForm(f => ({ ...f, actionAssigneeId: e.target.value }))}>
                          <option value="">不指定</option>
                          {allUsers.map(u => {
                            const uid = String(u.id);
                            const display = u.realName || u.real_name || u.name || u.email || uid;
                            return <option key={uid} value={uid}>{display}</option>;
                          })}
                        </select>
                      </div>
                      <div>
                        <label className={labelCls}>行动截止日期</label>
                        <input type="date" className={inputCls} value={decisionForm.actionDueDate}
                          onChange={e => setDecisionForm(f => ({ ...f, actionDueDate: e.target.value }))} />
                      </div>
                    </div>
                    <div>
                      <label className={labelCls}>条件</label>
                      <input type="text" className={inputCls} value={decisionForm.conditions}
                        onChange={e => setDecisionForm(f => ({ ...f, conditions: e.target.value }))}
                        placeholder="批准条件（如有）" />
                    </div>
                    <div>
                      <label className={labelCls}>行动项摘要</label>
                      <input type="text" className={inputCls} value={decisionForm.actionItem}
                        onChange={e => setDecisionForm(f => ({ ...f, actionItem: e.target.value }))}
                        placeholder="决议附带的行动项摘要" />
                    </div>
                    <div>
                      <label className={labelCls}>行动项状态</label>
                      <select className={inputCls} value={decisionForm.actionStatus}
                        onChange={e => setDecisionForm(f => ({ ...f, actionStatus: e.target.value }))}>
                        {ACTION_STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </div>
                    <div className="flex justify-end gap-2 pt-1">
                      <button onClick={() => setShowAddDecision(false)} className="px-3 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600">取消</button>
                      <button onClick={handleAddDecision} disabled={decisionSaving}
                        className="px-3 py-1.5 text-xs rounded-lg bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-60 flex items-center gap-1">
                        {decisionSaving ? <RefreshCw size={12} className="animate-spin" /> : <Plus size={12} />} 添加
                      </button>
                    </div>
                  </div>
                )}

                {decisionsLoading ? (
                  <div className="flex items-center justify-center py-8 text-gray-400">
                    <RefreshCw size={18} className="animate-spin mr-2" /> 加载中...
                  </div>
                ) : decisions.length === 0 ? (
                  <p className="text-sm text-gray-400 text-center py-8">暂无决议，点击「添加决议」记录评审结论</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className={`text-xs font-medium uppercase tracking-wide ${isDark ? 'bg-gray-700/50 text-gray-400' : 'bg-gray-50 text-gray-500'}`}>
                          <th className="px-3 py-2 text-left">决议类型</th>
                          <th className="px-3 py-2 text-left">条件</th>
                          <th className="px-3 py-2 text-left">行动项</th>
                          <th className="px-3 py-2 text-left">责任人</th>
                          <th className="px-3 py-2 text-left">截止日期</th>
                          <th className="px-3 py-2 text-left">状态</th>
                          <th className="px-3 py-2 text-right">操作</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-50 dark:divide-gray-700">
                        {decisions.map(d => {
                          const dCfg = getDecisionConfig(d.decisionType);
                          return (
                            <tr key={d.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/40 align-top">
                              <td className="px-3 py-2">
                                <select
                                  value={d.decisionType}
                                  onChange={e => handleUpdateDecision(d.id, { decisionType: e.target.value })}
                                  className={`text-xs px-2 py-1 rounded border ${isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-1 focus:ring-sky-500`}
                                >
                                  {DECISION_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                </select>
                                <span className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full ${dCfg.color}`}>{dCfg.label}</span>
                              </td>
                              <td className="px-3 py-2 min-w-[180px]">
                                <input
                                  type="text"
                                  defaultValue={d.conditions ?? ''}
                                  onBlur={e => {
                                    const val = e.target.value.trim();
                                    if (val !== (d.conditions ?? '')) handleUpdateDecision(d.id, { conditions: val || null });
                                  }}
                                  placeholder="无"
                                  className={`w-full text-xs px-2 py-1 rounded border ${isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-1 focus:ring-sky-500`}
                                />
                              </td>
                              <td className="px-3 py-2 min-w-[200px]">
                                <input
                                  type="text"
                                  defaultValue={d.actionItem ?? ''}
                                  onBlur={e => {
                                    const val = e.target.value.trim();
                                    if (val !== (d.actionItem ?? '')) handleUpdateDecision(d.id, { actionItem: val || null });
                                  }}
                                  placeholder="无"
                                  className={`w-full text-xs px-2 py-1 rounded border ${isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-1 focus:ring-sky-500`}
                                />
                              </td>
                              <td className="px-3 py-2 text-gray-700 dark:text-gray-200">{getUserLabel(d.actionAssigneeId)}</td>
                              <td className="px-3 py-2 text-gray-700 dark:text-gray-200 whitespace-nowrap">{fmtDate(d.actionDueDate)}</td>
                              <td className="px-3 py-2">
                                <select
                                  value={d.actionStatus ?? ''}
                                  onChange={e => handleUpdateDecision(d.id, { actionStatus: e.target.value || null })}
                                  className={`text-xs px-2 py-1 rounded border ${isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-1 focus:ring-sky-500`}
                                >
                                  <option value="">无</option>
                                  {ACTION_STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                </select>
                              </td>
                              <td className="px-3 py-2 text-right">
                                <button onClick={() => handleDeleteDecision(d.id)}
                                  className="p-1 text-gray-300 hover:text-red-500 dark:hover:text-red-400 rounded transition-colors" title="删除">
                                  <Trash2 size={13} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

      {/* ── CC 抄送 Modal ── */}
      {showCCModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className={`w-full max-w-md rounded-2xl shadow-xl ${isDark ? 'bg-gray-800' : 'bg-white'}`}>
            <div className={`flex items-center justify-between px-5 py-4 border-b ${isDark ? 'border-gray-700' : 'border-gray-200'}`}>
              <div className="flex items-center gap-2">
                <Send size={16} className="text-orange-500" />
                <h3 className={`text-sm font-semibold ${isDark ? 'text-white' : 'text-gray-900'}`}>抄送会议纪要</h3>
              </div>
              <button onClick={() => setShowCCModal(false)} className="p-1 text-gray-400 hover:text-gray-600 rounded transition-colors"><X size={16} /></button>
            </div>
            <div className="p-5 space-y-4">
              {/* 搜索用户 */}
              <div>
                <label className={`block text-xs font-medium mb-1.5 ${isDark ? 'text-gray-300' : 'text-gray-600'}`}>选择抄送人员</label>
                <div className="relative mb-2">
                  <Users size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                  <input
                    value={ccSearch}
                    onChange={e => setCCSearch(e.target.value)}
                    placeholder="搜索姓名..."
                    className={`w-full pl-8 pr-3 py-2 text-sm rounded-lg border ${isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-300'} focus:outline-none focus:ring-2 focus:ring-orange-500`}
                  />
                </div>
                <div className={`max-h-48 overflow-y-auto rounded-lg border space-y-0.5 p-1 ${isDark ? 'border-gray-700 bg-gray-750' : 'border-gray-200 bg-gray-50'}`}>
                  {allUsers.length === 0 ? (
                    <p className="text-xs text-center py-4 text-gray-400">加载用户中...</p>
                  ) : (
                    allUsers
                      .filter(u => {
                        if (!ccSearch) return true;
                        const q = ccSearch.toLowerCase();
                        return (u.realName || u.real_name || u.name || '').toLowerCase().includes(q) ||
                               (u.email || '').toLowerCase().includes(q);
                      })
                      .map(u => {
                        const uid = u.id;
                        const display = u.realName || u.real_name || u.name || u.email || uid;
                        const selected = ccUserIds.includes(uid);
                        return (
                          <button
                            key={uid}
                            onClick={() => setCCUserIds(prev => selected ? prev.filter(id => id !== uid) : [...prev, uid])}
                            className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                              selected
                                ? isDark ? 'bg-orange-900/30 text-orange-300' : 'bg-orange-50 text-orange-700'
                                : isDark ? 'hover:bg-gray-700 text-gray-300' : 'hover:bg-gray-100 text-gray-700'
                            }`}
                          >
                            <div className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${selected ? 'bg-orange-500 border-orange-500' : isDark ? 'border-gray-500' : 'border-gray-300'}`}>
                              {selected && <span className="text-white text-[10px] font-bold">✓</span>}
                            </div>
                            <span className="truncate">{display}</span>
                            {u.email && <span className="text-xs text-gray-400 truncate ml-auto">{u.email}</span>}
                          </button>
                        );
                      })
                  )}
                </div>
                {ccUserIds.length > 0 && (
                  <p className={`text-xs mt-1 ${isDark ? 'text-orange-400' : 'text-orange-600'}`}>
                    已选 {ccUserIds.length} 位
                  </p>
                )}
              </div>
              {/* 附言 */}
              <div>
                <label className={`block text-xs font-medium mb-1.5 ${isDark ? 'text-gray-300' : 'text-gray-600'}`}>附言（可选）</label>
                <textarea
                  value={ccMessage}
                  onChange={e => setCCMessage(e.target.value)}
                  rows={2}
                  placeholder="添加附言..."
                  className={`w-full px-3 py-2 text-sm rounded-lg border resize-none ${isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-300'} focus:outline-none focus:ring-2 focus:ring-orange-500`}
                />
              </div>
            </div>
            <div className={`flex items-center justify-end gap-3 px-5 py-4 border-t ${isDark ? 'border-gray-700' : 'border-gray-200'}`}>
              <button
                onClick={() => setShowCCModal(false)}
                className={`px-4 py-2 text-sm rounded-lg border transition-colors ${isDark ? 'border-gray-600 text-gray-300 hover:bg-gray-700' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}
              >
                取消
              </button>
              <button
                onClick={handleSendCC}
                disabled={ccUserIds.length === 0 || ccSending}
                className="flex items-center gap-1.5 px-4 py-2 text-sm bg-orange-600 text-white rounded-lg hover:bg-orange-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Send size={14} />{ccSending ? '发送中...' : `发送抄送（${ccUserIds.length}人）`}
              </button>
            </div>
          </div>
        </div>
      )}

      <MeetingTaskConfirmModal
        open={taskConfirmOpen}
        isDark={isDark}
        items={taskCandidates}
        users={allUsers}
        attendeesIds={selectedMeeting?.attendeesIds || []}
        submitting={confirmingTasks}
        onClose={() => {
          if (!confirmingTasks) {
            setTaskConfirmOpen(false);
            setTaskCandidates([]);
          }
        }}
        onConfirm={handleConfirmMeetingTasks}
      />
    </div>
    );
  }

  // ─────────────────── LIST VIEW ───────────────────
  return (
    <div className={`space-y-4 ${fullscreenShellCls}`}>
      {/* Header */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm px-5 py-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-sky-500 to-sky-600 flex items-center justify-center">
            <Calendar size={17} className="text-white" />
          </div>
          <div>
            <h2 className="text-base font-bold text-gray-900 dark:text-white">会议管理</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              共 {filteredMeetings.length} 次会议
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {toggleFullscreenBtn}
          <button onClick={loadMeetings} className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors" title="刷新">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <div className="relative">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input type="text" value={searchText} onChange={e => setSearchText(e.target.value)} placeholder="搜索会议标题..."
              className={`pl-9 pr-3 py-2 rounded-lg border text-sm w-52 ${isDark ? 'bg-gray-700 border-gray-600 text-white placeholder-gray-400' : 'bg-white border-gray-200'} focus:outline-none focus:ring-2 focus:ring-sky-500`} />
          </div>
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
            className={`px-3 py-2 rounded-lg border text-sm ${isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-2 focus:ring-sky-500`}>
            <option value="all">全部状态</option>
            {Object.entries(MEETING_STATUS_CONFIG).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
            className={`px-3 py-2 rounded-lg border text-sm ${isDark ? 'bg-gray-700 border-gray-600 text-white' : 'bg-white border-gray-200 text-gray-700'} focus:outline-none focus:ring-2 focus:ring-sky-500`}>
            <option value="all">全部类型</option>
            {listTypeOptions.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={openTemplateManager}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg border transition-colors ${
              isDark ? 'border-gray-600 text-gray-200 hover:bg-gray-700' : 'border-gray-200 text-gray-700 hover:bg-gray-50'
            }`}>
            <FileText size={15} /> 模板管理
          </button>
          <button onClick={openCreateModal}
            className="flex items-center gap-1.5 px-4 py-2 bg-sky-600 text-white text-sm font-medium rounded-lg hover:bg-sky-700 transition-colors shadow-sm">
            <Plus size={15} /> 新建会议
          </button>
        </div>
      </div>

      {/* Meeting table */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <RefreshCw size={22} className="animate-spin mr-2" /> 加载中...
          </div>
        ) : filteredMeetings.length === 0 ? (
          <div className="flex flex-col items-center py-16 text-gray-400">
            <Calendar size={40} className="mb-3 opacity-40" />
            <p className="text-sm font-medium">{meetings.length === 0 ? '暂无会议记录' : '没有匹配的会议'}</p>
            <p className="text-xs mt-1">{meetings.length === 0 ? '点击"新建会议"按钮创建第一次会议' : '请尝试调整搜索和筛选条件'}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className={`text-xs font-medium uppercase tracking-wide ${isDark ? 'bg-gray-700/50 text-gray-400' : 'bg-gray-50 text-gray-500'}`}>
                  <th className="px-4 py-3 text-left">会议标题</th>
                  <th className="px-4 py-3 text-left">会议类型</th>
                  <th className="px-4 py-3 text-left">状态</th>
                  <th className="px-4 py-3 text-left">召开时间</th>
                  <th className="px-4 py-3 text-left">地点/方式</th>
                  <th className="px-4 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50 dark:divide-gray-700">
                {filteredMeetings.map(meeting => {
                  const mCfg = MEETING_STATUS_CONFIG[meeting.status] ?? { label: meeting.status, color: 'bg-gray-100 text-gray-600' };
                  return (
                    <tr key={meeting.id} onClick={() => openMeetingDetail(meeting)}
                      className="hover:bg-gray-50 dark:hover:bg-gray-700/40 transition-colors cursor-pointer">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="w-7 h-7 rounded-lg bg-sky-100 dark:bg-sky-900/30 flex items-center justify-center flex-shrink-0">
                            <Calendar size={13} className="text-sky-600 dark:text-sky-400" />
                          </div>
                          <span className="font-medium text-gray-900 dark:text-white truncate max-w-xs">{meeting.title}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400">{getMeetingTypeLabel(meeting.meetingType)}</td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${mCfg.color}`}>{mCfg.label}</span>
                      </td>
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400 whitespace-nowrap">{fmtDateTime(meeting.scheduledStart)}</td>
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400">
                        {meeting.location ? (
                          <span className="flex items-center gap-1"><MapPin size={12} />{meeting.location}</span>
                        ) : meeting.onlineLink ? (
                          <span className="flex items-center gap-1 text-sky-500"><Link2 size={12} />在线会议</span>
                        ) : '-'}
                      </td>
                      <td className="px-4 py-3 text-right" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1">
                          <button onClick={() => openMeetingDetail(meeting)}
                            className="px-2.5 py-1 text-xs text-sky-600 dark:text-sky-400 border border-sky-200 dark:border-sky-700 rounded-lg hover:bg-sky-50 dark:hover:bg-sky-900/20 transition-colors">
                            查看
                          </button>
                          <button onClick={() => handleDeleteMeeting(meeting.id)}
                            className="p-1.5 text-gray-300 hover:text-red-500 dark:hover:text-red-400 rounded-lg transition-colors" title="删除">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create Meeting Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className={`w-full max-w-xl rounded-2xl shadow-2xl ${isDark ? 'bg-gray-800 border border-gray-700' : 'bg-white'}`}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-700">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-sky-500 to-sky-600 flex items-center justify-center">
                  <Calendar size={15} className="text-white" />
                </div>
                <h3 className="text-base font-bold text-gray-900 dark:text-white">新建会议</h3>
              </div>
              <button onClick={() => setShowCreateModal(false)} className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
                <X size={18} />
              </button>
            </div>
            <div className="px-6 py-5 space-y-4 max-h-[65vh] overflow-y-auto">
              <div>
                <label className={labelCls}>会议标题 <span className="text-red-500">*</span></label>
                <input type="text" className={inputCls} value={createForm.title}
                  onChange={e => setCreateForm(f => ({ ...f, title: e.target.value }))} placeholder="请输入会议标题" autoFocus />
              </div>
              <div>
                <label className={labelCls}>
                  会议类型
                  {createForm.meetingType && <span className="ml-2 text-sky-500 text-xs font-normal">（已自动载入会议模板）</span>}
                </label>
                <select className={inputCls} value={createForm.meetingType} onChange={e => handleTypeChange(e.target.value)}>
                  <option value="">请选择会议类型</option>
                  {meetingTypes.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className={labelCls}>会议模板</label>
                  <button type="button" onClick={openTemplateManager} className="text-xs text-sky-600 dark:text-sky-400 hover:underline">
                    管理模板
                  </button>
                </div>
                <select className={inputCls} value={createForm.templateId} onChange={e => handleTemplateChange(e.target.value)}>
                  <option value="">不使用会议模板</option>
                  {createModalTemplates.map(template => (
                    <option key={template.id} value={template.id}>
                      {template.name}{template.isSystem ? '（系统）' : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>开始时间 <span className="text-red-500">*</span></label>
                  <input type="datetime-local" className={inputCls} value={createForm.scheduledStart}
                    onChange={e => setCreateForm(f => ({ ...f, scheduledStart: e.target.value }))} />
                </div>
                <div>
                  <label className={labelCls}>结束时间</label>
                  <input type="datetime-local" className={inputCls} value={createForm.scheduledEnd}
                    onChange={e => setCreateForm(f => ({ ...f, scheduledEnd: e.target.value }))} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>会议地点</label>
                  <input type="text" className={inputCls} value={createForm.location}
                    onChange={e => setCreateForm(f => ({ ...f, location: e.target.value }))} placeholder="会议室或地址" />
                </div>
                <div>
                  <label className={labelCls}>在线链接</label>
                  <input type="url" className={inputCls} value={createForm.onlineLink}
                    onChange={e => setCreateForm(f => ({ ...f, onlineLink: e.target.value }))} placeholder="https://..." />
                </div>
              </div>
              <div>
                <label className={labelCls}>
                  会议议程
                  {(createForm.templateId || createForm.meetingType) && <span className="ml-1 text-sky-500 text-xs">（已自动填充模板，可直接编辑）</span>}
                </label>
                <QuillEditor
                  value={createForm.agenda}
                  onChange={val => setCreateForm(f => ({ ...f, agenda: val }))}
                  placeholder="输入会议议程..."
                  isDark={isDark}
                  minHeight="160px"
                />
              </div>
              {!createForm.meetingType && meetingTypes.length > 0 && (
                <div className={`flex items-start gap-2 p-3 rounded-lg text-xs ${isDark ? 'bg-sky-900/20 border border-sky-800 text-sky-300' : 'bg-sky-50 border border-sky-100 text-sky-700'}`}>
                  <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
                  <span>选择会议类型后，系统将自动载入对应的会议模板到议程中。</span>
                </div>
              )}
            </div>
            <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-100 dark:border-gray-700">
              <button onClick={() => setShowCreateModal(false)} className="px-4 py-2 text-sm text-gray-500 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">
                取消
              </button>
              <button onClick={handleCreate} disabled={creating || !createForm.title.trim() || !createForm.scheduledStart}
                className="flex items-center gap-1.5 px-5 py-2 text-sm bg-sky-600 text-white font-medium rounded-lg hover:bg-sky-700 transition-colors shadow-sm disabled:opacity-60">
                {creating ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
                {creating ? '创建中...' : '创建会议'}
              </button>
            </div>
          </div>
        </div>
      )}

      <MeetingTemplateManager
        show={showTemplateManager}
        onClose={() => setShowTemplateManager(false)}
        isDark={isDark}
        onChanged={loadMeetingTemplates}
      />
    </div>
  );
};

export default MeetingsPage;
