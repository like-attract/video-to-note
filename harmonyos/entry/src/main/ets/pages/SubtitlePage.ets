import { AppRoute, TaskRouteParam } from '../app/AppRoutes';
import { appContainer } from '../app/AppContainer';
import { AppError, AppErrorCode, toUserMessage } from '../core/errors/AppError';
import { AppColor, AppSpace } from '../core/theme/AppTheme';
import type {
  BiliPage, BiliSession, BiliVideo, SubtitlePreview, SubtitleTrack, TranscriptSegment
} from '../models/BiliModels';
import { TranscriptService } from '../services/TranscriptService';
import { LatestOperationGate } from '../features/capture/LatestOperationGate';
import type { LlmProfile, NoteStyle } from '../models/LlmModels';
import type { GenerationTask } from '../models/GenerationTask';

class LlmProfileOption {
  value: string;
  constructor(value: string) { this.value = value; }
}

@Component
export struct SubtitlePage {
  pathStack: NavPathStack = new NavPathStack();
  @State input: string = '';
  @State status: string = '输入 BV、完整链接或手机端分享的 b23.tv 短链接';
  @State isLoading: boolean = false;
  @State video: BiliVideo | undefined = undefined;
  @State pages: BiliPage[] = [];
  @State selectedCid: number = 0;
  @State previewText: string = '';
  @State segmentCount: number = 0;
  @State profiles: LlmProfile[] = [];
  @State selectedProfileId: string = '';
  @State noteStyle: NoteStyle = 'faithful';
  @State isPreparingGeneration: boolean = false;
  @StorageLink('captureDraft') captureDraft: string = '';

  private readonly transcriptService: TranscriptService = new TranscriptService();
  private preview?: SubtitlePreview;
  private disposed: boolean = false;
  private readonly operationGate: LatestOperationGate = new LatestOperationGate();

  aboutToAppear(): void {
    this.disposed = false;
    this.restoreCaptureDraft();
    this.checkSession();
    this.loadProfiles();
  }

  private async loadProfiles(): Promise<void> {
    try {
      const profiles: LlmProfile[] = await appContainer.llmProfiles.list();
      const defaultId: string = await appContainer.llmProfiles.getDefaultId();
      if (this.disposed) return;
      this.profiles = profiles;
      if (!profiles.some((item: LlmProfile) => item.id === this.selectedProfileId)) {
        this.selectedProfileId = profiles.some((item: LlmProfile) => item.id === defaultId) ?
          defaultId : (profiles.length > 0 ? profiles[0].id : '');
      }
    } catch (error) {
      if (!this.disposed) this.status = `读取 LLM 配置失败：${toUserMessage(error as Object)}`;
    }
  }

  private profileOptions(): LlmProfileOption[] {
    return this.profiles.map((profile: LlmProfile) =>
      new LlmProfileOption(`${profile.name} · ${profile.model}`));
  }

  private selectedProfileIndex(): number {
    const index: number = this.profiles.findIndex((profile: LlmProfile) => profile.id === this.selectedProfileId);
    return index >= 0 ? index : 0;
  }

  private selectedProfileLabel(): string {
    const index: number = this.selectedProfileIndex();
    if (index >= 0 && index < this.profiles.length)
      return `${this.profiles[index].name} · ${this.profiles[index].model}`;
    return '选择 LLM 配置';
  }

  private selectProfile(index: number): void {
    if (index >= 0 && index < this.profiles.length) this.selectedProfileId = this.profiles[index].id;
  }

  private async beginGeneration(): Promise<void> {
    if (this.isPreparingGeneration || this.preview === undefined) return;
    if (this.selectedProfileId.length === 0) {
      this.status = '请先在设置中添加 LLM 配置。'; return;
    }
    this.isPreparingGeneration = true;
    this.status = '正在准备生成任务…';
    try {
      const task: GenerationTask = await appContainer.generationTasks.create(
        this.preview, this.selectedProfileId, this.noteStyle);
      if (!this.disposed) this.pathStack.pushPath(
        new NavPathInfo(AppRoute.Processing, new TaskRouteParam(task.id)));
    } catch (error) {
      if (!this.disposed) this.status = `准备生成失败：${toUserMessage(error as Object)}`;
    } finally {
      if (!this.disposed) this.isPreparingGeneration = false;
    }
  }

  private restoreCaptureDraft(): void {
    const draft: string = this.captureDraft.trim();
    if (draft.length === 0) return;
    this.input = draft;
    this.captureDraft = '';
    this.status = '已从系统分享接收 B站链接，确认后获取字幕。';
  }

