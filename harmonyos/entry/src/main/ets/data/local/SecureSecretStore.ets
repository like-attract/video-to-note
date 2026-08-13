import { common } from '@kit.AbilityKit';
import { HuksSecretCipher, SecureEnvelope } from '../../core/security/HuksSecretCipher';
import { PreferenceEntry, PreferencesStore } from './PreferencesStore';

export class SecureSecretStore {
  private readonly preferences: PreferencesStore;
  private readonly cipher: HuksSecretCipher;
  private operation: Promise<void> = Promise.resolve();

  constructor(context: common.Context, preferencesName: string = 'videotono_secrets',
    keyAlias: string = 'videotono.credentials.v1') {
    this.preferences = new PreferencesStore(context, preferencesName);
    this.cipher = new HuksSecretCipher(keyAlias);
  }

  async get(key: string): Promise<string | undefined> {
    try { await this.operation; } catch (_error) {}
    const encoded: string = await this.preferences.getString(key);
    if (encoded.length === 0) return undefined;
    const envelope: SecureEnvelope = JSON.parse(encoded) as SecureEnvelope;
    try {
      return await this.cipher.decryptText(envelope);
    } catch (error) {
      if (this.cipher.consumeKeyCreatedFlag()) {
        await this.preferences.clear();
        return undefined;
      }
      throw error instanceof Error ? error : new Error('读取安全凭据失败。');
    }
  }

  async set(key: string, value: string): Promise<void> {
    await this.enqueue(() => this.writeEncrypted(key, value));
  }

  async remove(key: string): Promise<void> {
    await this.enqueue(() => this.preferences.remove(key));
  }

  async clearAndDeleteKey(): Promise<void> {
    await this.enqueue(async () => {
      await this.preferences.clear();
      await this.cipher.deleteKey();
    });
  }

  private async writeEncrypted(key: string, value: string): Promise<void> {
    const envelope: SecureEnvelope = await this.cipher.encryptText(value);
    if (this.cipher.consumeKeyCreatedFlag()) await this.preferences.clear();
    await this.preferences.putStrings([new PreferenceEntry(key, JSON.stringify(envelope))]);
  }

  private enqueue(operation: () => Promise<void>): Promise<void> {
    const next: Promise<void> = this.operation.then(operation, operation);
    this.operation = next;
    return next;
  }
}
