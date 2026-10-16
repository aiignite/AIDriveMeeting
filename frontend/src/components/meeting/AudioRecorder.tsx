import React, { useEffect, useRef, useState } from 'react';
import { Mic, Square, AlertTriangle, Upload } from 'lucide-react';

interface AudioRecorderProps {
  /** Disable after a successful capture until the caller resets. */
  disabled?: boolean;
  /** Called with the captured audio Blob and a synthetic file name. */
  onCaptured: (blob: Blob, fileName: string) => void;
  /** Max capture length in milliseconds. Default 60 min. */
  maxDurationMs?: number;
  /** Optional className passthrough. */
  className?: string;
  /** compact: toolbar style; unsupported still shows upload fallback (never hide). */
  variant?: 'default' | 'compact';
  /** When true with compact, render icon-only buttons (text via title). */
  iconOnly?: boolean;
  /** When microphone unsupported, call this to open file picker / upload UI. */
  onFallbackUpload?: () => void;
}

/**
 * 浏览器原生录音组件：优先 audio/webm;codecs=opus；Safari 不支持时
 * 主动报告失败以便上层回退到 AudioUploader 文件上传。
 *
 * 非安全上下文（如 http://内网IP）下无法录音；compact 模式改为上传入口，禁止 return null。
 */
export const AudioRecorder: React.FC<AudioRecorderProps> = ({
  disabled,
  onCaptured,
  maxDurationMs = 60 * 60 * 1000,
  className,
  variant = 'default',
  iconOnly = false,
  onFallbackUpload,
}) => {
  const [supported, setSupported] = useState<boolean>(true);
  const [recording, setRecording] = useState<boolean>(false);
  const [elapsed, setElapsed] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  const tickRef = useRef<number | null>(null);

  useEffect(() => {
    const hasMedia =
      typeof navigator !== 'undefined' &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof window !== 'undefined' &&
      window.isSecureContext;
    setSupported(hasMedia);
  }, []);

  useEffect(() => {
    return () => {
      if (tickRef.current !== null) {
        window.clearInterval(tickRef.current);
      }
      const rec = recorderRef.current;
      if (rec && rec.state !== 'inactive') {
        try { rec.stop(); } catch { /* noop */ }
      }
    };
  }, []);

  const startCapture = async () => {
    if (!supported) {
      setError('当前浏览器不支持录音，请改用文件上传。');
      return;
    }
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : '';
      const rec = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      rec.onstop = () => {
        const type = rec.mimeType || 'audio/webm';
        const blob = new Blob(chunksRef.current, { type });
        stream.getTracks().forEach((t) => t.stop());
        const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        onCaptured(blob, `recording-${ts}.${ext}`);
        chunksRef.current = [];
      };
      rec.start();
      recorderRef.current = rec;
      startedAtRef.current = Date.now();
      setRecording(true);
      setElapsed(0);
      tickRef.current = window.setInterval(() => {
        const ms = Date.now() - startedAtRef.current;
        setElapsed(ms);
        if (ms >= maxDurationMs) {
          stopCapture();
        }
      }, 250);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '无法访问麦克风';
      setError(msg);
      setRecording(false);
    }
  };

  const stopCapture = () => {
    const rec = recorderRef.current;
    if (rec && rec.state !== 'inactive') {
      try { rec.stop(); } catch { /* noop */ }
    }
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
    setRecording(false);
  };

  if (!supported) {
    const isHttp =
      typeof window !== 'undefined' && !window.isSecureContext;
    const hint = isHttp
      ? 'HTTP 环境无法浏览器录音，请上传音频或改用 HTTPS'
      : '当前浏览器不支持录音，请上传音频';

    if (variant === 'compact') {
      return (
        <div className={`flex items-center gap-2 ${className || ''}`}>
          <button
            type="button"
            onClick={() => onFallbackUpload?.()}
            disabled={disabled || !onFallbackUpload}
            title={hint}
            aria-label="上传音频"
            className={`inline-flex items-center justify-center font-medium rounded-lg bg-amber-50 text-amber-800 hover:bg-amber-100 border border-amber-200 disabled:opacity-50 shadow-sm ${
              iconOnly ? 'p-2' : 'gap-1.5 px-4 py-2 text-sm'
            }`}
          >
            <Upload size={14} />
            {!iconOnly && '上传音频'}
          </button>
          {!iconOnly && (
            <span className="text-[11px] text-amber-600 max-w-[200px] leading-tight hidden sm:inline">
              {hint}
            </span>
          )}
        </div>
      );
    }

    return (
      <div className={`text-xs text-amber-600 flex items-center gap-1 ${className || ''}`}>
        <AlertTriangle size={14} /> {hint}
      </div>
    );
  }

  const formatElapsed = (ms: number) => {
    const total = Math.floor(ms / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const compactIcon = variant === 'compact' && iconOnly;

  return (
    <div className={`flex items-center gap-2 ${className || ''}`}>
      {!recording ? (
        <button
          type="button"
          onClick={startCapture}
          disabled={disabled}
          title="开始录音"
          aria-label="开始录音"
          className={`inline-flex items-center justify-center font-medium disabled:opacity-50 ${
            compactIcon
              ? 'p-2 rounded-lg bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200 shadow-sm'
              : 'gap-1.5 px-3 py-1.5 text-xs rounded-md bg-rose-50 text-rose-700 hover:bg-rose-100 border border-rose-200'
          }`}
        >
          <Mic size={14} />
          {!compactIcon && '开始录音'}
        </button>
      ) : (
        <button
          type="button"
          onClick={stopCapture}
          title={`停止录音 (${formatElapsed(elapsed)})`}
          aria-label={`停止录音 (${formatElapsed(elapsed)})`}
          className={`inline-flex items-center justify-center font-medium ${
            compactIcon
              ? 'p-2 rounded-lg bg-rose-600 text-white hover:bg-rose-700 shadow-sm'
              : 'gap-1.5 px-3 py-1.5 text-xs rounded-md bg-rose-600 text-white hover:bg-rose-700'
          }`}
        >
          <Square size={14} />
          {!compactIcon && `停止 (${formatElapsed(elapsed)})`}
        </button>
      )}
      {error && <span className="text-xs text-rose-600">{error}</span>}
    </div>
  );
};

export default AudioRecorder;
