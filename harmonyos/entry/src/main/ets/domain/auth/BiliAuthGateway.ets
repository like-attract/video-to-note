import type { BiliSession } from '../../models/BiliModels';

export interface QrLoginChallenge {
  qrCodeUrl: string;
  qrCodeKey: string;
  expiresInSeconds: number;
}

export enum QrLoginState {
  WaitingForScan = 'waiting_for_scan',
  WaitingForConfirmation = 'waiting_for_confirmation',
  Succeeded = 'succeeded',
  Expired = 'expired'
}

export interface QrLoginPollResult {
  state: QrLoginState;
  message: string;
  session?: BiliSession;
}

export interface BiliAuthGateway {
  createChallenge(): Promise<QrLoginChallenge>;
  poll(qrCodeKey: string): Promise<QrLoginPollResult>;
  cancelCurrent(): void;
}
