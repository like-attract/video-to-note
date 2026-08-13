import { describe, expect, it } from '@ohos/hypium';
import type { LlmConfig } from '../main/ets/models/LlmModels';
import type { LlmCompatibilityMode } from '../main/ets/models/LlmModels';
import { resolveCompatibilityMode, usesDeepSeekCompatibility } from '../main/ets/models/LlmCompatibility';

function config(providerName: string, baseUrl: string, model: string,
  compatibilityMode: LlmCompatibilityMode = 'auto'): LlmConfig {
  return {
    providerName: providerName,
    baseUrl: baseUrl,
    model: model,
    compatibilityMode: compatibilityMode,
    apiKey: 'test-key',
    style: 'faithful',
    reasoningEffort: 'auto'
  };
}

export default function llmCompatibilityTest(): void {
  describe('LlmCompatibility', () => {
    it('uses DeepSeek handling for the provider preset', 0, () => {
      expect(usesDeepSeekCompatibility(config(
        'deepseek', 'https://gateway.example/v1', 'provider-alias'
      ))).assertTrue();
    });

    it('uses DeepSeek handling for a relayed DeepSeek model', 0, () => {
      expect(usesDeepSeekCompatibility(config(
        'custom', 'https://gateway.example/v1', 'deepseek-ai/DeepSeek-R1'
      ))).assertTrue();
    });

    it('uses DeepSeek handling for a custom official URL', 0, () => {
      expect(usesDeepSeekCompatibility(config(
        'custom', 'https://api.deepseek.com', 'model-alias'
      ))).assertTrue();
    });

    it('does not change unrelated custom models', 0, () => {
      expect(usesDeepSeekCompatibility(config(
        'custom', 'https://gateway.example/v1', 'other-model'
      ))).assertFalse();
    });

    it('lets explicit DeepSeek mode handle opaque relay aliases', 0, () => {
      expect(resolveCompatibilityMode(config(
        'custom', 'https://gateway.example/v1', 'reasoner-v3', 'deepseek'
      ))).assertEqual('deepseek');
    });

    it('lets generic mode disable a false DeepSeek guess', 0, () => {
      expect(resolveCompatibilityMode(config(
        'custom', 'https://deepseek.example/v1', 'other-model', 'generic'
      ))).assertEqual('generic');
    });

    it('maps custom OpenAI GLM and Qwen relays explicitly', 0, () => {
      expect(resolveCompatibilityMode(config('custom', 'https://relay/v1', 'alias', 'openai')))
        .assertEqual('openai');
      expect(resolveCompatibilityMode(config('custom', 'https://relay/v1', 'alias', 'glm')))
        .assertEqual('glm');
      expect(resolveCompatibilityMode(config('custom', 'https://relay/v1', 'alias', 'qwen')))
        .assertEqual('qwen');
    });
  });
}
