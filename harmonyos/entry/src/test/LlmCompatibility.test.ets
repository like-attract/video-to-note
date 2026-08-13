import { describe, expect, it } from '@ohos/hypium';
import type { LlmConfig } from '../main/ets/models/LlmModels';
import { usesDeepSeekCompatibility } from '../main/ets/models/LlmCompatibility';

function config(providerName: string, baseUrl: string, model: string): LlmConfig {
  return {
    providerName: providerName,
    baseUrl: baseUrl,
    model: model,
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
  });
}
