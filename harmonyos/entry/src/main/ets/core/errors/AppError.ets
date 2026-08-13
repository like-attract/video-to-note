export enum AppErrorCode {
  InvalidInput = 'INVALID_INPUT',
  NotLoggedIn = 'NOT_LOGGED_IN',
  SessionExpired = 'SESSION_EXPIRED',
  NetworkTimeout = 'NETWORK_TIMEOUT',
  NetworkUnavailable = 'NETWORK_UNAVAILABLE',
  Unauthorized = 'UNAUTHORIZED',
  RateLimited = 'RATE_LIMITED',
  ApiError = 'API_ERROR',
  RiskControl = 'RISK_CONTROL',
  NoSubtitle = 'NO_SUBTITLE',
  ParseError = 'PARSE_ERROR',
  Cancelled = 'CANCELLED',
  StorageError = 'STORAGE_ERROR',
  Unknown = 'UNKNOWN'
}

export class AppError extends Error {
  readonly code: AppErrorCode;
  readonly retryable: boolean;

  constructor(code: AppErrorCode, message: string, retryable: boolean = false) {
    super(message);
    this.name = 'AppError';
    this.code = code;
    this.retryable = retryable;
  }
}

export function toUserMessage(error: Object): string {
  if (error instanceof AppError) return error.message;
  if (error instanceof Error && error.message.trim().length > 0) return error.message;
  return '操作失败，请稍后重试。';
}
