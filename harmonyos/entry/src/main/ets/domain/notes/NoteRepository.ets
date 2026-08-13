import type { NoteRecord } from '../../models/LlmModels';

export interface NoteRepository {
  list(): Promise<NoteRecord[]>;
  getById(id: string): Promise<NoteRecord | undefined>;
  save(note: NoteRecord): Promise<void>;
  renameTitle(id: string, title: string): Promise<void>;
  deleteById(id: string): Promise<void>;
  clearAll(): Promise<void>;
}
