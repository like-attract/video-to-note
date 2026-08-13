import type { BiliSessionRepository } from '../auth/BiliSessionRepository';
import type { GenerationRepository } from '../generation/GenerationRepository';

export class AppBootstrapState {
  hasSession: boolean = false;
  recoveryMessage: string = '';
}

export class AppBootstrapUseCase {
  private readonly sessions: BiliSessionRepository;
  private readonly generation: GenerationRepository;

  constructor(sessions: BiliSessionRepository, generation: GenerationRepository) {
    this.sessions = sessions;
    this.generation = generation;
  }

  async restore(): Promise<AppBootstrapState> {
    const state: AppBootstrapState = new AppBootstrapState();
    try {
      state.hasSession = (await this.sessions.getCurrent()) !== undefined;
    } catch (_error) {
      try { await this.sessions.clear(); } catch (_clearError) {}
      state.hasSession = false;
      state.recoveryMessage = '旧的 B站登录凭据已失效，请重新扫码登录。';
    }
    try {
      await this.generation.getWorkspace();
    } catch (_error) {
      try { await this.generation.clearAll(); } catch (_clearError) {}
      state.recoveryMessage = state.recoveryMessage.length > 0 ?
        `${state.recoveryMessage} LLM 配置恢复失败，已重置。` : 'LLM 配置恢复失败，已重置。';
    }
    return state;
  }
}
