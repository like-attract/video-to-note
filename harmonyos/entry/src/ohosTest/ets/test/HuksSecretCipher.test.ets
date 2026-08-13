import { describe, it, expect } from '@ohos/hypium';
import { HuksSecretCipher, SecureEnvelope } from '../../../main/ets/core/security/HuksSecretCipher';

export default function huksSecretCipherTest() {
  describe('HuksSecretCipherTest', () => {
    it('encryptsAndDecryptsUtf8Text', 0, async () => {
      const cipher: HuksSecretCipher = new HuksSecretCipher(`videotono.test.cipher.${Date.now()}`);
      const source: string = 'VideoToNo 安全凭据 round-trip';
      const envelope: SecureEnvelope = await cipher.encryptText(source);
      expect(envelope.nonce.length > 0).assertTrue();
      expect(envelope.authTag.length > 0).assertTrue();
      expect(await cipher.decryptText(envelope)).assertEqual(source);
      envelope.authTag = `${envelope.authTag.startsWith('A') ? 'B' : 'A'}${envelope.authTag.slice(1)}`;
      let tamperRejected: boolean = false;
      try { await cipher.decryptText(envelope); } catch (_error) { tamperRejected = true; }
      expect(tamperRejected).assertTrue();
      await cipher.deleteKey();
    });
  });
}
