import { describe, expect, it } from '@ohos/hypium';
import { IncomingShareParser, SharedRecordLike } from '../main/ets/features/share/IncomingShareParser';

export default function incomingShareParserTest(): void {
  describe('IncomingShareParser', () => {
    it('extractsBiliShareTextAndHyperlink', 0, () => {
      const parser: IncomingShareParser = new IncomingShareParser();
      const shareText: SharedRecordLike[] = [{
        utd: 'general.text', content: '【视频标题】 https://b23.tv/AbCd123'
      }];
      const hyperlink: SharedRecordLike[] = [{
        utd: 'general.hyperlink', content: 'https://www.bilibili.com/video/BV1ab411c7mD'
      }];
      expect(parser.extractBiliInput(shareText)).assertEqual(shareText[0].content);
      expect(parser.extractBiliInput(hyperlink)).assertEqual(hyperlink[0].content);
    });

    it('rejectsUnrelatedSharedText', 0, () => {
      const parser: IncomingShareParser = new IncomingShareParser();
      expect(parser.extractBiliInput([{ utd: 'general.text', content: '普通文本' }])).assertEqual('');
    });
  });
}
