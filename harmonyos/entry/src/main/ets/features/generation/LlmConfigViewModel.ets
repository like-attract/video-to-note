import type { LlmConfig, NoteStyle, ReasoningEffort } from '../../models/LlmModels';
import { CUSTOM_MODEL_ID, getProviderPreset, ModelPreset, ProviderPreset } from '../../models/LlmPresets';

export class LlmConfigState {
  providerName: string = 'custom';
  baseUrl: string = '';
  model: string = '';
  modelChoice: string = CUSTOM_MODEL_ID;
  style: NoteStyle = 'faithful';
  reasoningEffort: ReasoningEffort = 'auto';
}

export class LlmConfigViewModel {
  restore(config?: LlmConfig): LlmConfigState {
    if (config === undefined) return this.selectProvider('deepseek', new LlmConfigState());
    const state: LlmConfigState = new LlmConfigState();
    state.providerName = config.providerName;
    state.baseUrl = config.baseUrl;
    state.model = config.model;
    state.modelChoice = this.isPresetModel(config.providerName, config.model) ?
      config.model : CUSTOM_MODEL_ID;
    state.style = config.style;
    state.reasoningEffort = config.reasoningEffort;
    return state;
  }

  selectProvider(provider: string, state: LlmConfigState): LlmConfigState {
    const preset: ProviderPreset = getProviderPreset(provider);
    state.providerName = provider;
    state.baseUrl = preset.baseUrl;
    state.modelChoice = preset.defaultModel;
    state.model = preset.defaultModel === CUSTOM_MODEL_ID ? '' : preset.defaultModel;
    return state;
  }

  selectModel(index: number, state: LlmConfigState): LlmConfigState {
    const models: ModelPreset[] = getProviderPreset(state.providerName).models;
    if (index >= models.length) {
      state.modelChoice = CUSTOM_MODEL_ID;
      if (this.isPresetModel(state.providerName, state.model)) state.model = '';
    } else {
      state.modelChoice = models[index].id;
      state.model = models[index].id;
    }
    return state;
  }

  selectedModelIndex(provider: string, choice: string): number {
    const models: ModelPreset[] = getProviderPreset(provider).models;
    const index: number = models.findIndex((item: ModelPreset) => item.id === choice);
    return index >= 0 ? index : models.length;
  }

  buildConfig(state: LlmConfigState, apiKey: string, rememberApiKey: boolean = false): LlmConfig {
    return {
      providerName: state.providerName,
      baseUrl: state.baseUrl.trim(),
      model: state.model.trim(),
      apiKey: apiKey.trim(),
      rememberApiKey: rememberApiKey,
      style: state.style,
      reasoningEffort: state.reasoningEffort
    };
  }

  validationMessage(config: LlmConfig): string {
    if (config.baseUrl.length === 0) return '请输入模型服务 Base URL。';
    if (!/^https?:\/\//i.test(config.baseUrl)) return 'Base URL 必须以 http:// 或 https:// 开头。';
    if (config.model.length === 0) return '请选择或输入模型 ID。';
    if (config.apiKey.length === 0) return '请输入 API Key。';
    return '';
  }

  private isPresetModel(provider: string, model: string): boolean {
    return getProviderPreset(provider).models.some((item: ModelPreset) => item.id === model);
  }
}
