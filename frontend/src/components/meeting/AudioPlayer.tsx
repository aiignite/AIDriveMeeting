import React, { useEffect, useRef, useState } from 'react';
import { Play, Pause, Volume2 } from 'lucide-react';

interface AudioPlayerProps {
  src: string;
  /** Optional mime type hint, used when the URL has no extension. */
  mimeType?: string;
  className?: string;
}

/**
 * 受控音频播放器。不直链 storage path —— 路由后端需要鉴权时由父组件
 * 传入已签名的 URL 或 blob: URL。这里只负责播放控件。
 *
 * 设计要点：
 *  1. 用 `key={src}` 强制 <audio> 在 src 变化时整体 remount，
 *     避免 <source> 子元素更新但 audio 元素不重载导致的"始终播放上一个录音"bug。
 *  2. 状态 (playing / current / duration) 随 remount 自动重置。
 *  3. 仅暴露播放/进度控制，不直连 storage path。
 */
export const AudioPlayer: React.FC<AudioPlayerProps> = ({ src, mimeType, className }) => {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(0.8);

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    el.volume = volume;
  }, [volume]);

  // 仅在组件挂载时同步音量；src 变化由 <audio key={src}> 触发 remount，
  // 不需要再手动 setPlaying / setCurrent / setDuration / load()。

  const format = (s: number) => {
    if (!isFinite(s) || s < 0) return '00:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s - m * 60);
    return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
  };

  return (
    <div className={`flex items-center gap-3 bg-gray-50 dark:bg-gray-800/60 rounded-lg px-3 py-2 ${className || ''}`}>
      <button
        type="button"
        onClick={() => {
          const el = audioRef.current;
          if (!el) return;
          if (playing) el.pause();
          else el.play().catch(() => { /* autoplay denied */ });
        }}
        className="w-9 h-9 rounded-full bg-sky-500 text-white flex items-center justify-center hover:bg-sky-600"
        aria-label={playing ? 'pause' : 'play'}
      >
        {playing ? <Pause size={16} /> : <Play size={16} />}
      </button>
      <div className="flex-1">
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={current}
          onChange={(e) => {
            const v = Number(e.target.value);
            const el = audioRef.current;
            if (el) el.currentTime = v;
            setCurrent(v);
          }}
          className="w-full accent-sky-500"
        />
        <div className="flex items-center justify-between text-[11px] text-gray-500 mt-0.5">
          <span>{format(current)}</span>
          <span>{format(duration)}</span>
        </div>
      </div>
      <div className="flex items-center gap-1.5 text-gray-500">
        <Volume2 size={14} />
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={volume}
          onChange={(e) => setVolume(Number(e.target.value))}
          className="w-16 accent-sky-500"
        />
      </div>
      <audio
        key={src}
        ref={audioRef}
        src={src || undefined}
        preload="metadata"
        onLoadedMetadata={(e) => setDuration((e.target as HTMLAudioElement).duration || 0)}
        onTimeUpdate={(e) => setCurrent((e.target as HTMLAudioElement).currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      >
        {src && mimeType ? <source src={src} type={mimeType} /> : null}
      </audio>
    </div>
  );
};

export default AudioPlayer;
