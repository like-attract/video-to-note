export class NetworkPolicy {
  static readonly ConnectTimeoutMs: number = 15000;
  static readonly BiliReadTimeoutMs: number = 30000;
  // Non-streaming reasoning responses may not deliver a completed body for several minutes.
  // Keep this bounded, while allowing faithful/detailed DeepSeek output to finish.
  static readonly LlmReadTimeoutMs: number = 300000;
  static readonly MaxResponseBytes: number = 8 * 1024 * 1024;
  static readonly ShortLinkMaxRedirects: number = 5;
  // B站读取是幂等操作，可由上层在明确的网络错误后允许用户重试一次。
  static readonly BiliOperationMaxAttempts: number = 2;
  // LLM POST 可能产生计费与重复结果，不进行无确认的自动重试。
  static readonly LlmOperationMaxAttempts: number = 1;
}
