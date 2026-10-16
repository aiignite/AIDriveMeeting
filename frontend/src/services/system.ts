/**
 * 系统用户目录服务（抄送/责任人下拉等场景）。
 */
import { api } from './client';

export interface DirectoryUser {
  id: string;
  email: string;
  name: string;
}

export const systemApi = {
  users: () => api.get<DirectoryUser[]>('/users/directory'),
};
