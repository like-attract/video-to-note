export type NoteStyle = 'detailed' | 'faithful' | 'concise';
export type ReasoningEffort = 'auto' | 'off' | 'high' | 'max';

export interface LlmConfig {
  providerName: string;
  baseUrl: string;
  model: string;
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
  reasoningEffort: ReasoningEffort;
  hasApiKey: boolean;
}

export interface NoteRecord {
  id: string;
  bvid: string;
  cid: number;
  noteTitle: string;
  videoTitle: string;
  markdown: string;
  createdAt: number;
}
