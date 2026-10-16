import React from 'react';
import { Loader2, CheckCircle2, Upload, AlertCircle } from 'lucide-react';

export type RecordingStatus = 'Uploaded' | 'Transcribing' | 'Transcribed' | 'Failed';

interface RecordingStatusBadgeProps {
  status: string;
  progressPct?: number;
  errorMessage?: string | null;
  className?: string;
}

const STATUS_STYLES: Record<RecordingStatus, { bg: string; text: string; label: string; icon: React.ReactNode }> = {
  Uploaded:    { bg: 'bg-sky-50',     text: 'text-sky-700',     label: '已上传',   icon: <Upload size={12} /> },
  Transcribing:{ bg: 'bg-amber-50',   text: 'text-amber-700',   label: '转写中',   icon: <Loader2 size={12} className="animate-spin" /> },
  Transcribed: { bg: 'bg-emerald-50', text: 'text-emerald-700', label: '已完成',   icon: <CheckCircle2 size={12} /> },
  Failed:      { bg: 'bg-rose-50',    text: 'text-rose-700',    label: '失败',     icon: <AlertCircle size={12} /> },
};

export const RecordingStatusBadge: React.FC<RecordingStatusBadgeProps> = ({
  status,
  progressPct = 0,
  errorMessage,
  className,
}) => {
  const cfg = STATUS_STYLES[status as RecordingStatus] || STATUS_STYLES.Uploaded;
  const isInProgress = status === 'Uploaded' || status === 'Transcribing';
  return (
    <div className={className}>
      <div className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ${cfg.bg} ${cfg.text}`}>
        {cfg.icon}
        {cfg.label}
        {isInProgress && progressPct > 0 && progressPct < 100 && (
          <span className="ml-1 text-gray-500">{progressPct}%</span>
        )}
      </div>
      {isInProgress && (
        <div className="mt-1 h-1 w-full bg-gray-100 dark:bg-gray-700 rounded overflow-hidden">
          <div
            className="h-full bg-sky-500 transition-all"
            style={{ width: `${Math.min(100, Math.max(0, progressPct))}%` }}
          />
        </div>
      )}
      {status === 'Failed' && errorMessage && (
        <p className="mt-1 text-[11px] text-rose-600 break-all">{errorMessage}</p>
      )}
    </div>
  );
};

export default RecordingStatusBadge;
