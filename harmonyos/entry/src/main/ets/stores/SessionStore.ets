import type { BiliSession } from '../models/BiliModels';

class SessionStore {
  private session?: BiliSession;

  setSession(session: BiliSession): void {
    this.session = session;
  }

  getSession(): BiliSession | undefined {
    return this.session;
  }

  clear(): void {
    this.session = undefined;
  }
}

export const sessionStore: SessionStore = new SessionStore();