  private async checkSession(): Promise<void> {
    if (!(await appContainer.capture.hasSession()) && !this.disposed) {
      this.status = '登录会话不存在，请返回重新扫码。';
    }
  }

  aboutToDisappear(): void {
    this.disposed = true;
    this.operationGate.invalidate();
  }

  private async loadVideo(): Promise<void> {
    if (this.isLoading) {
      return;
    }
    appContainer.capture.cancel();
    const operation: number = this.operationGate.begin();
    this.isLoading = true;
    this.previewText = '';
    this.preview = undefined;
    this.segmentCount = 0;
    this.status = '正在获取视频信息…';
    try {
      const video: BiliVideo = await appContainer.capture.loadVideo(this.input);
      if (this.disposed || !this.operationGate.isCurrent(operation)) return;
      this.video = video;
      this.pages = video.pages;
      this.status = video.pages.length > 1 ? '请选择分 P' : '正在获取字幕…';
      if (video.pages.length === 1) {
        await this.loadPage(video.pages[0]);
      }
    } catch (error) {
      if (this.operationGate.isCurrent(operation)) await this.handleLoadError(error as Object);
    } finally {
      if (!this.disposed && this.operationGate.isCurrent(operation)) this.isLoading = false;
    }
  }

  private async loadPage(page: BiliPage): Promise<void> {
    const video: BiliVideo | undefined = this.video;
    if (video === undefined) return;
    appContainer.capture.cancel();
    const operation: number = this.operationGate.begin();
    this.isLoading = true;
    this.selectedCid = page.cid;
    this.previewText = '';
    this.preview = undefined;
    this.status = `正在获取 P${page.page} 字幕列表…`;
    try {
      const loaded: SubtitlePreview = await appContainer.capture.loadSubtitle(video, page);
      if (this.disposed || !this.operationGate.isCurrent(operation)) return;
      const track: SubtitleTrack = loaded.track;
      const segments: TranscriptSegment[] = loaded.segments;
      this.preview = loaded;
      this.segmentCount = segments.length;
      const fullText: string = this.transcriptService.toTimestampedText(segments);
      this.previewText = fullText.length > 6000 ? `${fullText.slice(0, 6000)}\n\n…（预览已截断）` : fullText;
      this.status = `已获取 ${track.displayName}，共 ${segments.length} 条字幕`;
    } catch (error) {
      if (this.operationGate.isCurrent(operation)) await this.handleLoadError(error as Object);
    } finally {
      if (!this.disposed && this.operationGate.isCurrent(operation)) this.isLoading = false;
    }
  }

  private errorMessage(error: Object): string {
    return toUserMessage(error);
  }

  private async handleLoadError(error: Object): Promise<void> {
    if (error instanceof AppError &&
      (error.code === AppErrorCode.SessionExpired || error.code === AppErrorCode.NotLoggedIn)) {
      try { await appContainer.login.logout(); } catch (_clearError) {}
      if (!this.disposed) {
        this.captureDraft = this.input.trim();
        this.status = 'B站登录已失效，请重新扫码。';
        this.pathStack.replacePath(new NavPathInfo(AppRoute.Login, undefined));
      }
      return;
    }
    if (!this.disposed) this.status = this.errorMessage(error);
  }

  private async logout(): Promise<void> {
    appContainer.capture.cancel();
    this.isLoading = true;
    try {
      await appContainer.login.logout();
      if (!this.disposed) this.pathStack.replacePath(new NavPathInfo(AppRoute.Login, undefined));
    } catch (error) {
      if (!this.disposed) this.status = `退出登录失败：${this.errorMessage(error as Object)}`;
    } finally {
      if (!this.disposed) this.isLoading = false;
    }
  }

