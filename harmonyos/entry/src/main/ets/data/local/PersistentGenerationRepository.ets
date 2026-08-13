import { common } from '@kit.AbilityKit';
import type { SubtitlePreview } from '../../models/BiliModels';
import type { LlmConfig, NoteRecord } from '../../models/LlmModels';
import type { GenerationRepository, GenerationWorkspace } from '../../domain/generation/GenerationRepository';
import { workStore } from '../../stores/WorkStore';
import { SecureSecretStore } from './SecureSecretStore';

// The generation workspace is intentionally process-local. Long-lived LLM settings
// are owned by PersistentLlmProfileRepository; keeping them separate prevents a run
// from silently overwriting the user's default profile.
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
