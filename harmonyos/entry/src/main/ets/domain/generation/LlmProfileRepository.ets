import type { LlmConfig, LlmProfile, NoteStyle } from '../../models/LlmModels';

export interface LlmProfileRepository {
  list(): Promise<LlmProfile[]>;
  getDefaultId(): Promise<string>;
  save(profile: LlmProfile, apiKey?: string): Promise<LlmProfile>;
  setDefault(profileId: string): Promise<void>;
  remove(profileId: string): Promise<void>;
  resolve(profileId: string, style: NoteStyle): Promise<LlmConfig>;
  clearAll(): Promise<void>;
}
