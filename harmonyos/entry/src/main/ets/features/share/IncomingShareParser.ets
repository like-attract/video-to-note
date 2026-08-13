export interface SharedRecordLike {
  utd: string;
  content?: string;
  uri?: string;
  title?: string;
  description?: string;
}

export class IncomingShareParser {
  extractBiliInput(records: SharedRecordLike[]): string {
    for (const record of records) {
      const candidates: string[] = [];
      if (record.content !== undefined) candidates.push(record.content);
      if (record.uri !== undefined) candidates.push(record.uri);
      if (record.description !== undefined) candidates.push(record.description);
      if (record.title !== undefined) candidates.push(record.title);
      for (const candidate of candidates) {
        const normalized: string = candidate.trim();
        if (this.isBiliInput(normalized)) return normalized;
      }
    }
    return '';
  }

  private isBiliInput(input: string): boolean {
    if (/BV[0-9A-Za-z]{10}/i.test(input)) return true;
    return /https?:\/\/(?:[^\s/]+\.)?bilibili\.com(?:[\s/?#]|$)/i.test(input) ||
      /https?:\/\/b23\.tv(?:[\s/?#]|$)/i.test(input);
  }
}
