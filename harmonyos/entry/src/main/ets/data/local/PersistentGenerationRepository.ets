import { common } from '@kit.AbilityKit';
import type { SubtitlePreview } from '../../models/BiliModels';
import type { LlmConfig, NoteRecord } from '../../models/LlmModels';
import type { GenerationRepository, GenerationWorkspace } from '../../domain/generation/GenerationRepository';
import { workStore } from '../../stores/WorkStore';
import { SecureSecretStore } from './SecureSecretStore';

// Capture draft workspace remains process-local. Persistent generation history is
// owned separately by RdbGenerationTaskRepository.
export class PersistentGenerationRepository implements GenerationRepository {
  constructor(_context: common.Context, _secrets: SecureSecretStore) {}

  async getWorkspace(): Promise<GenerationWorkspace> {
    return {
      preview: workStore.getPreview(),
      llmConfig: workStore.getLlmConfig(),
      note: workStore.getNote()
    };
  }

  async savePreview(preview: SubtitlePreview): Promise<void> { workStore.setPreview(preview); }
  async saveLlmConfig(config: LlmConfig): Promise<void> { workStore.setLlmConfig(config); }
  async saveNote(note: NoteRecord): Promise<void> { workStore.setNote(note); }
  async clearTransient(): Promise<void> { workStore.clearTransient(); }
  async clearAll(): Promise<void> { workStore.clearAll(); }
}
