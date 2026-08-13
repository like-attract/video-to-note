import type { BiliSessionRepository } from '../auth/BiliSessionRepository';
import type { GenerationRepository } from '../generation/GenerationRepository';
import type { NoteRepository } from '../notes/NoteRepository';
import type { LlmProfileRepository } from '../generation/LlmProfileRepository';
import { SecureSecretStore } from '../../data/local/SecureSecretStore';

export class DataManagementUseCase {
  private readonly sessions: BiliSessionRepository;
  private readonly generation: GenerationRepository;
  private readonly notes: NoteRepository;
  private readonly llmProfiles: LlmProfileRepository;
  private readonly secrets: SecureSecretStore;

  constructor(sessions: BiliSessionRepository, generation: GenerationRepository,
    notes: NoteRepository, llmProfiles: LlmProfileRepository, secrets: SecureSecretStore) {
    this.sessions = sessions;
    this.generation = generation;
    this.notes = notes;
    this.llmProfiles = llmProfiles;
    this.secrets = secrets;
  }

  async clearAll(): Promise<void> {
    await this.notes.clearAll();
    await this.generation.clearAll();
    await this.llmProfiles.clearAll();
    await this.sessions.clear();
    await this.secrets.clearAndDeleteKey();
  }
}
