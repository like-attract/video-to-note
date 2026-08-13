import { AppError, AppErrorCode } from './AppError';

export function mapRemoteError(error: Object): AppError {
  if (error instanceof AppError) return error;
  const message: string = error instanceof Error ? error.message : '远程服务请求失败。';
  const lower: string = message.toLowerCase();
  if (lower.includes('timeout') || message.includes('超时'))
    return new AppError(AppErrorCode.NetworkTimeout, '请求超时，请检查网络后重试。', true);
  if (lower.includes('401') || lower.includes('403') || message.includes('无权限'))
    return new AppError(AppErrorCode.Unauthorized, message, false);
  if (lower.includes('429') || message.includes('受限') || message.includes('余额不足'))
    return new AppError(AppErrorCode.RateLimited, message, true);
  if (message.includes('字幕')) return new AppError(AppErrorCode.NoSubtitle, message, false);
  if (message.includes('登录') || message.includes('SESSDATA'))
    return new AppError(AppErrorCode.SessionExpired, message, false);
  return new AppError(AppErrorCode.ApiError, message, true);
}
