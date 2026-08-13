import { describe, expect, it } from '@ohos/hypium';
import { parseOpenAiStreamResponse } from '../main/ets/services/OpenAiSseParser';

export default function openAiSseParserTest(): void {
  describe('OpenAiSseParser', () => {
    it('joins content and ignores reasoning content', 0, () => {
      const raw: string =
        'data: {"choices":[{"delta":{"reasoning_content":"先思考"}}]}\r\n\r\n' +
        'data: {"choices":[{"delta":{"content":"忠实"}}]}\n\n' +
        'data: {"choices":[{"delta":{"content":"详细"},"finish_reason":"stop"}]}\n\n' +
        'data: [DONE]\n\n';
      const parsed = parseOpenAiStreamResponse(raw);
      expect(parsed.content).assertEqual('忠实详细');
      expect(parsed.finishReason).assertEqual('stop');
    });

    it('accepts a non-stream fallback response', 0, () => {
      const parsed = parseOpenAiStreamResponse(
        '{"choices":[{"message":{"content":"fallback data: value"},"finish_reason":"stop"}]}'
      );
      expect(parsed.content).assertEqual('fallback data: value');
    });

    it('surfaces an error payload', 0, () => {
      let message: string = '';
      try {
        parseOpenAiStreamResponse('{"error":{"message":"relay rejected stream"}}');
      } catch (error) {
        message = error instanceof Error ? error.message : '';
      }
      expect(message).assertEqual('relay rejected stream');
    });
  });
}
