interface StreamDelta {
  content?: string;
  reasoning_content?: string;
}

interface StreamChoice {
  delta?: StreamDelta;
  message?: StreamDelta;
  finish_reason?: string;
}

interface StreamErrorDetail { message?: string; }

interface StreamPayload {
  choices?: StreamChoice[];
  error?: StreamErrorDetail;
  message?: string;
}

export interface OpenAiStreamResult {
  content: string;
  finishReason: string;
}

function serverError(payload: StreamPayload): string {
  return payload.error?.message || payload.message || '';
}

function appendPayload(payload: StreamPayload, parts: string[], finishReasons: string[]): void {
  const detail: string = serverError(payload);
  if (detail.length > 0 && (payload.choices === undefined || payload.choices.length === 0)) {
    throw new Error(detail);
  }
  payload.choices?.forEach((choice: StreamChoice) => {
    const content: string | undefined = choice.delta?.content || choice.message?.content;
    if (content !== undefined && content.length > 0) parts.push(content);
    if (choice.finish_reason !== undefined && choice.finish_reason.length > 0) {
      finishReasons.push(choice.finish_reason);
    }
  });
}

export function parseOpenAiStreamResponse(raw: string): OpenAiStreamResult {
  const trimmed: string = raw.trim();
  if (trimmed.length === 0) return { content: '', finishReason: 'unknown' };

  const parts: string[] = [];
  const finishReasons: string[] = [];
  const normalized: string = raw.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const isEventStream: boolean = normalized.split('\n')
    .some((line: string) => line.startsWith('data:'));
  if (!isEventStream) {
    appendPayload(JSON.parse(trimmed) as StreamPayload, parts, finishReasons);
  } else {
    const events: string[] = normalized.split('\n\n');
    events.forEach((event: string) => {
      const dataLines: string[] = event.split('\n')
        .filter((line: string) => line.startsWith('data:'))
        .map((line: string) => line.slice(5).trimStart());
      if (dataLines.length === 0) return;
      const data: string = dataLines.join('\n').trim();
      if (data.length === 0 || data === '[DONE]') return;
      appendPayload(JSON.parse(data) as StreamPayload, parts, finishReasons);
    });
  }
  return {
    content: parts.join('').trim(),
    finishReason: finishReasons.length > 0 ? finishReasons[finishReasons.length - 1] : 'unknown'
  };
}
