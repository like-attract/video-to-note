import type { BiliSession } from '../../models/BiliModels';

export interface BiliSessionRepository {
  getCurrent(): Promise<BiliSession | undefined>;
  save(session: BiliSession): Promise<void>;
  clear(): Promise<void>;
}
