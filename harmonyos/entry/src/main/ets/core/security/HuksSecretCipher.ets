import { huks } from '@kit.UniversalKeystoreKit';
import { cryptoFramework } from '@kit.CryptoArchitectureKit';
import { util } from '@kit.ArkTS';
import { BusinessError } from '@kit.BasicServicesKit';

const DEFAULT_KEY_ALIAS: string = 'videotono.credentials.v1';
const GCM_TAG_BYTES: number = 16;
const NONCE_BYTES: number = 12;
const HUKS_ITEM_NOT_EXIST: number = 12000011;
const HUKS_KEY_ALREADY_EXIST: number = 12000017;

export class SecureEnvelope {
  version: number = 1;
  nonce: string = '';
  ciphertext: string = '';
  authTag: string = '';
}

export class HuksSecretCipher {
  private readonly base64: util.Base64Helper = new util.Base64Helper();
  private readonly keyAlias: string;
  private keyCreated: boolean = false;

  constructor(keyAlias: string = DEFAULT_KEY_ALIAS) { this.keyAlias = keyAlias; }

  consumeKeyCreatedFlag(): boolean {
    const created: boolean = this.keyCreated;
    this.keyCreated = false;
    return created;
  }

  async encryptText(value: string): Promise<SecureEnvelope> {
    try {
      await this.ensureKey();
      const nonce: Uint8Array = cryptoFramework.createRandom().generateRandomSync(NONCE_BYTES).data;
      const properties: huks.HuksParam[] = this.encryptProperties(nonce);
      const handle: huks.HuksSessionHandle = await huks.initSession(this.keyAlias, { properties });
      try {
        const plainBytes: Uint8Array = new util.TextEncoder('utf-8').encodeInto(value);
        const result: huks.HuksReturnResult = await huks.finishSession(
          handle.handle, { properties, inData: plainBytes });
        const output: Uint8Array | undefined = result.outData;
        if (output === undefined || output.length < GCM_TAG_BYTES) {
          throw new Error('HUKS 返回了无效的 AES-GCM 密文。');
        }
        const envelope: SecureEnvelope = new SecureEnvelope();
        envelope.nonce = this.base64.encodeToStringSync(nonce);
        envelope.ciphertext = this.base64.encodeToStringSync(output.slice(0, output.length - GCM_TAG_BYTES));
        envelope.authTag = this.base64.encodeToStringSync(output.slice(output.length - GCM_TAG_BYTES));
        return envelope;
      } catch (error) {
        await this.abortQuietly(handle.handle);
        throw this.readableError(error as Object, 'AES-GCM 加密会话失败');
      }
    } catch (error) {
      throw this.readableError(error as Object, 'HUKS 加密失败');
    }
  }

  async decryptText(envelope: SecureEnvelope): Promise<string> {
    try {
      await this.ensureKey();
      const nonce: Uint8Array = this.base64.decodeSync(envelope.nonce);
      const authTag: Uint8Array = this.base64.decodeSync(envelope.authTag);
      const properties: huks.HuksParam[] = this.decryptProperties(nonce, authTag);
      const handle: huks.HuksSessionHandle = await huks.initSession(this.keyAlias, { properties });
      try {
        const result: huks.HuksReturnResult = await huks.finishSession(handle.handle, {
          properties,
          inData: this.base64.decodeSync(envelope.ciphertext)
        });
        if (result.outData === undefined) throw new Error('HUKS 未返回明文。');
        return new util.TextDecoder('utf-8').decodeToString(result.outData);
      } catch (error) {
        await this.abortQuietly(handle.handle);
        throw this.readableError(error as Object, 'AES-GCM 解密会话失败');
      }
    } catch (error) {
      throw this.readableError(error as Object, 'HUKS 解密失败');
    }
  }

  async deleteKey(): Promise<void> {
    const options: huks.HuksOptions = { properties: [] };
    try {
      if (await huks.isKeyItemExist(this.keyAlias, options)) {
        await huks.deleteKeyItem(this.keyAlias, options);
      }
    } catch (error) {
      const businessError: BusinessError = error as BusinessError;
      if (businessError.code !== HUKS_ITEM_NOT_EXIST) {
        throw this.readableError(error as Object, 'HUKS 删除密钥失败');
      }
    }
  }

