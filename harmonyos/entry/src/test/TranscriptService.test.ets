import { describe, expect, it } from '@ohos/hypium';
import { TranscriptService } from '../main/ets/services/TranscriptService';

export default function transcriptServiceTest(): void {
  describe('TranscriptService', () => {
    it('formats timestamps', 0, () => {
      const service: TranscriptService = new TranscriptService();
      expect(service.formatTimestamp(95)).assertEqual('01:35');
      expect(service.formatTimestamp(3661)).assertEqual('1:01:01');
    });

    it('formats transcript segments', 0, () => {
      const service: TranscriptService = new TranscriptService();
      expect(service.toTimestampedText([
        { startSeconds: 12, endSeconds: 15, content: ' 开场 ' },
        { startSeconds: 20, endSeconds: 22, content: '' }
      ])).assertEqual('[00:12] 开场');
    });
  });
}

