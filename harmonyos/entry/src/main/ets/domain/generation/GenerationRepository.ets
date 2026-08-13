import type { SubtitlePreview } from '../../models/BiliModels';
import type { LlmConfig, NoteRecord } from '../../models/LlmModels';

export interface GenerationWorkspace {
  preview?: SubtitlePreview;
  llmConfig?: LlmConfig;
  note?: NoteRecord;
}

export interface GenerationRepository {
  getWorkspace(): Promise<GenerationWorkspace>;
  savePreview(preview: SubtitlePreview): Promise<void>;
  saveLlmConfig(config: LlmConfig): Promise<void>;
  saveNote(note: NoteRecord): Promise<void>;
  clearTransient(): Promise<void>;
  clearAll(): Promise<void>;
}