  private async ensureKey(): Promise<void> {
    const empty: huks.HuksOptions = { properties: [] };
    let exists: boolean = false;
    try {
      exists = await huks.isKeyItemExist(this.keyAlias, empty);
    } catch (error) {
      const businessError: BusinessError = error as BusinessError;
      if (businessError.code !== HUKS_ITEM_NOT_EXIST) {
        throw this.readableError(error as Object, 'HUKS 查询密钥失败');
      }
    }
    if (exists) return;
    try {
      await huks.generateKeyItem(this.keyAlias, { properties: this.keyProperties() });
      this.keyCreated = true;
    } catch (error) {
      const businessError: BusinessError = error as BusinessError;
      if (businessError.code !== HUKS_KEY_ALREADY_EXIST) {
        throw this.readableError(error as Object, 'HUKS 创建密钥失败');
      }
    }
  }

  private keyProperties(): huks.HuksParam[] {
    return [
      { tag: huks.HuksTag.HUKS_TAG_ALGORITHM, value: huks.HuksKeyAlg.HUKS_ALG_AES },
      { tag: huks.HuksTag.HUKS_TAG_KEY_SIZE, value: huks.HuksKeySize.HUKS_AES_KEY_SIZE_256 },
      { tag: huks.HuksTag.HUKS_TAG_PURPOSE,
        value: huks.HuksKeyPurpose.HUKS_KEY_PURPOSE_ENCRYPT |
          huks.HuksKeyPurpose.HUKS_KEY_PURPOSE_DECRYPT },
      { tag: huks.HuksTag.HUKS_TAG_BLOCK_MODE, value: huks.HuksCipherMode.HUKS_MODE_GCM },
      { tag: huks.HuksTag.HUKS_TAG_PADDING, value: huks.HuksKeyPadding.HUKS_PADDING_NONE }
    ];
  }

  private encryptProperties(nonce: Uint8Array): huks.HuksParam[] {
    return [
      { tag: huks.HuksTag.HUKS_TAG_ALGORITHM, value: huks.HuksKeyAlg.HUKS_ALG_AES },
      { tag: huks.HuksTag.HUKS_TAG_KEY_SIZE, value: huks.HuksKeySize.HUKS_AES_KEY_SIZE_256 },
      { tag: huks.HuksTag.HUKS_TAG_PURPOSE, value: huks.HuksKeyPurpose.HUKS_KEY_PURPOSE_ENCRYPT },
      { tag: huks.HuksTag.HUKS_TAG_BLOCK_MODE, value: huks.HuksCipherMode.HUKS_MODE_GCM },
      { tag: huks.HuksTag.HUKS_TAG_PADDING, value: huks.HuksKeyPadding.HUKS_PADDING_NONE },
      { tag: huks.HuksTag.HUKS_TAG_NONCE, value: nonce }
    ];
  }

  private decryptProperties(nonce: Uint8Array, authTag: Uint8Array): huks.HuksParam[] {
    return [
      { tag: huks.HuksTag.HUKS_TAG_ALGORITHM, value: huks.HuksKeyAlg.HUKS_ALG_AES },
      { tag: huks.HuksTag.HUKS_TAG_KEY_SIZE, value: huks.HuksKeySize.HUKS_AES_KEY_SIZE_256 },
      { tag: huks.HuksTag.HUKS_TAG_PURPOSE, value: huks.HuksKeyPurpose.HUKS_KEY_PURPOSE_DECRYPT },
      { tag: huks.HuksTag.HUKS_TAG_BLOCK_MODE, value: huks.HuksCipherMode.HUKS_MODE_GCM },
      { tag: huks.HuksTag.HUKS_TAG_PADDING, value: huks.HuksKeyPadding.HUKS_PADDING_NONE },
      { tag: huks.HuksTag.HUKS_TAG_NONCE, value: nonce },
      { tag: huks.HuksTag.HUKS_TAG_AE_TAG, value: authTag }
    ];
  }

  private readableError(error: Object, operation: string): Error {
    if (error instanceof Error && error.message.trim().length > 0) {
      return new Error(`${operation}：${error.message}`);
    }
    const businessError: BusinessError = error as BusinessError;
    return new Error(`${operation}（错误码 ${businessError.code}）`);
  }

  private async abortQuietly(handle: number): Promise<void> {
    try { await huks.abortSession(handle, { properties: [] }); } catch (_error) {}
  }
}
