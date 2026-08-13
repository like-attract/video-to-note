import type { LlmCompatibilityMode, LlmConfig } from './LlmModels';

export function normalizeCompatibilityMode(value: string): LlmCompatibilityMode {
  if (value === 'generic' || value === 'deepseek' || value === 'openai' ||
    value === 'glm' || value === 'qwen') return value;
  return 'auto';
}

export function resolveCompatibilityMode(config: LlmConfig): LlmCompatibilityMode {
  if (config.compatibilityMode !== 'auto') return config.compatibilityMode;
  const provider: string = config.providerName.trim().toLowerCase();
  if (provider === 'deepseek' || provider === 'openai' || provider === 'glm' || provider === 'qwen')
    return provider as LlmCompatibilityMode;
  const model: string = config.model.trim().toLowerCase();
  const baseUrl: string = config.baseUrl.trim().toLowerCase();
  if (model.includes('deepseek') || baseUrl.includes('deepseek')) return 'deepseek';
  return 'generic';
}

export function usesDeepSeekCompatibility(config: LlmConfig): boolean {
  return resolveCompatibilityMode(config) === 'deepseek';
}
