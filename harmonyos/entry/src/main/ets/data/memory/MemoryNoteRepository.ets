import type { NoteRecord } from '../../models/LlmModels';
import type { NoteRepository } from '../../domain/notes/NoteRepository';

export class MemoryNoteRepository implements NoteRepository {
  private readonly notes: NoteRecord[] = [];

  async list(): Promise<NoteRecord[]> { return this.notes.slice(); }
  async getById(id: string): Promise<NoteRecord | undefined> {
    return this.notes.find((item: NoteRecord) => item.id === id);
  }
  async save(note: NoteRecord): Promise<void> {
    const index: number = this.notes.findIndex((item: NoteRecord) => item.id === note.id);
    if (index >= 0) this.notes[index] = note;
    else this.notes.unshift(note);
  }
  async renameTitle(id: string, title: string): Promise<void> {
    const note: NoteRecord | undefined = await this.getById(id);
    if (note !== undefined) note.noteTitle = title;
  }
  async deleteById(id: string): Promise<void> {
    const index: number = this.notes.findIndex((item: NoteRecord) => item.id === id);
    if (index >= 0) this.notes.splice(index, 1);
  }
  async clearAll(): Promise<void> { this.notes.splice(0, this.notes.length); }
}
