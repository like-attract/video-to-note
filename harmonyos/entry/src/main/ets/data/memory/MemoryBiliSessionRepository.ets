import type { BiliSession } from '../../models/BiliModels';
import type { BiliSessionRepository } from '../../domain/auth/BiliSessionRepository';
import { sessionStore } from '../../stores/SessionStore';

export class MemoryBiliSessionRepository implements BiliSessionRepository {
  async getCurrent(): Promise<BiliSession | undefined> { return sessionStore.getSession(); }
  async save(session: BiliSession): Promise<void> { sessionStore.setSession(session); }
  async clear(): Promise<void> { sessionStore.clear(); }
}
