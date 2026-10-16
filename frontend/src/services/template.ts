/**
 * 会议模板服务（独立应用版）
 *
 * 后端 mtg_meeting_templates 表，路径 /meeting-templates。
 * 兼容原 tpl_templates 的 Template 形状：templateType 固定 'meeting'，
 * category = 会议类型，templateData = {meetingType, agendaHtml, minutesHtml}。
 */
import { api, buildQuery } from './client';

export interface Template {
  id: string;
  templateNumber: string;
  name: string;
  templateType: string;
  category: string | null;
  description: string | null;
  version: string;
  templateData: Record<string, unknown> | null;
  isSystem: boolean;
  isActive: boolean;
  tags: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TemplateCreate {
  templateNumber?: string;
  name: string;
  templateType: string;
  isActive?: boolean;
  category?: string;
  description?: string;
  version?: string;
  templateData?: Record<string, unknown>;
  tags?: string;
}

export interface TemplateListParams {
  templateType?: string;
  isActive?: boolean;
  limit?: number;
  keyword?: string;
}

const mapTemplate = (raw: Record<string, unknown>): Template => ({
  id: String(raw.id),
  templateNumber: String(raw.templateNumber ?? ''),
  name: String(raw.name ?? ''),
  templateType: 'meeting',
  category: (raw.meetingType ?? raw.category ?? null) as string | null,
  description: (raw.description ?? null) as string | null,
  version: String(raw.version ?? '1.0'),
  templateData: (raw.templateData ?? null) as Record<string, unknown> | null,
  isSystem: Boolean(raw.isSystem ?? false),
  isActive: raw.isActive == null ? true : Boolean(raw.isActive),
  tags: (raw.tags ?? null) as string | null,
  createdAt: String(raw.createdAt ?? ''),
  updatedAt: String(raw.updatedAt ?? ''),
});

const toCreateBody = (data: TemplateCreate) => ({
  name: data.name,
  meeting_type: data.category || null,
  description: data.description || null,
  version: data.version || '1.0',
  template_data: data.templateData || {},
  tags: data.tags || null,
});

export const templatesApi = {
  list: async (params?: TemplateListParams): Promise<Template[]> => {
    const raw = await api.get<Record<string, unknown>[]>(
      `/meeting-templates${buildQuery({ include_inactive: params?.isActive === false || undefined })}`,
    );
    let list = (raw || []).map(mapTemplate);
    if (params?.keyword) {
      const kw = params.keyword.toLowerCase();
      list = list.filter(
        (t) =>
          t.name.toLowerCase().includes(kw) ||
          (t.description || '').toLowerCase().includes(kw),
      );
    }
    if (params?.limit) list = list.slice(0, params.limit);
    return list;
  },
  get: async (id: string): Promise<Template> => {
    const raw = await api.get<Record<string, unknown>>(`/meeting-templates/${id}`);
    return mapTemplate(raw);
  },
  create: async (data: TemplateCreate): Promise<Template> => {
    const raw = await api.post<Record<string, unknown>>('/meeting-templates', toCreateBody(data));
    return mapTemplate(raw);
  },
  update: async (id: string, data: Partial<TemplateCreate> & { isActive?: boolean }): Promise<Template> => {
    const body: Record<string, unknown> = {};
    if (data.name !== undefined) body.name = data.name;
    if (data.category !== undefined) body.meeting_type = data.category;
    if (data.description !== undefined) body.description = data.description;
    if (data.version !== undefined) body.version = data.version;
    if (data.templateData !== undefined) body.template_data = data.templateData;
    if (data.isActive !== undefined) body.is_active = data.isActive;
    const raw = await api.patch<Record<string, unknown>>(`/meeting-templates/${id}`, body);
    return mapTemplate(raw);
  },
  delete: (id: string) => api.delete<void>(`/meeting-templates/${id}`),
};
