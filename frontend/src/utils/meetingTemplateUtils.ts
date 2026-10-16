/**
 * 会议模板工具 — 解析 legacy content、HTML 转换、纪要骨架生成。
 */

export interface ParsedMeetingContent {
  agenda: string;
  minutesSkeleton: string;
}

export interface MeetingTypeOption {
  value: string;
  label: string;
}

export interface MeetingTemplateData {
  meetingType?: string;
  agendaHtml?: string;
  minutesHtml?: string;
  taskTemplateId?: string;
  taskTemplateName?: string;
}

/** 从 legacy 字典 content 拆分【议程】与【纪要模板】。 */
export function parseMeetingTypeContent(content: string): ParsedMeetingContent {
  if (!content?.trim()) {
    return { agenda: '', minutesSkeleton: '' };
  }
  if (!content.includes('【议程】')) {
    return { agenda: content.trim(), minutesSkeleton: '' };
  }
  const afterAgenda = content.split('【议程】')[1] ?? '';
  if (afterAgenda.includes('【纪要模板】')) {
    const [agendaPart, minutesPart] = afterAgenda.split('【纪要模板】');
    return {
      agenda: agendaPart.trim(),
      minutesSkeleton: (minutesPart ?? '').trim(),
    };
  }
  return { agenda: afterAgenda.trim(), minutesSkeleton: '' };
}

/** 纯文本按行转 Quill 兼容 HTML。 */
export function plainTextToQuillHtml(text: string): string {
  const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
  if (lines.length === 0) return '';
  return lines.map(line => `<p>${line}</p>`).join('');
}

/** 生成与 AI 纪要五段结构对齐的 minutesHtml。 */
export function buildMinutesHtml(typeSpecific: string): string {
  const sections: Array<{ title: string; placeholder?: string }> = [
    { title: '会议概览' },
    { title: '关键讨论' },
    { title: '决议事项' },
    { title: '行动项', placeholder: '责任人 / 截止日期：' },
    { title: '风险与待办' },
  ];

  const parts: string[] = [];
  for (const section of sections) {
    parts.push(`<h2>${section.title}</h2>`);
    if (section.placeholder) {
      parts.push(`<p>${section.placeholder}</p>`);
    } else {
      parts.push('<p><br></p>');
    }
  }

  const skeleton = typeSpecific.trim();
  if (skeleton) {
    parts.push('<h2>类型专属字段</h2>');
    for (const line of skeleton.split('\n')) {
      const trimmed = line.trim();
      if (trimmed) parts.push(`<p>${trimmed}</p>`);
    }
  }
  return parts.join('');
}

export function getMeetingTemplateData(
  template: { templateData?: Record<string, unknown> | null; category?: string | null } | null | undefined,
): MeetingTemplateData {
  if (!template?.templateData || typeof template.templateData !== 'object') {
    return {};
  }
  return template.templateData as MeetingTemplateData;
}

export function getTemplateMeetingType(
  template: { templateData?: Record<string, unknown> | null; category?: string | null },
): string {
  const data = getMeetingTemplateData(template);
  return data.meetingType || template.category || '';
}

/** 从模板列表推导会议类型下拉（system 模板优先，按 type 去重）。 */
export function deriveMeetingTypesFromTemplates(
  templates: Array<{
    name: string;
    isSystem?: boolean;
    templateData?: Record<string, unknown> | null;
    category?: string | null;
  }>,
): MeetingTypeOption[] {
  const sorted = [...templates].sort((a, b) => {
    if (a.isSystem === b.isSystem) return 0;
    return a.isSystem ? -1 : 1;
  });
  const seen = new Set<string>();
  const result: MeetingTypeOption[] = [];
  for (const template of sorted) {
    const value = getTemplateMeetingType(template);
    if (!value || seen.has(value)) continue;
    seen.add(value);
    result.push({ value, label: template.name });
  }
  return result;
}

export function getDefaultSystemTemplateByType<
  T extends { id: string; isSystem?: boolean; templateData?: Record<string, unknown> | null; category?: string | null },
>(templates: T[], meetingType: string): T | undefined {
  return templates.find(
    t => t.isSystem && getTemplateMeetingType(t) === meetingType,
  );
}

export function getMeetingTypeLabel(
  meetingTypes: MeetingTypeOption[],
  value: string | null | undefined,
): string {
  if (!value) return '-';
  return meetingTypes.find(t => t.value === value)?.label ?? value;
}
