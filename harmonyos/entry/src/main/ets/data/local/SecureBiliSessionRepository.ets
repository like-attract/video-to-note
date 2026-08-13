import type { BiliSessionRepository } from '../../domain/auth/BiliSessionRepository';
import type { BiliSession } from '../../models/BiliModels';
import { sessionStore } from '../../stores/SessionStore';
import { SecureSecretStore } from './SecureSecretStore';

const SESSION_KEY: string = 'bili.session';

export class SecureBiliSessionRepository implements BiliSessionRepository {
  private readonly secrets: SecureSecretStore;
  private loaded: boolean = false;

  constructor(secrets: SecureSecretStore) { this.secrets = secrets; }

  async getCurrent(): Promise<BiliSession | undefined> {
    const memory: BiliSession | undefined = sessionStore.getSession();
    if (memory !== undefined || this.loaded) return memory;
    this.loaded = true;
    try {
      const encoded: string | undefined = await this.secrets.get(SESSION_KEY);
      if (encoded === undefined) return undefined;
      const session: BiliSession = JSON.parse(encoded) as BiliSession;
      if (session.cookieHeader.length === 0) return undefined;
      sessionStore.setSession(session);
      return session;
    } catch (error) {
      this.loaded = false;
      throw error instanceof Error ? error : new Error('恢复 B站登录凭据失败。');
    }
  }

  async save(session: BiliSession): Promise<void> {
    await this.secrets.set(SESSION_KEY, JSON.stringify(session));
    sessionStore.setSession(session);
    this.loaded = true;
  }

  async clear(): Promise<void> {
    await this.secrets.remove(SESSION_KEY);
    sessionStore.clear();
    this.loaded = true;
  }
}
