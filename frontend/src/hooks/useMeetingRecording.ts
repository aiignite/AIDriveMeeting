import { useState, useEffect, useCallback, useRef } from 'react';
import { meetingsApi, type MeetingRecording, type MeetingTranscript, type MeetingTranscriptSegment, type MeetingRecordingStatus } from '../services/meeting';
import { extractErrorMessage } from '../utils/errors';

interface UseMeetingRecordingOptions {
  /** 录音列表的刷新触发器；变化时自动重载。 */
  refreshKey?: string | number;
  /**
   * 为 false 时不在 meetingId 变化时自动拉录音/转写。
   * 详情默认落在「基本信息」Tab，可延迟到进入录音 Tab 再加载。
   */
  enabled?: boolean;
}

interface UseMeetingRecordingResult {
  recordings: MeetingRecording[];
  activeRecording: MeetingRecording | null;
  transcriptSegments: MeetingTranscriptSegment[];
  isRecordingsLoading: boolean;
  isTranscriptLoading: boolean;
  isUploading: boolean;
  retranscribingId: string | null;
  refiningId: string | null;
  error: string | null;
  loadRecordings: (meetingId: string) => Promise<void>;
  selectRecording: (rec: MeetingRecording) => Promise<void>;
  uploadRecording: (file: File | Blob, fileName?: string) => Promise<void>;
  retranscribe: (rec: MeetingRecording) => Promise<void>;
  refine: (rec: MeetingRecording) => Promise<void>;
  remove: (rec: MeetingRecording) => Promise<void>;
  setActiveRecording: (rec: MeetingRecording | null) => void;
  /** Clear all state, typically called when switching meeting. */
  reset: () => void;
}

/**
 * 录音 / 转写 / AI 草稿相关的所有副作用。
 * - 自动按 status 轮询
 * - AbortController 取消正在进行的轮询请求
 * - 切换 meeting 时重置所有状态
 * - 拆分 setState 颗粒度（recordingError / uploading / 等独立 state）
 */
