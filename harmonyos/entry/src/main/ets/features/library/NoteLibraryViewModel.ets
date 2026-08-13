import type { NoteRecord } from '../../models/LlmModels';

export type NoteSortMode = 'newest' | 'oldest' | 'title';

export class NoteLibraryViewModel {
  visible(notes: NoteRecord[], query: string, sortMode: NoteSortMode): NoteRecord[] {
    const normalized: string = query.trim().toLowerCase();
    const filtered: NoteRecord[] = normalized.length === 0 ? notes.slice() :
      notes.filter((note: NoteRecord) => this.matches(note, normalized));
    return filtered.sort((left: NoteRecord, right: NoteRecord) => {
      if (sortMode === 'title') return left.noteTitle.localeCompare(right.noteTitle);
      return sortMode === 'newest' ? right.createdAt - left.createdAt : left.createdAt - right.createdAt;
    });
  }

  excerpt(markdown: string): string {
    const plain: string = markdown
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/^#{1,6}\s+/gm, '')
      .replace(/^\s*[-*>]\s+/gm, '')
      .replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1')
      .replace(/[*_`~]/g, '')
      .replace(/\s+/g, ' ').trim();
    return plain.length > 100 ? `${plain.slice(0, 100)}…` : plain;
  }

  formatDate(timestamp: number): string {
    const date: Date = new Date(timestamp);
    const month: string = (date.getMonth() + 1).toString().padStart(2, '0');
    const day: string = date.getDate().toString().padStart(2, '0');
    const hour: string = date.getHours().toString().padStart(2, '0');
    const minute: string = date.getMinutes().toString().padStart(2, '0');
    return `${date.getFullYear()}-${month}-${day} ${hour}:${minute}`;
  }

  private matches(note: NoteRecord, normalized: string): boolean {
    return note.noteTitle.toLowerCase().includes(normalized) ||
      note.videoTitle.toLowerCase().includes(normalized) ||
      note.bvid.toLowerCase().includes(normalized) ||
      note.markdown.toLowerCase().includes(normalized);
  }
}
