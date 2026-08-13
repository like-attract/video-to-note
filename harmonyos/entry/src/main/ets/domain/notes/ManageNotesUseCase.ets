import { AppError, AppErrorCode } from '../../core/errors/AppError';
import type { NoteRecord } from '../../models/LlmModels';
import type { GenerationRepository } from '../generation/GenerationRepository';
import type { NoteRepository } from './NoteRepository';

export class ManageNotesUseCase {
  private readonly notes: NoteRepository;
  private readonly generation: GenerationRepository;

  constructor(notes: NoteRepository, generation: GenerationRepository) {
    this.notes = notes;
    this.generation = generation;
  }

  list(): Promise<NoteRecord[]> { return this.notes.list(); }

  async open(id: string): Promise<NoteRecord> {
    const note: NoteRecord | undefined = await this.notes.getById(id);
    if (note === undefined) {
      throw new AppError(AppErrorCode.StorageError, '笔记不存在或已被删除。');
    }
    await this.generation.saveNote(note);
    return note;
  }

  async rename(id: string, title: string): Promise<void> {
    const normalized: string = title.trim();
    if (normalized.length === 0) {
      throw new AppError(AppErrorCode.InvalidInput, '笔记标题不能为空。');
    }
    const note: NoteRecord | undefined = await this.notes.getById(id);
    if (note === undefined) {
      throw new AppError(AppErrorCode.StorageError, '笔记不存在或已被删除。');
    }
    await this.notes.renameTitle(id, normalized);
    note.noteTitle = normalized;
    const current: NoteRecord | undefined = (await this.generation.getWorkspace()).note;
    if (current?.id === id) await this.generation.saveNote(note);
  }

  async delete(id: string): Promise<void> {
    await this.notes.deleteById(id);
    const current: NoteRecord | undefined = (await this.generation.getWorkspace()).note;
    if (current?.id === id) await this.generation.clearTransient();
  }
}
