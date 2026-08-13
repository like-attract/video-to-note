import type { SubtitlePreview } from '../models/BiliModels';
import type { LlmConfig, NoteRecord } from '../models/LlmModels';

class WorkStore {
  private preview?: SubtitlePreview;
  private llmConfig?: LlmConfig;
  private note?: NoteRecord;

  setPreview(preview: SubtitlePreview): void { this.preview = preview; }
  getPreview(): SubtitlePreview | undefined { return this.preview; }
  setLlmConfig(config: LlmConfig): void { this.llmConfig = config; }
  getLlmConfig(): LlmConfig | undefined { return this.llmConfig; }
  setNote(note: NoteRecord): void { this.note = note; }
  getNote(): NoteRecord | undefined { return this.note; }
  clearTransient(): void {
    this.preview = undefined;
    this.note = undefined;
  }
  clearAll(): void {
    this.preview = undefined;
    this.llmConfig = undefined;
    this.note = undefined;
  }
}

export const workStore: WorkStore = new WorkStore();
