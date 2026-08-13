import { common } from '@kit.AbilityKit';
import { AppError, AppErrorCode } from '../../core/errors/AppError';
import type { LlmConfig, LlmProfile, NoteStyle, ReasoningEffort } from '../../models/LlmModels';
import { normalizeCompatibilityMode } from '../../models/LlmCompatibility';
import type { LlmProfileRepository } from '../../domain/generation/LlmProfileRepository';
import { PreferenceEntry, PreferencesStore } from './PreferencesStore';
import { SecureSecretStore } from './SecureSecretStore';

class ProfileDocument {
  version: number = 2;
  defaultId: string = '';
  profiles: LlmProfile[] = [];
}

class LegacyKey {
  static readonly Provider: string = 'llm.provider';
  static readonly BaseUrl: string = 'llm.baseUrl';
  static readonly Model: string = 'llm.model';
  static readonly Style: string = 'llm.style';
  static readonly Reasoning: string = 'llm.reasoning';
  static readonly RememberApiKey: string = 'llm.rememberApiKey';
  static all(): string[] {
    return [LegacyKey.Provider, LegacyKey.BaseUrl, LegacyKey.Model, LegacyKey.Style,
      LegacyKey.Reasoning, LegacyKey.RememberApiKey];
  }
}

const DOCUMENT_KEY: string = 'profiles.document';
const LEGACY_SECRET: string = 'llm.apiKey';
const LEGACY_ID: string = 'legacy-default';

export class PersistentLlmProfileRepository implements LlmProfileRepository {
  private readonly profiles: PreferencesStore;
  private readonly legacy: PreferencesStore;
  private readonly secrets: SecureSecretStore;
  private migrationPromise?: Promise<void>;

  constructor(context: common.Context, secrets: SecureSecretStore,
    profilesName: string = 'videotono_llm_profiles', legacyName: string = 'videotono_settings') {
    this.profiles = new PreferencesStore(context, profilesName);
    this.legacy = new PreferencesStore(context, legacyName);
    this.secrets = secrets;
  }

  async list(): Promise<LlmProfile[]> {
    await this.ensureMigrated();
    return (await this.readDocument()).profiles.slice();
  }

  async getDefaultId(): Promise<string> {
    await this.ensureMigrated();
    return (await this.readDocument()).defaultId;
  }

  async save(profile: LlmProfile, apiKey?: string): Promise<LlmProfile> {
    await this.ensureMigrated();
    const document: ProfileDocument = await this.readDocument();
    const index: number = document.profiles.findIndex((item: LlmProfile) => item.id === profile.id);
    const existing: LlmProfile | undefined = index >= 0 ? document.profiles[index] : undefined;
    if (index < 0 && document.profiles.length >= 12)
      throw new AppError(AppErrorCode.InvalidInput, '最多保存 12 个 LLM 配置。');
    const replacement: string = apiKey?.trim() || '';
    const hasApiKey: boolean = replacement.length > 0 || existing?.hasApiKey === true;
    if (!hasApiKey) throw new AppError(AppErrorCode.InvalidInput, '请输入 API Key。');
    if (replacement.length > 0) await this.secrets.set(this.secretKey(profile.id), replacement);
    const saved: LlmProfile = {
      id: profile.id, name: profile.name.trim(), providerName: profile.providerName,
      baseUrl: profile.baseUrl.trim(), model: profile.model.trim(),
      compatibilityMode: profile.compatibilityMode,
      reasoningEffort: profile.reasoningEffort, hasApiKey: hasApiKey
    };
    if (saved.name.length === 0) throw new AppError(AppErrorCode.InvalidInput, '请输入配置名称。');
    if (index >= 0) document.profiles[index] = saved; else document.profiles.push(saved);
    if (document.defaultId.length === 0) document.defaultId = saved.id;
    await this.writeDocument(document);
    return saved;
  }

  async setDefault(profileId: string): Promise<void> {
    await this.ensureMigrated();
    const document: ProfileDocument = await this.readDocument();
    if (!document.profiles.some((item: LlmProfile) => item.id === profileId))
      throw new AppError(AppErrorCode.InvalidInput, 'LLM 配置不存在。');
    document.defaultId = profileId;
    await this.writeDocument(document);
  }

  async remove(profileId: string): Promise<void> {
    await this.ensureMigrated();
    const document: ProfileDocument = await this.readDocument();
    document.profiles = document.profiles.filter((item: LlmProfile) => item.id !== profileId);
    if (document.defaultId === profileId)
      document.defaultId = document.profiles.length > 0 ? document.profiles[0].id : '';
    await this.writeDocument(document);
    await this.secrets.remove(this.secretKey(profileId));
  }

