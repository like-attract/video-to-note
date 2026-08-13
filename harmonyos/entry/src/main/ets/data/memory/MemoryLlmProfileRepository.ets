import { AppError, AppErrorCode } from '../../core/errors/AppError';
import type { LlmConfig, LlmProfile, NoteStyle } from '../../models/LlmModels';
import type { LlmProfileRepository } from '../../domain/generation/LlmProfileRepository';

export class MemoryLlmProfileRepository implements LlmProfileRepository {
  private profiles: LlmProfile[] = [];
  private apiKeys: Map<string, string> = new Map<string, string>();
  private defaultId: string = '';

  async list(): Promise<LlmProfile[]> { return this.profiles.slice(); }
  async getDefaultId(): Promise<string> { return this.defaultId; }

  async save(profile: LlmProfile, apiKey?: string): Promise<LlmProfile> {
    const index: number = this.profiles.findIndex((item: LlmProfile) => item.id === profile.id);
    const existing: LlmProfile | undefined = index >= 0 ? this.profiles[index] : undefined;
    const hasApiKey: boolean = apiKey !== undefined && apiKey.trim().length > 0 ? true :
      existing?.hasApiKey === true;
    if (!hasApiKey) throw new AppError(AppErrorCode.InvalidInput, '请输入 API Key。');
    const saved: LlmProfile = {
      id: profile.id, name: profile.name, providerName: profile.providerName,
      baseUrl: profile.baseUrl, model: profile.model,
      compatibilityMode: profile.compatibilityMode,
      reasoningEffort: profile.reasoningEffort, hasApiKey: hasApiKey
    };
    if (apiKey !== undefined && apiKey.trim().length > 0) this.apiKeys.set(profile.id, apiKey.trim());
    if (index >= 0) this.profiles[index] = saved; else this.profiles.push(saved);
    if (this.defaultId.length === 0) this.defaultId = saved.id;
    return saved;
  }

  async setDefault(profileId: string): Promise<void> {
    if (!this.profiles.some((item: LlmProfile) => item.id === profileId))
      throw new AppError(AppErrorCode.InvalidInput, 'LLM 配置不存在。');
    this.defaultId = profileId;
  }

  async remove(profileId: string): Promise<void> {
    this.profiles = this.profiles.filter((item: LlmProfile) => item.id !== profileId);
    this.apiKeys.delete(profileId);
    if (this.defaultId === profileId) this.defaultId = this.profiles.length > 0 ? this.profiles[0].id : '';
  }

  async resolve(profileId: string, style: NoteStyle): Promise<LlmConfig> {
    const profile: LlmProfile | undefined = this.profiles.find((item: LlmProfile) => item.id === profileId);
    const apiKey: string | undefined = this.apiKeys.get(profileId);
    if (profile === undefined) throw new AppError(AppErrorCode.InvalidInput, 'LLM 配置不存在。');
    if (apiKey === undefined || apiKey.length === 0)
      throw new AppError(AppErrorCode.InvalidInput, '该配置缺少 API Key，请先在设置中补充。');
    return {
      providerName: profile.providerName, baseUrl: profile.baseUrl, model: profile.model,
      compatibilityMode: profile.compatibilityMode,
      apiKey: apiKey, rememberApiKey: true, style: style, reasoningEffort: profile.reasoningEffort
    };
  }

  async clearAll(): Promise<void> {
    this.profiles = []; this.apiKeys.clear(); this.defaultId = '';
  }
}
