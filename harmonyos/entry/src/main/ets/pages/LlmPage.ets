import { AppRoute } from '../app/AppRoutes';
import { appContainer } from '../app/AppContainer';
import { toUserMessage } from '../core/errors/AppError';
import { AppColor, AppSpace } from '../core/theme/AppTheme';
import type { LlmConfig, NoteStyle, ReasoningEffort } from '../models/LlmModels';
import { CUSTOM_MODEL_ID, getProviderPreset, LLM_PROVIDERS, ModelPreset,
  ProviderPreset } from '../models/LlmPresets';
import { LlmConfigState, LlmConfigViewModel } from '../features/generation/LlmConfigViewModel';

class LlmSelectOption {
  value: string;
  constructor(value: string) { this.value = value; }
}

@Component
export struct LlmPage {
  pathStack: NavPathStack = new NavPathStack();
  @State baseUrl: string = '';
  @State model: string = '';
  @State modelChoice: string = CUSTOM_MODEL_ID;
  @State apiKey: string = '';
  @State rememberApiKey: boolean = false;
  @State providerName: string = 'custom';
  @State style: NoteStyle = 'faithful';
  @State reasoningEffort: ReasoningEffort = 'auto';
  @State status: string = '配置 OpenAI-compatible LLM；API Key 仅保存在当前进程内存。';
  @State isPreparing: boolean = false;
  private readonly configViewModel: LlmConfigViewModel = new LlmConfigViewModel();
  private disposed: boolean = false;

  aboutToAppear(): void {
    this.disposed = false;
    this.restoreConfig();
  }
  aboutToDisappear(): void { this.disposed = true; }

  private async restoreConfig(): Promise<void> {
    try {
      const config: LlmConfig | undefined = (await appContainer.generation.getWorkspace()).llmConfig;
      if (this.disposed) return;
      const state: LlmConfigState = this.configViewModel.restore(config);
      this.applyConfigState(state);
      if (config !== undefined) {
        this.apiKey = config.apiKey;
        this.rememberApiKey = config.rememberApiKey === true;
      }
    } catch (error) {
      if (!this.disposed) this.status = `恢复模型配置失败：${toUserMessage(error as Object)}`;
    }
  }

  private currentConfigState(): LlmConfigState {
    const state: LlmConfigState = new LlmConfigState();
    state.providerName = this.providerName; state.baseUrl = this.baseUrl;
    state.model = this.model; state.modelChoice = this.modelChoice;
    state.style = this.style; state.reasoningEffort = this.reasoningEffort;
    return state;
  }

  private applyConfigState(state: LlmConfigState): void {
    this.providerName = state.providerName; this.baseUrl = state.baseUrl;
    this.model = state.model; this.modelChoice = state.modelChoice;
    this.style = state.style; this.reasoningEffort = state.reasoningEffort;
  }

  private applyProvider(provider: string): void {
    this.applyConfigState(this.configViewModel.selectProvider(provider, this.currentConfigState()));
  }

  private modelOptions(): LlmSelectOption[] {
    const options: LlmSelectOption[] = getProviderPreset(this.providerName).models.map((item: ModelPreset) => {
      return new LlmSelectOption(item.label);
    });
    options.push(new LlmSelectOption('手动输入模型 ID…'));
    return options;
  }

  private selectedModelIndex(): number {
    return this.configViewModel.selectedModelIndex(this.providerName, this.modelChoice);
  }

  private selectedModelLabel(): string {
    const models: ModelPreset[] = getProviderPreset(this.providerName).models;
    const index: number = models.findIndex((item: ModelPreset) => item.id === this.modelChoice);
    if (index >= 0) return models[index].label;
    return this.model.length > 0 ? this.model : '选择模型';
  }

  private selectModel(index: number): void {
    this.applyConfigState(this.configViewModel.selectModel(index, this.currentConfigState()));
  }

  private async beginGeneration(): Promise<void> {
    const config: LlmConfig = this.configViewModel.buildConfig(
      this.currentConfigState(), this.apiKey, this.rememberApiKey);
    const validation: string = this.configViewModel.validationMessage(config);
    if (validation.length > 0) { this.status = validation; return; }
    this.isPreparing = true; this.status = '正在保存配置并准备生成…';
    try {
      if ((await appContainer.generation.getWorkspace()).preview === undefined) {
        this.status = '字幕数据不存在，请返回重新获取。';
        return;
      }
      await appContainer.generation.saveLlmConfig(config);
      if (this.disposed) return;
      this.isPreparing = false;
      this.pathStack.pushPath(new NavPathInfo(AppRoute.Processing, undefined));
    } catch (error) {
      if (!this.disposed) this.status = `准备生成失败：${toUserMessage(error)}`;
    } finally {
      if (!this.disposed) this.isPreparing = false;
    }
  }

  private async saveConfig(): Promise<void> {
    try {
      const config: LlmConfig = this.configViewModel.buildConfig(
        this.currentConfigState(), this.apiKey, this.rememberApiKey);
      await appContainer.generation.saveLlmConfig(config);
      if (!this.disposed) this.status = this.rememberApiKey ?
        '配置已保存，API Key 已使用 HUKS 加密。' : '配置已保存，已删除本机 API Key。';
    } catch (error) {
      if (!this.disposed) this.status = `保存配置失败：${toUserMessage(error as Object)}`;
    }
  }

  private async disableApiKeyPersistence(): Promise<void> {
    this.rememberApiKey = false;
    await this.saveConfig();
  }

