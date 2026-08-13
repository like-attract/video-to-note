import type { LlmConfig } from './LlmModels';

export function usesDeepSeekCompatibility(config: LlmConfig): boolean {
  const provider: string = config.providerName.trim().toLowerCase();
  const model: string = config.model.trim().toLowerCase();
  const baseUrl: string = config.baseUrl.trim().toLowerCase();
  return provider === 'deepseek' || model.includes('deepseek') || baseUrl.includes('deepseek');
}
