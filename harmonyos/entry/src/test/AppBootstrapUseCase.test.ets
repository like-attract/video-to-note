import { describe, expect, it } from '@ohos/hypium';
import type { BiliSessionRepository } from '../main/ets/domain/auth/BiliSessionRepository';
import type {
  GenerationRepository, GenerationWorkspace
} from '../main/ets/domain/generation/GenerationRepository';
import { AppBootstrapState, AppBootstrapUseCase } from '../main/ets/domain/startup/AppBootstrapUseCase';
import type { BiliSession, SubtitlePreview } from '../main/ets/models/BiliModels';
import type { LlmConfig, NoteRecord } from '../main/ets/models/LlmModels';

class BrokenSessionRepository implements BiliSessionRepository {
  cleared: boolean = false;
  async getCurrent(): Promise<BiliSession | undefined> { throw new Error('decrypt failed'); }
  async save(_session: BiliSession): Promise<void> {}
  async clear(): Promise<void> { this.cleared = true; }
}

class StableGenerationRepository implements GenerationRepository {
  loaded: boolean = false;
  async getWorkspace(): Promise<GenerationWorkspace> { this.loaded = true; return {}; }
  async savePreview(_preview: SubtitlePreview): Promise<void> {}
  async saveLlmConfig(_config: LlmConfig): Promise<void> {}
  async saveNote(_note: NoteRecord): Promise<void> {}
  async clearTransient(): Promise<void> {}
  async clearAll(): Promise<void> {}
}

export default function appBootstrapUseCaseTest(): void {
  describe('AppBootstrapUseCase', () => {
    it('dropsUnreadableSessionWithoutBlockingStartup', 0, async () => {
      const sessions: BrokenSessionRepository = new BrokenSessionRepository();
      const generation: StableGenerationRepository = new StableGenerationRepository();
      const state: AppBootstrapState =
        await new AppBootstrapUseCase(sessions, generation).restore();
      expect(state.hasSession).assertFalse();
      expect(state.recoveryMessage.length > 0).assertTrue();
      expect(sessions.cleared).assertTrue();
      expect(generation.loaded).assertTrue();
    });
  });
}