export function useMeetingRecording(
  meetingId: string | null,
  options: UseMeetingRecordingOptions = {}
): UseMeetingRecordingResult {
  const [recordings, setRecordings] = useState<MeetingRecording[]>([]);
  const [activeRecording, setActiveRecording] = useState<MeetingRecording | null>(null);
  const [transcriptSegments, setTranscriptSegments] = useState<MeetingTranscriptSegment[]>([]);
  const [isRecordingsLoading, setIsRecordingsLoading] = useState(false);
  const [isTranscriptLoading, setIsTranscriptLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [retranscribingId, setRetranscribingId] = useState<string | null>(null);
  const [refiningId, setRefiningId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pollTimerRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 用 ref 跟踪 latest state，避免 effect 闭包陷阱
  const activeIdRef = useRef<string | null>(null);
  const meetingIdRef = useRef<string | null>(null);
  activeIdRef.current = activeRecording?.id ?? null;
  meetingIdRef.current = meetingId;

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const fetchTranscriptOnce = useCallback(async (recId: string) => {
    const mid = meetingIdRef.current;
    if (!mid) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setIsTranscriptLoading(true);
      const tr: MeetingTranscript = await meetingsApi.getTranscript(mid, recId);
      if (!controller.signal.aborted) {
        setTranscriptSegments(tr.segments || []);
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        // ignore — list may not be ready yet
      }
    } finally {
      if (!controller.signal.aborted) setIsTranscriptLoading(false);
    }
  }, []);

  const loadRecordings = useCallback(
    async (midArg: string) => {
      
      setIsRecordingsLoading(true);
      setError(null);
      try {
        const list = await meetingsApi.listRecordings(midArg);
        const safeList = Array.isArray(list) ? list : [];
        setRecordings(safeList);
        setActiveRecording((prev) => {
          if (prev && safeList.some((r) => r.id === prev.id)) return prev;
          return safeList[0] ?? null;
        });
      } catch (e) {
        setError(extractErrorMessage(e, '加载录音失败'));
      } finally {
        setIsRecordingsLoading(false);
      }
    },
    [],
  );

  const selectRecording = useCallback(async (rec: MeetingRecording) => {
    setActiveRecording(rec);
    setTranscriptSegments([]);
    if (rec.status === 'Transcribed') {
      await fetchTranscriptOnce(rec.id);
    }
  }, [fetchTranscriptOnce]);

  const uploadRecording = useCallback(
    async (file: File | Blob, fileName?: string) => {
      const mid = meetingId;
      if (!mid) return;
      const realFile = file instanceof File
        ? file
        : new File([file], fileName || 'recording.webm', { type: (file as Blob).type || 'audio/webm' });
      setIsUploading(true);
      setError(null);
      try {
        const rec = await meetingsApi.uploadRecording(mid, realFile);
        setRecordings((prev) => [rec, ...prev]);
        setActiveRecording(rec);
        setTranscriptSegments([]);
      } catch (e) {
        setError(extractErrorMessage(e, '上传失败'));
      } finally {
        setIsUploading(false);
      }
    },
    [meetingId]
  );

  const retranscribe = useCallback(
    async (rec: MeetingRecording) => {
      if (!meetingId) return;
      setError(null);
      setRetranscribingId(rec.id);
      try {
        const refreshed = await meetingsApi.retranscribeRecording(meetingId, rec.id);
        setRecordings((prev) => prev.map((r) => (r.id === refreshed.id ? refreshed : r)));
        setActiveRecording(refreshed);
        setTranscriptSegments([]);
      } catch (e) {
        setError(extractErrorMessage(e, '重新转写失败'));
      } finally {
        setRetranscribingId((prev) => (prev === rec.id ? null : prev));
      }
    },
    [meetingId]
  );

  const refine = useCallback(
    async (rec: MeetingRecording) => {
      if (!meetingId) return;
      setError(null);
      setRefiningId(rec.id);
      try {
        const refreshed = await meetingsApi.refineRecordingTranscript(meetingId, rec.id);
        setRecordings((prev) => prev.map((r) => (r.id === refreshed.id ? refreshed : r)));
        setActiveRecording(refreshed);
        if (refreshed.status === 'Transcribed') {
          await fetchTranscriptOnce(rec.id);
        }
      } catch (e) {
        setError(extractErrorMessage(e, '文本润色失败'));
      } finally {
        setRefiningId((prev) => (prev === rec.id ? null : prev));
      }
    },
    [meetingId, fetchTranscriptOnce]
  );

  const remove = useCallback(
    async (rec: MeetingRecording) => {
      if (!meetingId) return;
      try {
        await meetingsApi.deleteRecording(meetingId, rec.id);
        setRecordings((prev) => prev.filter((r) => r.id !== rec.id));
        setActiveRecording((prev) => (prev?.id === rec.id ? null : prev));
        setTranscriptSegments((prev) => (activeIdRef.current === rec.id ? [] : prev));
      } catch (e) {
        setError(extractErrorMessage(e, '删除失败'));
      }
    },
    [meetingId]
  );

  const reset = useCallback(() => {
    stopPolling();
    setRecordings([]);
    setActiveRecording(null);
    setTranscriptSegments([]);
    setError(null);
  }, [stopPolling]);

  // 切到别的 meeting 时清干净
  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling, meetingId]);

  const enabled = options.enabled !== false;

  // refreshKey / enabled 变化时重载；enabled=false 时不抢首屏带宽
  useEffect(() => {
    if (!enabled) {
      stopPolling();
      return;
    }
    if (meetingId) {
      void loadRecordings(meetingId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId, options.refreshKey, enabled]);

  // 按 status 启动/停止轮询；条件变化才重跑
  useEffect(() => {
    stopPolling();
    if (!enabled || !activeRecording || !meetingId) return;
    const status: MeetingRecordingStatus = activeRecording.status;
    if (status === 'Uploaded' || status === 'Transcribing') {
      pollTimerRef.current = window.setInterval(() => {
        const mid = meetingIdRef.current;
        const rid = activeIdRef.current;
        if (!mid || !rid) return;
        const controller = new AbortController();
        abortRef.current = controller;
        meetingsApi
          .getRecording(mid, rid)
          .then((detail) => {
            setRecordings((prev) => prev.map((r) => (r.id === detail.id ? detail : r)));
            setActiveRecording((prev) => (prev?.id === detail.id ? detail : prev));
            if (detail.status === 'Transcribed' || detail.status === 'Failed') {
              stopPolling();
              if (detail.status === 'Transcribed') {
                void fetchTranscriptOnce(rid);
              }
            }
          })
          .catch(() => { /* swallow — next tick will retry */ });
      }, 2000);
    } else if (status === 'Transcribed') {
      // 已完成则拉一次转写
      void fetchTranscriptOnce(activeRecording.id);
    }
    return () => stopPolling();
  }, [enabled, activeRecording?.id, activeRecording?.status, meetingId, fetchTranscriptOnce, stopPolling]);

  return {
    recordings,
    activeRecording,
    transcriptSegments,
    isRecordingsLoading,
    isTranscriptLoading,
    isUploading,
    retranscribingId,
    refiningId,
    error,
    loadRecordings,
    selectRecording,
    uploadRecording,
    retranscribe,
    refine,
    remove,
    setActiveRecording,
    reset,
  };
}
