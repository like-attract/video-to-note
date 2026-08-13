import { describe, expect, it } from '@ohos/hypium';
import { CUSTOM_MODEL_ID } from '../main/ets/models/LlmPresets';
import { LlmConfigState, LlmConfigViewModel } from '../main/ets/features/generation/LlmConfigViewModel';

export default function llmConfigViewModelTest(): void {
  describe('LlmConfigViewModel', () => {
    it('applies provider defaults', 0, () => {
      const viewModel: LlmConfigViewModel = new LlmConfigViewModel();
      const state: LlmConfigState = viewModel.selectProvider('glm', new LlmConfigState());
      expect(state.baseUrl).assertEqual('https://open.bigmodel.cn/api/paas/v4');
      expect(state.model).assertEqual('glm-4.5-flash');
    });
    it('keeps custom model configuration', 0, () => {
      const viewModel: LlmConfigViewModel = new LlmConfigViewModel();
      const state: LlmConfigState = viewModel.restore({
        providerName: 'deepseek', baseUrl: 'https://gateway.example/v1',
        model: 'custom-deepseek', apiKey: '', style: 'faithful', reasoningEffort: 'auto'
      });
      expect(state.modelChoice).assertEqual(CUSTOM_MODEL_ID);
      expect(state.model).assertEqual('custom-deepseek');
    });
    it('validatesRequiredGenerationConfiguration', 0, () => {
      const viewModel: LlmConfigViewModel = new LlmConfigViewModel();
      const state: LlmConfigState = viewModel.selectProvider('deepseek', new LlmConfigState());
      expect(viewModel.validationMessage(viewModel.buildConfig(state, ''))).assertEqual('请输入 API Key。');
      expect(viewModel.validationMessage(viewModel.buildConfig(state, 'sk-test'))).assertEqual('');
      state.baseUrl = 'not-a-url';
      expect(viewModel.validationMessage(viewModel.buildConfig(state, 'sk-test')).length > 0).assertTrue();
    });
  });
}
