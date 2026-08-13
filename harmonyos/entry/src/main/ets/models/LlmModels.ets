export type NoteStyle = 'detailed' | 'faithful' | 'concise';
export type ReasoningEffort = 'auto' | 'off' | 'high' | 'max';
export type LlmCompatibilityMode = 'auto' | 'generic' | 'deepseek' | 'openai' | 'glm' | 'qwen';

export interface LlmConfig {
  providerName: string;
  baseUrl: string;
  model: string;
  compatibilityMode: LlmCompatibilityMode;
  apiKey: string;
  rememberApiKey?: boolean;
  style: NoteStyle;
  reasoningEffort: ReasoningEffort;
}

export interface LlmProfile {
  id: string;
  name: string;
  providerName: string;
  baseUrl: string;
  model: string;
  compatibilityMode: LlmCompatibilityMode;
  reasoningEffort: ReasoningEffort;
  hasApiKey: boolean;
}

export interface NoteGenerationMetadata {
  providerName: string;
  model: string;
  style: NoteStyle;
  reasoningEffort: ReasoningEffort;
}

export interface NoteRecord {
  id: string;
  bvid: string;
  cid: number;
  noteTitle: string;
  videoTitle: string;
  markdown: string;
  createdAt: number;
  generation?: NoteGenerationMetadata;
}
