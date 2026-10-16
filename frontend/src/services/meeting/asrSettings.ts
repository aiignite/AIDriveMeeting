/**
 * 会议语音服务（ASR）设置 API
 *
 * 提供本机/远程 Whisper 语音转写配置的读取、保存以及远程连通性测试。
 * 后端返回 snake_case，前端 client.ts 会自动转换为 camelCase，因此接口定义统一使用 camelCase。
 */
import { api } from '../client';

/** ASR 运行模式 */
export type MeetingAsrMode = 'local' | 'remote';

/** 会议 ASR 设置（读取/保存后返回） */
export interface MeetingAsrSettings {
  /** 运行模式：本机(local) 或 远程(remote) */
  mode: MeetingAsrMode;
  /** 远程 ASR 网关地址 */
  remoteUrl: string | null;
  /** 远程 ASR API Key（读取时可能脱敏） */
  remoteApiKey: string | null;
  /** 远程请求超时（秒） */
  remoteTimeoutSeconds: number;
  /** 远程失败时是否降级到本机 */
  fallbackToLocal: boolean;
  /** 本机 faster-whisper 模型名称 */
  model: string;
  /** 识别语言 */
  language: string;
  /** 计算设备：cpu / cuda */
  device: string;
  /** 计算精度：int8 / float16 等 */
  computeType: string;
}

/** 会议 ASR 设置更新请求（所有字段可选） */
export interface MeetingAsrSettingsUpdate {
  mode?: MeetingAsrMode;
  remoteUrl?: string | null;
  remoteApiKey?: string | null;
  remoteTimeoutSeconds?: number;
  fallbackToLocal?: boolean;
  model?: string;
  language?: string;
  device?: string;
  computeType?: string;
}

/** 远程 ASR 连通性测试结果 */
export interface MeetingAsrTestResult {
  /** 是否可达 */
  reachable: boolean;
  /** 详细提示信息 */
  detail?: string;
}

/** 会议 ASR 设置相关 API */
export const meetingAsrSettingsApi = {
  /** 获取当前生效的会议 ASR 设置 */
  get: () => api.get<MeetingAsrSettings>('/meeting/asr-settings'),

  /** 保存会议 ASR 设置 */
  save: (data: MeetingAsrSettingsUpdate) =>
    api.put<MeetingAsrSettings>('/meeting/asr-settings', data),

  /** 测试远程 ASR 服务连通性 */
  testRemote: () =>
    api.post<MeetingAsrTestResult>('/meeting/asr-settings/test-remote'),
};
