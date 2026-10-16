/**
 * 语音服务设置组件
 *
 * 用于在系统设置页面配置会议纪要的 ASR（语音转文字）服务。
 * 支持本机 faster-whisper 与远程 asr_gateway 两种模式切换。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Copy, ChevronDown, ChevronUp, Loader2, Check, Radio } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  meetingAsrSettingsApi,
  type MeetingAsrSettings,
  type MeetingAsrSettingsUpdate,
  type MeetingAsrMode,
} from '../../services/meeting/asrSettings';

/**
 * 表单兜底默认值：仅在 GET 接口失败时使用。
 * 这些值必须与 backend/app/config.py 及 .env 中的默认值保持一致：
 *   MEETING_ASR_MODE=local
 *   MEETING_ASR_REMOTE_TIMEOUT=3600
 *   MEETING_ASR_FALLBACK_LOCAL=false
 *   MEETING_WHISPER_MODEL=medium
 *   MEETING_WHISPER_LANGUAGE=zh
 *   MEETING_WHISPER_DEVICE=cpu
 *   MEETING_WHISPER_COMPUTE_TYPE=int8
 * 正常流程下页面会通过 GET /meeting/asr-settings 读取后端 env 当前值，
 * 数据库未保存时后端返回 env 当前值，因此表单默认显示的就是 env 配置。
 */
const DEFAULT_SETTINGS: MeetingAsrSettings = {
  mode: 'local',
  remoteUrl: null,
  remoteApiKey: null,
  remoteTimeoutSeconds: 3600,
  fallbackToLocal: false,
  model: 'medium',
  language: 'zh',
  device: 'cpu',
  computeType: 'int8',
};

/** 本地模式安装指南帮助文本 */
const LOCAL_GUIDE_ITEMS = [
  {
    label: '安装 ffmpeg / ffprobe',
    description: '并确保已加入系统 PATH，转写前会调用 ffprobe 读取音频信息。',
    command: '# macOS\nbrew install ffmpeg\n\n# Ubuntu/Debian\nsudo apt update && sudo apt install ffmpeg -y',
  },
  {
    label: '安装 Python 依赖 faster-whisper',
    description: '推荐使用国内镜像加速下载。',
    command: 'pip install faster-whisper -i https://mirrors.aliyun.com/pypi/simple',
  },
  {
    label: '设置模型下载镜像（可选）',
    description: '首次使用会自动下载 Whisper 模型；国内建议设置 HuggingFace 镜像。',
    command: 'export HF_ENDPOINT=https://hf-mirror.com',
  },
  {
    label: '选择设备与计算精度',
    description: 'CPU 推荐 device=cpu、computeType=int8；有 NVIDIA GPU 可改为 cuda + float16。',
    command: '# CPU\ndevice=cpu\ncomputeType=int8\n\n# NVIDIA GPU\ndevice=cuda\ncomputeType=float16',
  },
];

/** 从 unknown 错误对象中提取可读消息 */
function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) return err.message;
  if (typeof err === 'string') return err;
  return fallback;
}

/** 远程模式安装指南帮助文本 */
const REMOTE_GUIDE_ITEMS = [
  {
    label: '部署 asr_gateway',
    description: '在局域网 GPU 服务器上部署 asr_gateway/ 目录中的服务。',
    command: 'git clone <asr_gateway_repo>\ncd asr_gateway\ncp .env.example .env',
  },
  {
    label: '安装 NVIDIA 驱动与 CUDA',
    description: '确保 GPU 服务器已安装 NVIDIA Driver、CUDA，可选 Docker + nvidia-container-toolkit。',
    command: '# 检查驱动\nnvidia-smi\n\n# Docker 运行示例（如提供镜像）\ndocker run --gpus all -d --name asr-gateway -p 8000:8000 asr_gateway:latest',
  },
  {
    label: '配置 API Key',
    description: '启动 asr_gateway 时设置 ASR_API_KEY，与下方“API Key”输入框保持一致。',
    command: 'ASR_API_KEY=your-secret-key\n# 或写入 .env 后启动',
  },
  {
    label: '网络安全',
    description: '防火墙仅允许应用服务器访问算力机目标端口，不要暴露到公网。',
    command: '# 示例：仅允许应用服务器 10.0.0.5 访问 8000 端口\nufw allow from 10.0.0.5 to any port 8000',
  },
];

