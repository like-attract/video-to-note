import { describe, expect, it } from '@ohos/hypium';
import { AppErrorCode } from '../main/ets/core/errors/AppError';
import { mapRemoteError } from '../main/ets/core/errors/RemoteErrorMapper';

class NetworkKitErrorLike {
  code: number;
  message: string;
  constructor(code: number, message: string = '') { this.code = code; this.message = message; }
}

export default function remoteErrorMapperTest(): void {
  describe('RemoteErrorMapper', () => {
    it('maps timeout as retryable', 0, () => {
      const mapped = mapRemoteError(new Error('request timeout'));
      expect(mapped.code).assertEqual(AppErrorCode.NetworkTimeout);
      expect(mapped.retryable).assertTrue();
    });
    it('maps unauthorized as not retryable', 0, () => {
      const mapped = mapRemoteError(new Error('HTTP 401'));
      expect(mapped.code).assertEqual(AppErrorCode.Unauthorized);
      expect(mapped.retryable).assertFalse();
    });
    it('preserves NetworkKit object message and code', 0, () => {
      const withMessage = mapRemoteError(new NetworkKitErrorLike(2300006, 'socket disconnected'));
      expect(withMessage.message).assertEqual('socket disconnected');
      const codeOnly = mapRemoteError(new NetworkKitErrorLike(2300006));
      expect(codeOnly.message).assertContain('2300006');
    });
  });
}
