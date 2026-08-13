import { AppRoute } from '../app/AppRoutes';
import { appContainer } from '../app/AppContainer';
import { toUserMessage } from '../core/errors/AppError';
import { AppColor, AppSpace } from '../core/theme/AppTheme';
import type { LlmProfile, ReasoningEffort } from '../models/LlmModels';
import { CUSTOM_MODEL_ID, getProviderPreset, LLM_PROVIDERS, ModelPreset,
  ProviderPreset } from '../models/LlmPresets';
import { LlmConfigState, LlmConfigViewModel } from '../features/generation/LlmConfigViewModel';

class ProfileSelectOption {
  value: string;
  constructor(value: string) { this.value = value; }
}

@Component
export struct SettingsPage {
  pathStack: NavPathStack = new NavPathStack();
  @State status: string = '可管理 LLM 配置、登录凭据和本机数据。';
  @State confirmClearAll: boolean = false;
  @State isWorking: boolean = false;
  @State profiles: LlmProfile[] = [];
  @State defaultProfileId: string = '';
  @State deletingProfileId: string = '';
  @State showEditor: boolean = false;
  @State editingId: string = '';
  @State profileName: string = '';
  @State providerName: string = 'deepseek';
  @State baseUrl: string = '';
  @State model: string = '';
  @State modelChoice: string = CUSTOM_MODEL_ID;
  @State apiKey: string = '';
  @State reasoningEffort: ReasoningEffort = 'auto';
  private disposed: boolean = false;
  private readonly configViewModel: LlmConfigViewModel = new LlmConfigViewModel();

  aboutToAppear(): void { this.disposed = false; this.loadProfiles(); }
  aboutToDisappear(): void { this.disposed = true; }

  private async loadProfiles(): Promise<void> {
    try {
      const profiles: LlmProfile[] = await appContainer.llmProfiles.list();
      const defaultId: string = await appContainer.llmProfiles.getDefaultId();
      if (!this.disposed) { this.profiles = profiles; this.defaultProfileId = defaultId; }
    } catch (error) {
      if (!this.disposed) this.status = `读取 LLM 配置失败：${toUserMessage(error as Object)}`;
    }
  }

  private currentConfigState(): LlmConfigState {
    const state: LlmConfigState = new LlmConfigState();
    state.providerName = this.providerName; state.baseUrl = this.baseUrl;
    state.model = this.model; state.modelChoice = this.modelChoice;
    state.reasoningEffort = this.reasoningEffort;
    return state;
  }

  private applyConfigState(state: LlmConfigState): void {
    this.providerName = state.providerName; this.baseUrl = state.baseUrl;
    this.model = state.model; this.modelChoice = state.modelChoice;
    this.reasoningEffort = state.reasoningEffort;
  }

  private startAddProfile(): void {
    const state: LlmConfigState = this.configViewModel.selectProvider('deepseek', new LlmConfigState());
    this.editingId = ''; this.profileName = 'DeepSeek'; this.apiKey = '';
    this.applyConfigState(state); this.showEditor = true; this.deletingProfileId = '';
    this.status = '新增配置的 API Key 将使用 HUKS 加密保存在本机。';
  }

  private startEditProfile(profile: LlmProfile): void {
    this.editingId = profile.id; this.profileName = profile.name; this.apiKey = '';
    this.providerName = profile.providerName; this.baseUrl = profile.baseUrl;
    this.model = profile.model; this.reasoningEffort = profile.reasoningEffort;
    const preset: ProviderPreset = getProviderPreset(profile.providerName);
    this.modelChoice = preset.models.some((item: ModelPreset) => item.id === profile.model) ?
      profile.model : CUSTOM_MODEL_ID;
    this.showEditor = true; this.deletingProfileId = '';
    this.status = '编辑已有配置时，API Key 留空会保留原密钥。';
  }

  private applyProvider(provider: string): void {
    this.applyConfigState(this.configViewModel.selectProvider(provider, this.currentConfigState()));
    if (this.editingId.length === 0 && this.profileName.trim().length === 0)
      this.profileName = getProviderPreset(provider).label;
  }