/** 单行可复制命令（用于一键复制） */
const REMOTE_ENV_EXAMPLE = `ASR_API_KEY=your-secret-key
ASR_HOST=0.0.0.0
ASR_PORT=8000`;

const REMOTE_SYSTEMD_SERVICE = `[Unit]
Description=ASR Gateway
After=network.target

[Service]
Type=simple
User=asr
WorkingDirectory=/opt/asr_gateway
Environment=ASR_API_KEY=your-secret-key
Environment=ASR_HOST=0.0.0.0
Environment=ASR_PORT=8000
ExecStart=/opt/asr_gateway/venv/bin/python -m asr_gateway.main
Restart=always

[Install]
WantedBy=multi-user.target`;

/**
 * 语音服务设置主组件
 */
const VoiceServiceSettings: React.FC = () => {
  /** 当前生效设置（从后端读取/保存后返回） */
  const [savedSettings, setSavedSettings] = useState<MeetingAsrSettings | null>(null);
  /** 表单草稿 */
  const [draft, setDraft] = useState<MeetingAsrSettingsUpdate>({});
  /** 页面初始加载中 */
  const [loading, setLoading] = useState(false);
  /** 保存中 */
  const [saving, setSaving] = useState(false);
  /** 远程连通性测试中 */
  const [testing, setTesting] = useState(false);
  /** 安装指南展开状态 */
  const [guideOpen, setGuideOpen] = useState(false);
  /** 保存成功提示 */
  const [saveSuccess, setSaveSuccess] = useState(false);

  /** 初始化加载设置 */
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    meetingAsrSettingsApi
      .get()
      .then((data) => {
        if (cancelled) return;
        setSavedSettings(data);
        setDraft(data);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        toast.error(err.message || '加载语音服务设置失败');
        setSavedSettings(DEFAULT_SETTINGS);
        setDraft(DEFAULT_SETTINGS);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** 当前生效显示文本 */
  const effectiveStatus = useMemo(() => {
    const settings = savedSettings ?? DEFAULT_SETTINGS;
    if (settings.mode === 'remote') {
      const url = settings.remoteUrl || '未配置';
      const fallback = settings.fallbackToLocal ? '，失败降级本机' : '';
      return `当前生效：远程模式 ${url}${fallback}`;
    }
    return `当前生效：本机模式（model=${settings.model}, device=${settings.device}, computeType=${settings.computeType}）`;
  }, [savedSettings]);

  /** 更新单个表单字段 */
  const updateField = <K extends keyof MeetingAsrSettingsUpdate>(
    key: K,
    value: MeetingAsrSettingsUpdate[K],
  ) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
  };

  /** 切换模式时保留当前模式对应字段的合理性 */
  const handleModeChange = (mode: MeetingAsrMode) => {
    setDraft((prev) => ({ ...prev, mode }));
  };

  /** 复制文本到剪贴板 */
  const copyToClipboard = async (text: string, description: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${description} 已复制`);
    } catch {
      toast.error('复制失败，请手动复制');
    }
  };

  /** 保存设置 */
  const handleSave = async () => {
    setSaving(true);
    setSaveSuccess(false);
    try {
      const data = await meetingAsrSettingsApi.save(draft);
      setSavedSettings(data);
      setDraft(data);
      setSaveSuccess(true);
      toast.success('语音服务设置已保存');
      setTimeout(() => setSaveSuccess(false), 2000);
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, '保存失败'));
    } finally {
      setSaving(false);
    }
  };

  /** 测试远程连接 */
  const handleTestRemote = async () => {
    setTesting(true);
    try {
      const result = await meetingAsrSettingsApi.testRemote();
      if (result.reachable) {
        toast.success(result.detail || '远程 ASR 服务可达');
      } else {
        toast.error(result.detail || '远程 ASR 服务不可达');
      }
    } catch (err: unknown) {
      toast.error(getErrorMessage(err, '测试远程连接失败'));
    } finally {
      setTesting(false);
    }
  };

  /** 当前模式 */
  const currentMode = draft.mode ?? DEFAULT_SETTINGS.mode;
  const guideItems = currentMode === 'local' ? LOCAL_GUIDE_ITEMS : REMOTE_GUIDE_ITEMS;

  return (
    <form
      autoComplete="off"
      // 阻止浏览器把保存的账号密码自动填充到远程配置输入框
      onSubmit={(e) => e.preventDefault()}
      className="space-y-6"
    >
      <h3 className="text-lg font-bold text-gray-900 dark:text-white border-b border-gray-100 dark:border-gray-700 pb-4">
        语音服务设置
      </h3>

      {/* 生效状态提示 */}
      <div className="flex items-start gap-2 text-sm text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800 rounded-lg p-3">
        <Radio size={18} className="mt-0.5 flex-shrink-0" />
        <span>{effectiveStatus}</span>
      </div>

      {/* 模式选择 */}
      <div className="space-y-3">
        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 block">运行模式</label>
        <div className="flex gap-6">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="asr-mode"
              value="local"
              checked={currentMode === 'local'}
              onChange={() => handleModeChange('local')}
              disabled={loading}
              className="text-blue-600 focus:ring-blue-500"
            />
            <span className="text-sm text-gray-700 dark:text-gray-300">本机模式</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="asr-mode"
              value="remote"
              checked={currentMode === 'remote'}
              onChange={() => handleModeChange('remote')}
              disabled={loading}
              className="text-blue-600 focus:ring-blue-500"
            />
            <span className="text-sm text-gray-700 dark:text-gray-300">远程模式</span>
          </label>
        </div>
      </div>

      {/* 本机模式表单 */}
      {currentMode === 'local' && (
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5 col-span-2 sm:col-span-1">
            <label htmlFor="asr-model" className="text-sm font-medium text-gray-700 dark:text-gray-300">模型</label>
            <input
              id="asr-model"
              type="text"
              value={draft.model ?? DEFAULT_SETTINGS.model}
              onChange={(e) => updateField('model', e.target.value)}
              placeholder="例如：medium"
              disabled={loading}
              className="w-full px-4 py-2 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            />
          </div>
          <div className="space-y-1.5 col-span-2 sm:col-span-1">
            <label htmlFor="asr-language" className="text-sm font-medium text-gray-700 dark:text-gray-300">语言</label>
            <input
              id="asr-language"
              type="text"
              value={draft.language ?? DEFAULT_SETTINGS.language}
              onChange={(e) => updateField('language', e.target.value)}
              placeholder="例如：zh"
              disabled={loading}
              className="w-full px-4 py-2 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            />
          </div>
          <div className="space-y-1.5 col-span-2 sm:col-span-1">
            <label htmlFor="asr-device" className="text-sm font-medium text-gray-700 dark:text-gray-300">设备</label>
            <select
              id="asr-device"
              value={draft.device ?? DEFAULT_SETTINGS.device}
              onChange={(e) => updateField('device', e.target.value)}
              disabled={loading}
              className="w-full px-4 py-2 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            >
              <option value="cpu">cpu</option>
              <option value="cuda">cuda</option>
            </select>
          </div>
          <div className="space-y-1.5 col-span-2 sm:col-span-1">
            <label htmlFor="asr-compute-type" className="text-sm font-medium text-gray-700 dark:text-gray-300">计算精度</label>
            <select
              id="asr-compute-type"
              value={draft.computeType ?? DEFAULT_SETTINGS.computeType}
              onChange={(e) => updateField('computeType', e.target.value)}
              disabled={loading}
              className="w-full px-4 py-2 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            >
              <option value="int8">int8</option>
              <option value="float16">float16</option>
              <option value="float32">float32</option>
            </select>
          </div>
        </div>
      )}

      {/* 远程模式表单 */}
      {currentMode === 'remote' && (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="asr-remote-url" className="text-sm font-medium text-gray-700 dark:text-gray-300">远程服务地址</label>
            <input
              id="asr-remote-url"
              type="text"
              name="asr-remote-url-off"
              autoComplete="off"
              value={draft.remoteUrl ?? ''}
              onChange={(e) => updateField('remoteUrl', e.target.value || null)}
              placeholder="例如：http://10.0.0.10:8000"
              disabled={loading}
              className="w-full px-4 py-2 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="asr-remote-api-key" className="text-sm font-medium text-gray-700 dark:text-gray-300">API Key</label>
            <input
              id="asr-remote-api-key"
              type="text"
              name="asr-remote-api-key-off"
              autoComplete="off"
              value={draft.remoteApiKey ?? ''}
              onChange={(e) => updateField('remoteApiKey', e.target.value || null)}
              placeholder="与 asr_gateway 的 ASR_API_KEY 保持一致"
              disabled={loading}
              className="w-full px-4 py-2 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="asr-remote-timeout" className="text-sm font-medium text-gray-700 dark:text-gray-300">超时时间（秒）</label>
            <input
              id="asr-remote-timeout"
              type="number"
              min={1}
              value={draft.remoteTimeoutSeconds ?? DEFAULT_SETTINGS.remoteTimeoutSeconds}
              onChange={(e) => updateField('remoteTimeoutSeconds', Number(e.target.value))}
              disabled={loading}
              className="w-full px-4 py-2 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-blue-100 focus:border-blue-500 outline-none bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={draft.fallbackToLocal ?? DEFAULT_SETTINGS.fallbackToLocal}
              onChange={(e) => updateField('fallbackToLocal', e.target.checked)}
              disabled={loading}
              className="w-4 h-4 text-blue-600 rounded focus:ring-blue-500 border-gray-300"
            />
            <span className="text-sm text-gray-600 dark:text-gray-400">远程服务不可用时降级到本机模式</span>
          </label>
        </div>
      )}

      {/* 操作按钮 */}
      <div className="pt-4 flex justify-end gap-3 items-center border-t border-gray-100 dark:border-gray-700">
        {saveSuccess && (
          <span className="text-sm text-green-600 dark:text-green-400 flex items-center gap-1">
            <Check size={16} /> 已保存
          </span>
        )}
        <button
          type="button"
          onClick={handleTestRemote}
          disabled={currentMode === 'local' || loading || testing}
          className="px-4 py-2 text-sm font-medium text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg disabled:opacity-50 flex items-center gap-2"
        >
          {testing && <Loader2 size={14} className="animate-spin" />}
          测试远程连接
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={loading || saving}
          className="px-4 py-2 text-sm font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
        >
          {saving && <Loader2 size={14} className="animate-spin" />}
          保存
        </button>
      </div>

      {/* 安装与配置指南 */}
      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <button
          type="button"
          onClick={() => setGuideOpen((prev) => !prev)}
          className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 dark:bg-gray-800/50 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          <span>安装与配置指南</span>
          {guideOpen ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>
        {guideOpen && (
          <div className="p-4 space-y-4 bg-white dark:bg-gray-800">
            {guideItems.map((item) => (
              <div key={item.label} className="space-y-2">
                <h4 className="text-sm font-semibold text-gray-900 dark:text-white">{item.label}</h4>
                <p className="text-xs text-gray-500 dark:text-gray-400">{item.description}</p>
                <div className="relative group">
                  <pre className="text-xs bg-gray-900 text-gray-100 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all">
                    {item.command}
                  </pre>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(item.command, item.label)}
                    className="absolute top-2 right-2 p-1.5 bg-gray-700 hover:bg-gray-600 rounded-md text-white opacity-0 group-hover:opacity-100 transition-opacity"
                    title="复制"
                  >
                    <Copy size={14} />
                  </button>
                </div>
              </div>
            ))}
            {currentMode === 'remote' && (
              <>
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold text-gray-900 dark:text-white">远程 .env 示例</h4>
                  <div className="relative group">
                    <pre className="text-xs bg-gray-900 text-gray-100 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all">
                      {REMOTE_ENV_EXAMPLE}
                    </pre>
                    <button
                      type="button"
                      onClick={() => copyToClipboard(REMOTE_ENV_EXAMPLE, '远程 .env 示例')}
                      className="absolute top-2 right-2 p-1.5 bg-gray-700 hover:bg-gray-600 rounded-md text-white opacity-0 group-hover:opacity-100 transition-opacity"
                      title="复制"
                    >
                      <Copy size={14} />
                    </button>
                  </div>
                </div>
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold text-gray-900 dark:text-white">systemd 启动命令</h4>
                  <div className="relative group">
                    <pre className="text-xs bg-gray-900 text-gray-100 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all">
                      {REMOTE_SYSTEMD_SERVICE}
                    </pre>
                    <button
                      type="button"
                      onClick={() => copyToClipboard(REMOTE_SYSTEMD_SERVICE, 'systemd 启动命令')}
                      className="absolute top-2 right-2 p-1.5 bg-gray-700 hover:bg-gray-600 rounded-md text-white opacity-0 group-hover:opacity-100 transition-opacity"
                      title="复制"
                    >
                      <Copy size={14} />
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </form>
  );
};

export default VoiceServiceSettings;