  build() {
    NavDestination() {
      Scroll() {
      Column({ space: 14 }) {
      Row({ space: 8 }) {
        Button($r('app.string.action_back')).fontSize(13).onClick(() => this.pathStack.pop())
        Text($r('app.string.llm_title')).fontSize(26).fontWeight(FontWeight.Bold).layoutWeight(1)
      }.width('100%')
      Text($r('app.string.flow_step_llm'))
        .fontSize(12).fontColor(AppColor.Primary).fontWeight(FontWeight.Medium).width('100%')
      Text($r('app.string.llm_provider')).fontSize(14).fontWeight(FontWeight.Medium).width('100%')
      Scroll() {
        Row({ space: 6 }) {
          ForEach(LLM_PROVIDERS, (provider: ProviderPreset) => {
            Button(provider.label).fontSize(12).type(ButtonType.Capsule)
              .backgroundColor(this.providerName === provider.id ? AppColor.Primary : AppColor.Control)
              .fontColor(this.providerName === provider.id ? AppColor.Surface : AppColor.Text)
              .onClick(() => { this.applyProvider(provider.id); })
          }, (provider: ProviderPreset) => provider.id)
        }
      }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
      Text($r('app.string.llm_provider_help'))
        .fontSize(11).fontColor(AppColor.TextMuted).width('100%')
      TextInput({ text: this.baseUrl, placeholder: $r('app.string.llm_base_url_placeholder') })
        .onChange((value: string) => { this.baseUrl = value; }).width('100%')
      Select(this.modelOptions()).selected(this.selectedModelIndex())
        .value(this.selectedModelLabel()).width('100%')
        .onSelect((index: number) => { this.selectModel(index); })
      if (this.modelChoice === CUSTOM_MODEL_ID) {
        TextInput({ text: this.model, placeholder: $r('app.string.llm_custom_model_placeholder') })
          .onChange((value: string) => { this.model = value; }).width('100%')
      }
      TextInput({ text: this.apiKey, placeholder: $r('app.string.llm_api_key_placeholder') })
        .type(InputType.Password).onChange((value: string) => { this.apiKey = value; }).width('100%')
      Row({ space: 10 }) {
        Toggle({ type: ToggleType.Switch, isOn: this.rememberApiKey })
          .onChange((value: boolean) => {
            if (value) this.rememberApiKey = true;
            else this.disableApiKeyPersistence();
          })
        Text($r('app.string.llm_remember_api_key')).fontSize(13).layoutWeight(1)
      }.width('100%')
      Text($r('app.string.llm_note_style')).fontSize(14).fontWeight(FontWeight.Medium).width('100%')
      Row({ space: 8 }) {
        Button($r('app.string.llm_style_detailed')).layoutWeight(1).onClick(() => { this.style = 'detailed'; })
          .backgroundColor(this.style === 'detailed' ? AppColor.Primary : AppColor.Control)
          .fontColor(this.style === 'detailed' ? AppColor.Surface : AppColor.Text)
        Button($r('app.string.llm_style_faithful')).layoutWeight(1).onClick(() => { this.style = 'faithful'; })
          .backgroundColor(this.style === 'faithful' ? AppColor.Primary : AppColor.Control)
          .fontColor(this.style === 'faithful' ? AppColor.Surface : AppColor.Text)
        Button($r('app.string.llm_style_concise')).layoutWeight(1).onClick(() => { this.style = 'concise'; })
          .backgroundColor(this.style === 'concise' ? AppColor.Primary : AppColor.Control)
          .fontColor(this.style === 'concise' ? AppColor.Surface : AppColor.Text)
      }.width('100%')
      Text($r('app.string.llm_reasoning_effort')).fontSize(14).fontWeight(FontWeight.Medium).width('100%')
      Row({ space: 6 }) {
        ForEach(['auto', 'off', 'high', 'max'], (effort: ReasoningEffort) => {
          Text(effort === 'auto' ? '默认' : effort === 'off' ? '关闭' :
            effort === 'high' ? '高' : '最大')
            .fontSize(12).textAlign(TextAlign.Center)
            .padding({ left: 12, right: 12, top: 7, bottom: 7 }).borderRadius(18)
            .backgroundColor(this.reasoningEffort === effort ? AppColor.Primary : AppColor.Control)
            .fontColor(this.reasoningEffort === effort ? AppColor.Surface : AppColor.Text)
            .onClick(() => { this.reasoningEffort = effort; })
        }, (effort: ReasoningEffort) => effort)
      }.width('100%')
      if (this.providerName === 'deepseek' && this.reasoningEffort === 'auto') {
        Text($r('app.string.llm_deepseek_auto_help'))
          .fontSize(11).fontColor(AppColor.TextSecondary).width('100%')
      }
      Text(this.status).fontSize(14).fontColor(AppColor.TextSecondary).width('100%')
      Button($r('app.string.action_save_llm_config')).width('100%')
        .backgroundColor(AppColor.Control).fontColor(AppColor.Text)
        .enabled(!this.isPreparing).onClick(() => this.saveConfig())
      Button(this.isPreparing ? '正在准备…' : $r('app.string.action_start_generation'))
        .width('100%').enabled(!this.isPreparing)
        .onClick(() => this.beginGeneration())
      Text(this.rememberApiKey ? $r('app.string.llm_secure_save_notice') :
        $r('app.string.llm_memory_only_notice'))
        .fontSize(12).fontColor(AppColor.TextMuted).width('100%')
      }.width('100%').padding(AppSpace.Page)
      }.width('100%').height('100%').backgroundColor(AppColor.Background)
    }
    .hideTitleBar(true)
    .onShown(() => { this.disposed = false; this.restoreConfig(); })
    .onHidden(() => { this.disposed = true; })
  }
}