  build() {
    NavDestination() {
      Column({ space: 12 }) {
      Row({ space: 8 }) {
        Button($r('app.string.action_back')).fontSize(13)
          .backgroundColor(AppColor.Control).fontColor(AppColor.Text).onClick(() => this.pathStack.pop())
        Text($r('app.string.subtitle_title')).fontSize(24).fontWeight(FontWeight.Bold).layoutWeight(1)
        Button($r('app.string.action_settings')).fontSize(13)
          .backgroundColor(AppColor.Control).fontColor(AppColor.Text)
          .onClick(() => this.pathStack.pushPath(new NavPathInfo(AppRoute.Settings, undefined)))
      }.width('100%')

      Text($r('app.string.flow_step_subtitle'))
        .fontSize(12).fontColor(AppColor.Primary).fontWeight(FontWeight.Medium).width('100%')

      TextInput({ text: this.input, placeholder: $r('app.string.subtitle_input_placeholder') })
        .width('100%')
        .onChange((value: string) => { this.input = value; })
      Button($r('app.string.action_load_subtitle'))
        .width('100%').enabled(!this.isLoading && this.input.trim().length > 0)
        .backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
        .onClick(() => this.loadVideo())

      if (this.isLoading) {
        LoadingProgress().width(36).height(36).color(AppColor.Primary)
      }
      Text(this.status).fontSize(14).fontColor(AppColor.TextSecondary).width('100%')

      if (this.video !== undefined) {
        Text(this.video?.title || '').fontSize(18).fontWeight(FontWeight.Medium).width('100%')
        Text(`UP主：${this.video?.ownerName || ''}`).fontSize(13).fontColor(AppColor.TextMuted).width('100%')
      }

      if (this.pages.length > 1) {
        Scroll() {
          Row({ space: 8 }) {
            ForEach(this.pages, (page: BiliPage) => {
              Button(`P${page.page} ${page.part}`)
                .type(ButtonType.Capsule)
                .enabled(!this.isLoading)
                .backgroundColor(this.selectedCid === page.cid ? AppColor.Primary : AppColor.Control)
                .fontColor(this.selectedCid === page.cid ? AppColor.OnPrimary : AppColor.Text)
                .onClick(() => this.loadPage(page))
            }, (page: BiliPage) => page.cid.toString())
          }
        }.scrollable(ScrollDirection.Horizontal).width('100%')
      }

      if (this.previewText.length > 0) {
        Text(`字幕预览（${this.segmentCount} 条）`).fontSize(16).fontWeight(FontWeight.Medium).width('100%')
        TextArea({ text: this.previewText })
          .width('100%').layoutWeight(1)
        if (this.profiles.length > 0) {
          Text('使用已配置的 LLM').fontSize(14).fontWeight(FontWeight.Medium).width('100%')
          Select(this.profileOptions()).selected(this.selectedProfileIndex())
            .value(this.selectedProfileLabel()).width('100%')
            .onSelect((index: number) => this.selectProfile(index))
          Row({ space: 8 }) {
            Button($r('app.string.llm_style_detailed')).layoutWeight(1)
              .backgroundColor(this.noteStyle === 'detailed' ? AppColor.Primary : AppColor.Control)
              .fontColor(this.noteStyle === 'detailed' ? AppColor.OnPrimary : AppColor.Text)
              .onClick(() => { this.noteStyle = 'detailed'; })
            Button($r('app.string.llm_style_faithful')).layoutWeight(1)
              .backgroundColor(this.noteStyle === 'faithful' ? AppColor.Primary : AppColor.Control)
              .fontColor(this.noteStyle === 'faithful' ? AppColor.OnPrimary : AppColor.Text)
              .onClick(() => { this.noteStyle = 'faithful'; })
            Button($r('app.string.llm_style_concise')).layoutWeight(1)
              .backgroundColor(this.noteStyle === 'concise' ? AppColor.Primary : AppColor.Control)
              .fontColor(this.noteStyle === 'concise' ? AppColor.OnPrimary : AppColor.Text)
              .onClick(() => { this.noteStyle = 'concise'; })
          }.width('100%')
          Button(this.isPreparingGeneration ? '正在准备…' : $r('app.string.action_generate_note'))
            .width('100%').enabled(!this.isPreparingGeneration)
            .backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
            .onClick(() => this.beginGeneration())
        } else {
          Text('尚未配置 LLM，请先在设置中添加模型服务与 API Key。')
            .fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
          Button('前往设置 LLM').width('100%')
            .backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
            .onClick(() => this.pathStack.pushPath(new NavPathInfo(AppRoute.Settings, undefined)))
        }
      } else {
        Blank().layoutWeight(1)
      }
      }
      .width('100%').height('100%').padding(AppSpace.Page).backgroundColor(AppColor.Background)
    }
    .hideTitleBar(true)
    .onShown(() => {
      this.disposed = false; this.restoreCaptureDraft(); this.checkSession(); this.loadProfiles();
    })
    .onHidden(() => {
      this.disposed = true;
      this.operationGate.invalidate();
      appContainer.capture.cancel();
    })
  }
}
