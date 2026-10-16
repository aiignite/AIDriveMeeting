import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { templatesApi, type Template } from '../services/template';
import {
  deriveMeetingTypesFromTemplates,
  getDefaultSystemTemplateByType,
  getMeetingTemplateData,
  getMeetingTypeLabel as resolveMeetingTypeLabel,
  getTemplateMeetingType,
  type MeetingTemplateData,
  type MeetingTypeOption,
} from '../utils/meetingTemplateUtils';

export function useMeetingTemplates(options: { autoLoad?: boolean } = {}) {
  const autoLoad = options.autoLoad !== false;
  const [meetingTemplates, setMeetingTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const loadedOnceRef = useRef(false);

  const loadMeetingTemplates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await templatesApi.list({ templateType: 'meeting', isActive: true, limit: 200 });
      setMeetingTemplates(list || []);
      loadedOnceRef.current = true;
    } catch (err) {
      console.error('加载会议模板失败', err);
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  const ensureMeetingTemplates = useCallback(async () => {
    if (loadedOnceRef.current || loading) return;
    await loadMeetingTemplates();
  }, [loadMeetingTemplates, loading]);

  useEffect(() => {
    if (autoLoad) void loadMeetingTemplates();
  }, [autoLoad, loadMeetingTemplates]);

  const meetingTypes = useMemo(
    () => deriveMeetingTypesFromTemplates(meetingTemplates),
    [meetingTemplates],
  );

  const getTemplateById = useCallback(
    (templateId: string | null | undefined): Template | undefined => {
      if (!templateId) return undefined;
      return meetingTemplates.find(item => item.id === templateId);
    },
    [meetingTemplates],
  );

  const getTemplateData = useCallback(
    (templateKey: string | null | undefined): MeetingTemplateData => {
      return getMeetingTemplateData(getTemplateById(templateKey));
    },
    [getTemplateById],
  );

  const getDefaultTemplateByType = useCallback(
    (meetingType: string) => getDefaultSystemTemplateByType(meetingTemplates, meetingType),
    [meetingTemplates],
  );

  const getTemplatesForType = useCallback(
    (meetingType: string) =>
      meetingTemplates.filter(t => getTemplateMeetingType(t) === meetingType),
    [meetingTemplates],
  );

  const resolveAgenda = useCallback(
    (templateKey: string | null | undefined): string => {
      return getTemplateData(templateKey).agendaHtml || '';
    },
    [getTemplateData],
  );

  const resolveMinutes = useCallback(
    (templateKey: string | null | undefined, meetingType?: string | null): string => {
      const fromKey = getTemplateData(templateKey).minutesHtml;
      if (fromKey) return fromKey;
      if (meetingType) {
        const defaultTpl = getDefaultTemplateByType(meetingType);
        return getMeetingTemplateData(defaultTpl).minutesHtml || '';
      }
      return '';
    },
    [getTemplateData, getDefaultTemplateByType],
  );

  const getMeetingTypeLabel = useCallback(
    (value: string | null | undefined) => resolveMeetingTypeLabel(meetingTypes, value),
    [meetingTypes],
  );

  return {
    meetingTemplates,
    meetingTypes,
    loading,
    error,
    loadMeetingTemplates,
    ensureMeetingTemplates,
    getTemplateById,
    getTemplateData,
    getDefaultTemplateByType,
    getTemplatesForType,
    resolveAgenda,
    resolveMinutes,
    getMeetingTypeLabel,
  };
}

export type { MeetingTemplateData, MeetingTypeOption };