  async resolve(profileId: string, style: NoteStyle): Promise<LlmConfig> {
    await this.ensureMigrated();
    const profile: LlmProfile | undefined = (await this.readDocument()).profiles
      .find((item: LlmProfile) => item.id === profileId);
    if (profile === undefined) throw new AppError(AppErrorCode.InvalidInput, 'LLM 配置不存在。');
    const apiKey: string = await this.secrets.get(this.secretKey(profileId)) || '';
    if (apiKey.length === 0)
      throw new AppError(AppErrorCode.InvalidInput, '该配置缺少 API Key，请先在设置中补充。');
    return {
      providerName: profile.providerName, baseUrl: profile.baseUrl, model: profile.model,
      compatibilityMode: profile.compatibilityMode,
      apiKey: apiKey, rememberApiKey: true, style: style, reasoningEffort: profile.reasoningEffort
    };
  }

  async clearAll(): Promise<void> {
    let document: ProfileDocument = new ProfileDocument();
    try {
      await this.ensureMigrated();
      document = await this.readDocument();
    } catch (_error) {
      // Clear-all is also the recovery path for corrupted profile metadata.
    }
    await this.profiles.clear();
    for (let index: number = 0; index < document.profiles.length; index++)
      await this.secrets.remove(this.secretKey(document.profiles[index].id));
    await this.legacy.removeMany(LegacyKey.all());
    await this.secrets.remove(LEGACY_SECRET);
  }

  private ensureMigrated(): Promise<void> {
    if (this.migrationPromise === undefined) this.migrationPromise = this.migrateLegacy();
    return this.migrationPromise;
  }

  private async migrateLegacy(): Promise<void> {
    const encoded: string = await this.profiles.getString(DOCUMENT_KEY);
    if (encoded.length > 0) {
      // A previous migration may have committed the new document before cleanup.
      // Cleanup is safe to repeat and makes the migration crash-resumable.
      await this.legacy.removeMany(LegacyKey.all());
      await this.secrets.remove(LEGACY_SECRET);
      return;
    }
    const provider: string = await this.legacy.getString(LegacyKey.Provider);
    if (provider.length === 0) return;
    const remembered: boolean = await this.legacy.getString(LegacyKey.RememberApiKey, 'false') === 'true';
    const legacyApiKey: string = remembered ? (await this.secrets.get(LEGACY_SECRET) || '') : '';
    if (legacyApiKey.length > 0) await this.secrets.set(this.secretKey(LEGACY_ID), legacyApiKey);
    const profile: LlmProfile = {
      id: LEGACY_ID, name: '已迁移配置', providerName: provider,
      baseUrl: await this.legacy.getString(LegacyKey.BaseUrl),
      model: await this.legacy.getString(LegacyKey.Model),
      compatibilityMode: 'auto',
      reasoningEffort: this.toReasoning(await this.legacy.getString(LegacyKey.Reasoning, 'auto')),
      hasApiKey: legacyApiKey.length > 0
    };
    const document: ProfileDocument = new ProfileDocument();
    document.defaultId = profile.id; document.profiles = [profile];
    await this.writeDocument(document);
    await this.legacy.removeMany(LegacyKey.all());
    await this.secrets.remove(LEGACY_SECRET);
  }

  private async readDocument(): Promise<ProfileDocument> {
    const encoded: string = await this.profiles.getString(DOCUMENT_KEY);
    if (encoded.length === 0) return new ProfileDocument();
    try {
      const parsed: ProfileDocument = JSON.parse(encoded) as ProfileDocument;
      if (!Array.isArray(parsed.profiles)) throw new Error('profiles');
      parsed.profiles.forEach((profile: LlmProfile) => {
        profile.compatibilityMode = normalizeCompatibilityMode(String(profile.compatibilityMode || 'auto'));
        profile.reasoningEffort = this.toReasoning(String(profile.reasoningEffort));
      });
      parsed.version = 2;
      return parsed;
    } catch (_error) {
      throw new AppError(AppErrorCode.ParseError, 'LLM 配置数据损坏，请在设置中清除后重新添加。');
    }
  }

  private writeDocument(document: ProfileDocument): Promise<void> {
    return this.profiles.putStrings([new PreferenceEntry(DOCUMENT_KEY, JSON.stringify(document))]);
  }

  private secretKey(profileId: string): string { return `llm.profile.${profileId}.apiKey`; }
  private toReasoning(value: string): ReasoningEffort {
    if (value === 'off' || value === 'high' || value === 'max')
      return value;
    return 'auto';
  }
}
