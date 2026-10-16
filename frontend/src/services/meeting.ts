/**
 * 会议服务 API（独立应用版，自 AIIgnitePLM 移植）
 *
 * 路径去项目化：/meetings、/meetings/{id}/recordings 等。
 * 后端返回 snake_case，client.ts 自动转 camelCase。
 */
import { api, buildQuery } from './client';

export type MeetingRecordingStatus = 'Uploaded' | 'Transcribing' | 'Transcribed' | 'Refining' | 'Failed';

export interface MeetingRecording {
  id: string;
  meetingId: string;
  uploaderId: string;
  fileName: string;
  fileSize: number;
  durationSeconds: number | null;
  mimeType: string | null;
  storagePath: string;
  status: MeetingRecordingStatus;
  progressPct: number;
  language: string | null;
  modelSize: string | null;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MeetingTranscriptSegment {
  id: string;
  segmentIndex: number;
  startTime: number;
  endTime: number;
  speakerLabel: string | null;
  text: string;
  confidence: number | null;
  language: string | null;
  chapterId: number | null;
  chapterTitle: string | null;
  keywords: string[] | null;
}

export interface MeetingTranscript {
  recordingId: string;
  status: MeetingRecordingStatus;
  progressPct: number;
  segments: MeetingTranscriptSegment[];
}

/** 会议列表过滤参数 */
export interface MeetingListFilter {
  activeOnly?: boolean;
  keyword?: string;
}

/** 会议参会人投票（对应 mtg_meeting_votes 表）。 */
export interface MeetingVote {
  id: string;
  meetingId: string;
  userId: string | null;
  role: string;
  attendance: string;
  vote: string | null;
  voteComment: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MeetingVoteCreatePayload {
  user_id?: string | null;
  role: string;
  attendance?: string;
  vote?: string | null;
  vote_comment?: string | null;
}

export interface MeetingVoteUpdatePayload {
  attendance?: string | null;
  vote?: string | null;
  vote_comment?: string | null;
}

/** 会议结构化决议（对应 mtg_meeting_decisions 表）。 */
export interface MeetingDecision {
  id: string;
  meetingId: string;
  decisionType: string;
  conditions: string | null;
  actionItem: string | null;
  actionAssigneeId: string | null;
  actionDueDate: string | null;
  actionStatus: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MeetingDecisionCreatePayload {
  decision_type: string;
  conditions?: string | null;
  action_item?: string | null;
  action_assignee_id?: string | null;
  action_due_date?: string | null;
  action_status?: string | null;
}

export interface MeetingDecisionUpdatePayload {
  decision_type?: string | null;
  conditions?: string | null;
  action_item?: string | null;
  action_assignee_id?: string | null;
  action_due_date?: string | null;
  action_status?: string | null;
}

type RawVote = Record<string, unknown>;
type RawDecision = Record<string, unknown>;

const mapVoteOut = (v: RawVote): MeetingVote => ({
  id: String(v.id),
  meetingId: String(v.meetingId ?? v.meeting_id ?? ''),
  userId: v.userId != null ? String(v.userId) : (v.user_id != null ? String(v.user_id) : null),
  role: String(v.role ?? ''),
  attendance: String(v.attendance ?? 'Invited'),
  vote: v.vote != null ? String(v.vote) : null,
  voteComment: v.voteComment != null ? String(v.voteComment) : (v.vote_comment != null ? String(v.vote_comment) : null),
  createdAt: String(v.createdAt ?? v.created_at ?? ''),
  updatedAt: String(v.updatedAt ?? v.updated_at ?? ''),
});

const mapDecisionOut = (d: RawDecision): MeetingDecision => ({
  id: String(d.id),
  meetingId: String(d.meetingId ?? d.meeting_id ?? ''),
  decisionType: String(d.decisionType ?? d.decision_type ?? ''),
  conditions: d.conditions != null ? String(d.conditions) : null,
  actionItem: d.actionItem != null ? String(d.actionItem) : (d.action_item != null ? String(d.action_item) : null),
  actionAssigneeId: d.actionAssigneeId != null ? String(d.actionAssigneeId) : (d.action_assignee_id != null ? String(d.action_assignee_id) : null),
  actionDueDate: d.actionDueDate != null ? String(d.actionDueDate) : (d.action_due_date != null ? String(d.action_due_date) : null),
  actionStatus: d.actionStatus != null ? String(d.actionStatus) : (d.action_status != null ? String(d.action_status) : null),
  createdAt: String(d.createdAt ?? d.created_at ?? ''),
  updatedAt: String(d.updatedAt ?? d.updated_at ?? ''),
});

/** ─── Meetings endpoints ─── */
export const meetingsApi = {
  /** 列出全部会议（支持 active_only 与关键词过滤）。 */
  list: (filter?: MeetingListFilter) =>
    api.get<any[]>(
      `/meetings${buildQuery(filter ? {
        active_only: filter.activeOnly,
        keyword: filter.keyword,
      } : undefined)}`,
    ),
  get: (meetingId: string) => api.get<any>(`/meetings/${meetingId}`),
  create: (data: any) => api.post<any>('/meetings', data),
  update: (meetingId: string, data: any) => api.patch<any>(`/meetings/${meetingId}`, data),
  delete: (meetingId: string) => api.delete(`/meetings/${meetingId}`),
  // Minutes
  listMinutes: (meetingId: string) =>
    api.get<any[]>(`/meetings/${meetingId}/minutes`),
  createMinutes: (meetingId: string, data: any) =>
    api.post<any>(`/meetings/${meetingId}/minutes`, data),
  updateMinutes: (meetingId: string, minutesId: string, data: any) =>
    api.patch<any & { action_dispatch?: unknown }>(
      `/meetings/${meetingId}/minutes/${minutesId}`,
      data
    ),
  /** 从会议转写生成纪要并自动落库。默认合并全部已转写录音。 */
  generateMinutesFromTranscript: (
    meetingId: string,
    options?: {
      recordingId?: string;
      mergeAllRecordings?: boolean;
      provider?: string;
      model?: string;
      baseContent?: string;
    } | string,
  ) => {
    const path = `/meetings/${meetingId}/minutes:generate-from-transcript`;
    // 兼容旧调用：第二个参数为 recordingId 字符串
    const opts =
      typeof options === 'string'
        ? { recordingId: options, mergeAllRecordings: false }
        : options || {};
    const body = {
      recording_id: opts.recordingId || null,
      merge_all_recordings: opts.mergeAllRecordings !== false,
      provider: opts.provider || null,
      model: opts.model || null,
      base_content: opts.baseContent || null,
    };
    const MINUTES_GENERATE_TIMEOUT_MS = 300_000;
    return api.post<{
      draft: Record<string, unknown>;
      summaryHtml: string;
      summaryMarkdown: string;
      minutesId?: string;
      savedContent?: string;
      modelUsed?: string | null;
      contextSummary?: Record<string, unknown> | null;
    }>(path, body, MINUTES_GENERATE_TIMEOUT_MS);
  },
  extractTasksFromMinutes: (
    meetingId: string,
    options?: { model?: string; provider?: string },
  ) =>
    api.post<{
      items: Array<{
        name: string;
        responsible?: string | null;
        responsibleUserId?: string | null;
        plannedStart?: string | null;
        plannedEnd?: string | null;
        priority?: string | null;
        description?: string | null;
      }>;
      modelUsed?: string | null;
    }>(
      `/meetings/${meetingId}/tasks:extract-from-minutes`,
      {
        provider: options?.provider || null,
        model: options?.model || null,
      },
      180_000,
    ),
  confirmMeetingTasks: (
    meetingId: string,
    data: {
      items: Array<{
        name: string;
        responsibleUserId?: string | null;
        responsible?: string | null;
        plannedStart?: string | null;
        plannedEnd?: string | null;
        priority?: string | null;
        description?: string | null;
      }>;
      publish: boolean;
    },
  ) =>
    api.post<{
      created: number;
      published?: number;
      taskInfoId?: string | null;
      itemIds?: string[];
    }>(`/meetings/${meetingId}/tasks:confirm`, {
      items: data.items.map((it) => ({
        name: it.name,
        responsible_user_id: it.responsibleUserId || null,
        responsible: it.responsible || null,
        planned_start: it.plannedStart || null,
        planned_end: it.plannedEnd || null,
        priority: it.priority || 'Medium',
        description: it.description || null,
      })),
      publish: data.publish,
    }),
  // Action Items
  listActionItems: (meetingId: string) =>
    api.get<any[]>(`/meetings/${meetingId}/action-items`),
  createActionItem: (meetingId: string, data: any) =>
    api.post<any>(`/meetings/${meetingId}/action-items`, data),
  updateActionItem: (meetingId: string, itemId: string, data: any) =>
    api.patch<any>(`/meetings/${meetingId}/action-items/${itemId}`, data),
  deleteActionItem: (meetingId: string, itemId: string) =>
    api.delete(`/meetings/${meetingId}/action-items/${itemId}`),
  // Invite as tasks（参会人转行动项）
  inviteTasks: (meetingId: string, data: { attendee_ids?: string[]; due_date?: string }) =>
    api.post<{ created: number; message: string }>(`/meetings/${meetingId}/invite-tasks`, data),
  // CC (抄送记录)
  cc: (meetingId: string, data: { user_ids?: string[]; names?: string[]; message?: string }) =>
    api.post<{ sent: number; message: string }>(`/meetings/${meetingId}/cc`, data),

  // ── Recordings & Transcripts ─────────────────────────────
  listRecordings: (meetingId: string, statusFilter?: string) =>
    api.get<MeetingRecording[]>(
      `/meetings/${meetingId}/recordings${statusFilter ? `?status_filter=${encodeURIComponent(statusFilter)}` : ''}`
    ),
  getRecording: (meetingId: string, recordingId: string) =>
    api.get<MeetingRecording>(`/meetings/${meetingId}/recordings/${recordingId}`),
  getTranscript: (meetingId: string, recordingId: string) =>
    api.get<MeetingTranscript>(
      `/meetings/${meetingId}/recordings/${recordingId}/transcript`
    ),
  uploadRecording: (meetingId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api.postForm<MeetingRecording>(`/meetings/${meetingId}/recordings`, form);
  },
  retranscribeRecording: (meetingId: string, recordingId: string) =>
    api.post<MeetingRecording>(
      `/meetings/${meetingId}/recordings/${recordingId}/retranscribe`,
      {}
    ),
  refineRecordingTranscript: (meetingId: string, recordingId: string) =>
    api.post<MeetingRecording>(
      `/meetings/${meetingId}/recordings/${recordingId}/refine`,
      {}
    ),
  deleteRecording: (meetingId: string, recordingId: string) =>
    api.delete(`/meetings/${meetingId}/recordings/${recordingId}`),

  // ── CCB 投票 (mtg_meeting_votes) ─────────────────────────────
  votes: {
    list: async (meetingId: string): Promise<MeetingVote[]> => {
      const raw = await api.get<RawVote[]>(`/meetings/${meetingId}/votes`);
      return (raw || []).map(mapVoteOut);
    },
    create: async (meetingId: string, data: MeetingVoteCreatePayload): Promise<MeetingVote> => {
      const raw = await api.post<RawVote>(`/meetings/${meetingId}/votes`, data);
      return mapVoteOut(raw);
    },
    update: async (meetingId: string, voteId: string, data: MeetingVoteUpdatePayload): Promise<MeetingVote> => {
      const raw = await api.patch<RawVote>(`/meetings/${meetingId}/votes/${voteId}`, data);
      return mapVoteOut(raw);
    },
    delete: (meetingId: string, voteId: string) =>
      api.delete(`/meetings/${meetingId}/votes/${voteId}`),
  },

  // ── CCB 决议 (mtg_meeting_decisions) ─────────────────────────────
  decisions: {
    list: async (meetingId: string): Promise<MeetingDecision[]> => {
      const raw = await api.get<RawDecision[]>(`/meetings/${meetingId}/decisions`);
      return (raw || []).map(mapDecisionOut);
    },
    create: async (meetingId: string, data: MeetingDecisionCreatePayload): Promise<MeetingDecision> => {
      const raw = await api.post<RawDecision>(`/meetings/${meetingId}/decisions`, data);
      return mapDecisionOut(raw);
    },
    update: async (meetingId: string, decisionId: string, data: MeetingDecisionUpdatePayload): Promise<MeetingDecision> => {
      const raw = await api.patch<RawDecision>(`/meetings/${meetingId}/decisions/${decisionId}`, data);
      return mapDecisionOut(raw);
    },
    delete: (meetingId: string, decisionId: string) =>
      api.delete(`/meetings/${meetingId}/decisions/${decisionId}`),
  },
};
