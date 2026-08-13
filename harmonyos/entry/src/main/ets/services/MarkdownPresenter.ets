export interface MarkdownBlock {
  kind: string;
  text: string;
  level: number;
}

export class MarkdownPresenter {
  parse(markdown: string): MarkdownBlock[] {
    const blocks: MarkdownBlock[] = [];
    const lines: string[] = markdown.replace(/\r\n/g, '\n').split('\n');
    let paragraph: string[] = [];
    let inCode: boolean = false;
    let code: string[] = [];

    const flushParagraph = (): void => {
      if (paragraph.length > 0) {
        blocks.push({ kind: 'paragraph', text: this.inlineText(paragraph.join(' ')), level: 0 });
        paragraph = [];
      }
    };
    const flushCode = (): void => {
      if (code.length > 0) {
        blocks.push({ kind: 'code', text: code.join('\n'), level: 0 });
        code = [];
      }
    };

    lines.forEach((raw: string) => {
      const line: string = raw.trimEnd();
      if (line.trim().startsWith('```')) {
        flushParagraph();
        if (inCode) flushCode();
        inCode = !inCode;
        return;
      }
      if (inCode) {
        code.push(raw);
        return;
      }
      if (line.trim().length === 0) {
        flushParagraph();
        return;
      }
      const heading: RegExpMatchArray | null = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading !== null) {
        flushParagraph();
        blocks.push({ kind: 'heading', text: this.inlineText(heading[2]), level: heading[1].length });
        return;
      }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
        flushParagraph();
        blocks.push({ kind: 'divider', text: '', level: 0 });
        return;
      }
      const bullet: RegExpMatchArray | null = line.match(/^\s*[-*+]\s+(.+)$/);
      if (bullet !== null) {
        flushParagraph();
        blocks.push({ kind: 'bullet', text: this.inlineText(bullet[1]), level: 0 });
        return;
      }
      const ordered: RegExpMatchArray | null = line.match(/^\s*(\d+)\.\s+(.+)$/);
      if (ordered !== null) {
        flushParagraph();
        blocks.push({ kind: 'ordered', text: `${ordered[1]}. ${this.inlineText(ordered[2])}`, level: 0 });
        return;
      }
      const quote: RegExpMatchArray | null = line.match(/^>\s?(.*)$/);
      if (quote !== null) {
        flushParagraph();
        blocks.push({ kind: 'quote', text: this.inlineText(quote[1]), level: 0 });
        return;
      }
      paragraph.push(line.trim());
    });
    flushParagraph();
    if (inCode) flushCode();
    return blocks;
  }

  private inlineText(value: string): string {
    return value
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1 ($2)')
      .replace(/(\*\*|__)(.*?)\1/g, '$2')
      .replace(/(\*|_)(.*?)\1/g, '$2')
      .replace(/`([^`]+)`/g, '$1');
  }
}
