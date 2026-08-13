import { describe, expect, it } from '@ohos/hypium';
import { AppErrorCode } from '../main/ets/core/errors/AppError';
import { mapRemoteError } from '../main/ets/core/errors/RemoteErrorMapper';

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
  });
}
