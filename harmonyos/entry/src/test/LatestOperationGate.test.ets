import { describe, expect, it } from '@ohos/hypium';
import { LatestOperationGate } from '../main/ets/features/capture/LatestOperationGate';

export default function latestOperationGateTest(): void {
  describe('LatestOperationGate', () => {
    it('acceptsOnlyLatestOperation', 0, () => {
      const gate: LatestOperationGate = new LatestOperationGate();
      const first: number = gate.begin();
      const second: number = gate.begin();
      expect(gate.isCurrent(first)).assertFalse();
      expect(gate.isCurrent(second)).assertTrue();
      gate.invalidate();
      expect(gate.isCurrent(second)).assertFalse();
    });
  });
}
