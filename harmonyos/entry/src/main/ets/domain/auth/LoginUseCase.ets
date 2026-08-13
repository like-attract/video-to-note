import type { BiliAuthGateway, QrLoginChallenge, QrLoginPollResult } from './BiliAuthGateway';
import { QrLoginState } from './BiliAuthGateway';
import type { BiliSessionRepository } from './BiliSessionRepository';
import { AppError, AppErrorCode, toUserMessage } from '../../core/errors/AppError';

export class LoginUseCase {
  private readonly gateway: BiliAuthGateway;
  private readonly sessions: BiliSessionRepository;
  constructor(gateway: BiliAuthGateway, sessions: BiliSessionRepository) {
    this.gateway = gateway;
    this.sessions = sessions;
  }

  createChallenge(): Promise<QrLoginChallenge> { return this.gateway.createChallenge(); }

  async poll(qrCodeKey: string): Promise<QrLoginPollResult> {
    const result: QrLoginPollResult = await this.gateway.poll(qrCodeKey);
    if (result.state === QrLoginState.Succeeded && result.session !== undefined) {
      try {
        await this.sessions.save(result.session);
      } catch (error) {
        throw new AppError(AppErrorCode.StorageError,
          `B站已确认登录，但安全保存登录凭据失败：${toUserMessage(error as Object)}`);
      }
    }
    return result;
  }

  async logout(): Promise<void> { await this.sessions.clear(); }
  cancel(): void { this.gateway.cancelCurrent(); }
}
