import type { BiliAuthGateway, QrLoginChallenge, QrLoginPollResult } from '../../domain/auth/BiliAuthGateway';
import { mapRemoteError } from '../../core/errors/RemoteErrorMapper';
import { BiliLoginService } from '../../services/BiliLoginService';

export class RemoteBiliAuthGateway implements BiliAuthGateway {
  private readonly service: BiliLoginService = new BiliLoginService();
  async createChallenge(): Promise<QrLoginChallenge> {
    try { return await this.service.createChallenge(); }
    catch (error) { throw mapRemoteError(error); }
  }
  async poll(qrCodeKey: string): Promise<QrLoginPollResult> {
    try { return await this.service.poll(qrCodeKey); }
    catch (error) { throw mapRemoteError(error); }
  }
  cancelCurrent(): void { this.service.cancelCurrent(); }
}