  private modelOptions(): ProfileSelectOption[] {
    const options: ProfileSelectOption[] = getProviderPreset(this.providerName).models
      .map((item: ModelPreset) => new ProfileSelectOption(item.label));
    options.push(new ProfileSelectOption('手动输入模型 ID…'));
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

  private async saveProfile(): Promise<void> {
    if (this.isWorking) return;
    const name: string = this.profileName.trim();
    const baseUrl: string = this.baseUrl.trim();
    const model: string = this.model.trim();
    if (name.length === 0) { this.status = '请输入配置名称。'; return; }
    if (!/^https?:\/\//i.test(baseUrl)) { this.status = 'Base URL 必须以 http:// 或 https:// 开头。'; return; }
    if (model.length === 0) { this.status = '请选择或输入模型 ID。'; return; }
    if (this.editingId.length === 0 && this.apiKey.trim().length === 0) {
      this.status = '新增配置时必须输入 API Key。'; return;
    }
    const id: string = this.editingId.length > 0 ? this.editingId : `profile-${Date.now()}`;
    const profile: LlmProfile = {
      id: id, name: name, providerName: this.providerName, baseUrl: baseUrl,
      model: model, reasoningEffort: this.reasoningEffort, hasApiKey: this.editingId.length > 0
    };
    this.isWorking = true;
    try {
      await appContainer.llmProfiles.save(profile,
        this.apiKey.trim().length > 0 ? this.apiKey.trim() : undefined);
      if (!this.disposed) {
        this.showEditor = false; this.apiKey = ''; this.status = `LLM 配置“${name}”已保存。`;
        await this.loadProfiles();
      }
    } catch (error) {
      if (!this.disposed) this.status = `保存失败：${toUserMessage(error as Object)}`;
    } finally {
      if (!this.disposed) this.isWorking = false;
    }
  }

  private async makeDefault(profileId: string): Promise<void> {
    try {
      await appContainer.llmProfiles.setDefault(profileId);
      if (!this.disposed) { this.defaultProfileId = profileId; this.status = '默认 LLM 配置已更新。'; }
    } catch (error) {
      if (!this.disposed) this.status = `设置默认项失败：${toUserMessage(error as Object)}`;
    }
  }

  private async removeProfile(profileId: string): Promise<void> {
    if (this.isWorking) return;
    this.isWorking = true;
    try {
      await appContainer.llmProfiles.remove(profileId);
      if (!this.disposed) {
        this.deletingProfileId = ''; this.status = 'LLM 配置及其本机密钥已删除。';
        await this.loadProfiles();
      }
    } catch (error) {
      if (!this.disposed) this.status = `删除失败：${toUserMessage(error as Object)}`;
    } finally {
      if (!this.disposed) this.isWorking = false;
    }
  }

  private async logout(): Promise<void> {
    if (this.isWorking) return;
    this.isWorking = true;
    try {
      await appContainer.login.logout();
      if (!this.disposed) this.pathStack.replacePath(new NavPathInfo(AppRoute.Login, undefined));
    } catch (error) {
      if (!this.disposed) this.status = `退出登录失败：${toUserMessage(error as Object)}`;
    } finally { if (!this.disposed) this.isWorking = false; }
  }

  private async clearAllData(): Promise<void> {
    if (this.isWorking) return;
    const dataManagement = appContainer.dataManagement;
    if (dataManagement === undefined) { this.status = '数据管理服务尚未初始化。'; return; }
    this.isWorking = true;
    try {
      await dataManagement.clearAll();
      if (!this.disposed) {
        this.confirmClearAll = false;
        this.pathStack.replacePath(new NavPathInfo(AppRoute.Login, undefined));
      }
    } catch (error) {
      if (!this.disposed) this.status = `清除数据失败：${toUserMessage(error as Object)}`;
    } finally { if (!this.disposed) this.isWorking = false; }
  }

  build() {
    NavDestination() {
      Scroll() {
        Column({ space: 16 }) {
          Row({ space: 8 }) {
            Button($r('app.string.action_back')).fontSize(13).onClick(() => this.pathStack.pop())
            Text($r('app.string.settings_title')).fontSize(26).fontWeight(FontWeight.Bold).layoutWeight(1)
          }.width('100%')

          Column({ space: 10 }) {
            Row() {
              Text('LLM 配置').fontSize(18).fontWeight(FontWeight.Medium).layoutWeight(1)
              Button('添加').fontSize(13).enabled(!this.isWorking).onClick(() => this.startAddProfile())
            }.width('100%')
            Text('提前保存多个模型服务；生成笔记时直接选择。API Key 使用 HUKS 加密。')
              .fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
            if (this.profiles.length === 0 && !this.showEditor) {
              Text('尚未配置 LLM。添加后即可在字幕页直接生成。')
                .fontSize(14).fontColor(AppColor.TextMuted).width('100%')
            }
            ForEach(this.profiles, (profile: LlmProfile) => {
              Column({ space: 8 }) {
                Row({ space: 8 }) {
                  Text(profile.name).fontSize(16).fontWeight(FontWeight.Medium).layoutWeight(1)
                  if (this.defaultProfileId === profile.id) {
                    Text('默认').fontSize(11).fontColor(AppColor.Primary)
                  }
                }.width('100%')
                Text(`${getProviderPreset(profile.providerName).label} · ${profile.model} · 思考 ${profile.reasoningEffort}`)
                  .fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
                Text(profile.hasApiKey ? '密钥已安全保存' : '缺少密钥，请编辑补充')
                  .fontSize(12).fontColor(profile.hasApiKey ? AppColor.TextMuted : '#C43131').width('100%')
                if (this.deletingProfileId === profile.id) {
                  Text('确定删除此配置和 API Key？').fontSize(13).fontColor('#C43131').width('100%')
                  Row({ space: 8 }) {
                    Button('取消').layoutWeight(1).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
                      .onClick(() => { this.deletingProfileId = ''; })
                    Button('确认删除').layoutWeight(1).backgroundColor('#C43131')
                      .onClick(() => this.removeProfile(profile.id))
                  }.width('100%')
                } else {
                  Row({ space: 8 }) {
                    Button('设为默认').layoutWeight(1).fontSize(12)
                      .enabled(this.defaultProfileId !== profile.id && !this.isWorking)
                      .backgroundColor(AppColor.Control).fontColor(AppColor.Text)
                      .onClick(() => this.makeDefault(profile.id))
                    Button('编辑').layoutWeight(1).fontSize(12)
                      .onClick(() => this.startEditProfile(profile))
                    Button('删除').layoutWeight(1).fontSize(12).backgroundColor('#C43131')
                      .onClick(() => { this.deletingProfileId = profile.id; })
                  }.width('100%')
                }
              }.width('100%').padding(12).backgroundColor(AppColor.Background).borderRadius(AppSpace.Radius)
            }, (profile: LlmProfile) => profile.id)

            if (this.showEditor) {
              Text(this.editingId.length > 0 ? '编辑 LLM 配置' : '新增 LLM 配置')
                .fontSize(16).fontWeight(FontWeight.Medium).width('100%')
              TextInput({ text: this.profileName, placeholder: '配置名称，例如 DeepSeek 高质量' })
                .onChange((value: string) => { this.profileName = value; }).width('100%')
              Scroll() {
                Row({ space: 6 }) {
                  ForEach(LLM_PROVIDERS, (provider: ProviderPreset) => {
                    Button(provider.label).fontSize(12).type(ButtonType.Capsule)
                      .backgroundColor(this.providerName === provider.id ? AppColor.Primary : AppColor.Control)
                      .fontColor(this.providerName === provider.id ? AppColor.Surface : AppColor.Text)
                      .onClick(() => this.applyProvider(provider.id))
                  }, (provider: ProviderPreset) => provider.id)
                }
              }.scrollable(ScrollDirection.Horizontal).scrollBar(BarState.Off).width('100%')
              TextInput({ text: this.baseUrl, placeholder: $r('app.string.llm_base_url_placeholder') })
                .onChange((value: string) => { this.baseUrl = value; }).width('100%')
              Select(this.modelOptions()).selected(this.selectedModelIndex())
                .value(this.selectedModelLabel()).width('100%')
                .onSelect((index: number) => this.selectModel(index))
              if (this.modelChoice === CUSTOM_MODEL_ID) {
                TextInput({ text: this.model, placeholder: $r('app.string.llm_custom_model_placeholder') })
                  .onChange((value: string) => { this.model = value; }).width('100%')
              }
              TextInput({ text: this.apiKey,
                placeholder: this.editingId.length > 0 ? 'API Key（留空保留原密钥）' : 'API Key' })
                .type(InputType.Password).onChange((value: string) => { this.apiKey = value; }).width('100%')
              Text('思考强度').fontSize(14).fontWeight(FontWeight.Medium).width('100%')
              Row({ space: 6 }) {
                ForEach(['auto', 'off', 'high', 'max'], (effort: ReasoningEffort) => {
                  Text(effort === 'auto' ? '默认' : effort === 'off' ? '关闭' :
                    effort === 'high' ? '高' : '最大')
                    .fontSize(11).textAlign(TextAlign.Center)
                    .padding({ left: 12, right: 12, top: 7, bottom: 7 }).borderRadius(18)
                    .backgroundColor(this.reasoningEffort === effort ? AppColor.Primary : AppColor.Control)
                    .fontColor(this.reasoningEffort === effort ? AppColor.Surface : AppColor.Text)
                    .onClick(() => { this.reasoningEffort = effort; })
                }, (effort: ReasoningEffort) => effort)
              }.width('100%')
              Row({ space: 8 }) {
                Button('取消').layoutWeight(1).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
                  .onClick(() => { this.showEditor = false; this.apiKey = ''; })
                Button('保存配置').layoutWeight(1).enabled(!this.isWorking).onClick(() => this.saveProfile())
              }.width('100%')
            }
          }.width('100%').padding(AppSpace.Card).backgroundColor(AppColor.Surface).borderRadius(AppSpace.Radius)

          Column({ space: 10 }) {
            Text($r('app.string.settings_account_title')).fontSize(18).fontWeight(FontWeight.Medium).width('100%')
            Text($r('app.string.settings_logout_help')).fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
            Button($r('app.string.action_logout')).width('100%').enabled(!this.isWorking).onClick(() => this.logout())
          }.width('100%').padding(AppSpace.Card).backgroundColor(AppColor.Surface).borderRadius(AppSpace.Radius)

          Column({ space: 10 }) {
            Text($r('app.string.settings_data_title')).fontSize(18).fontWeight(FontWeight.Medium).width('100%')
            Text($r('app.string.settings_clear_all_help')).fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
            if (this.confirmClearAll) {
              Text($r('app.string.settings_clear_all_confirm')).fontSize(14).fontColor('#C43131').width('100%')
              Row({ space: 8 }) {
                Button($r('app.string.action_cancel')).layoutWeight(1).backgroundColor(AppColor.Control)
                  .fontColor(AppColor.Text).onClick(() => { this.confirmClearAll = false; })
                Button($r('app.string.action_confirm_clear')).layoutWeight(1).backgroundColor('#C43131')
                  .enabled(!this.isWorking).onClick(() => this.clearAllData())
              }.width('100%')
            } else {
              Button($r('app.string.action_clear_all_data')).width('100%').backgroundColor('#C43131')
                .enabled(!this.isWorking).onClick(() => { this.confirmClearAll = true; })
            }
          }.width('100%').padding(AppSpace.Card).backgroundColor(AppColor.Surface).borderRadius(AppSpace.Radius)

          Text(this.status).fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
          if (this.isWorking) LoadingProgress().width(36).height(36).color(AppColor.Primary)
        }.width('100%').padding(AppSpace.Page)
      }.width('100%').height('100%').backgroundColor(AppColor.Background)
    }
    .hideTitleBar(true)
    .onShown(() => { this.disposed = false; this.loadProfiles(); })
    .onHidden(() => { this.disposed = true; })
  }
}
