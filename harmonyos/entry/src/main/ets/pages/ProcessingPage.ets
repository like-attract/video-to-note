import { AppRoute, NoteRouteParam } from '../app/AppRoutes';
import { appContainer } from '../app/AppContainer';
import { AppError, AppErrorCode, toUserMessage } from '../core/errors/AppError';
import { AppColor, AppSpace } from '../core/theme/AppTheme';
import type { SubtitlePreview } from '../models/BiliModels';
import type { LlmConfig, NoteRecord } from '../models/LlmModels';

@Component
export struct ProcessingPage {
  pathStack: NavPathStack = new NavPathStack();
  @State progress: number = 0;
  @State message: string = '正在准备生成任务…';
  @State isRunning: boolean = false;
  @State canRetry: boolean = false;
  @State videoTitle: string = '';
  @State pageTitle: string = '';
  @State segmentCount: number = 0;
  private disposed: boolean = false;
  private started: boolean = false;
  private cancelRequested: boolean = false;

  aboutToAppear(): void { this.disposed = false; }
  aboutToDisappear(): void {
    this.disposed = true;
    if (this.isRunning) appContainer.generateNote.cancel();
  }

  private async runGeneration(): Promise<void> {
    if (this.isRunning) return;
    this.started = true;
    this.cancelRequested = false;
    this.canRetry = false;
    this.isRunning = true;
    this.progress = 0;
    this.message = '正在读取字幕和模型配置…';
    try {
      const workspace = await appContainer.generation.getWorkspace();
      const preview: SubtitlePreview | undefined = workspace.preview;
      const config: LlmConfig | undefined = workspace.llmConfig;
      if (preview === undefined) throw new AppError(
        AppErrorCode.InvalidInput, '字幕数据不存在，请返回重新获取。');
      if (config === undefined) throw new AppError(
        AppErrorCode.InvalidInput, '模型配置不存在，请返回重新配置。');
      this.videoTitle = preview.video.title;
      this.pageTitle = `P${preview.page.page} ${preview.page.part}`;
      this.segmentCount = preview.segments.length;
      const note: NoteRecord = await appContainer.generateNote.execute(
        config,
        (progress: number, message: string): void => {
          if (!this.disposed) { this.progress = progress; this.message = message; }
        }
      );
      if (this.disposed) return;
      this.progress = 100;
      this.message = '笔记生成完成，正在打开…';
      this.isRunning = false;
      this.pathStack.removeByName(AppRoute.Subtitle);
      this.pathStack.replacePath(new NavPathInfo(AppRoute.Note, new NoteRouteParam(note.id)));
    } catch (error) {
      if (this.disposed) return;
      const cancelled: boolean = this.cancelRequested ||
        (error instanceof AppError && error.code === AppErrorCode.Cancelled);
      this.message = cancelled ? '生成已取消，可以返回修改选项或重新生成。' :
        `生成失败：${toUserMessage(error as Object)}`;
      this.canRetry = true;
    } finally {
      if (!this.disposed) this.isRunning = false;
    }
  }

  private cancel(): void {
    if (!this.isRunning) return;
    this.cancelRequested = true;
    this.message = '正在取消生成…';
    appContainer.generateNote.cancel();
  }

  build() {
    NavDestination() {
      Column({ space: 18 }) {
        Row({ space: 8 }) {
          Button($r('app.string.action_back')).fontSize(13).enabled(!this.isRunning)
            .onClick(() => this.pathStack.pop())
          Text($r('app.string.processing_title')).fontSize(26).fontWeight(FontWeight.Bold).layoutWeight(1)
        }.width('100%')
        Text($r('app.string.flow_step_processing'))
          .fontSize(12).fontColor(AppColor.Primary).fontWeight(FontWeight.Medium).width('100%')

        Column({ space: 8 }) {
          Text(this.videoTitle || '正在读取视频信息…')
            .fontSize(18).fontWeight(FontWeight.Medium).width('100%').maxLines(2)
          if (this.pageTitle.length > 0) {
            Text(`${this.pageTitle} · ${this.segmentCount} 条字幕`)
              .fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
          }
        }.width('100%').padding(AppSpace.Card).backgroundColor(AppColor.Surface)
          .borderRadius(AppSpace.Radius)

        Progress({ value: this.progress, total: 100, type: ProgressType.Linear })
          .width('100%').color(AppColor.Primary)
        Row() {
          Text(this.message).fontSize(14).fontColor(AppColor.TextSecondary).layoutWeight(1)
          Text(`${Math.round(this.progress)}%`).fontSize(14).fontColor(AppColor.Primary)
        }.width('100%')

        Blank().layoutWeight(1)
        if (this.isRunning) {
          Button($r('app.string.action_cancel_generation')).width('100%')
            .backgroundColor(AppColor.Control).fontColor(AppColor.Text).onClick(() => this.cancel())
        } else if (this.canRetry) {
          Button($r('app.string.action_retry_generation')).width('100%')
            .onClick(() => this.runGeneration())
          Button($r('app.string.action_back_to_config')).width('100%')
            .backgroundColor(AppColor.Control).fontColor(AppColor.Text).onClick(() => this.pathStack.pop())
        }
      }
      .width('100%').height('100%').padding(AppSpace.Page).backgroundColor(AppColor.Background)
    }
    .hideTitleBar(true)
    .onShown(() => {
      this.disposed = false;
      if (!this.started) this.runGeneration();
    })
    .onHidden(() => {
      if (this.isRunning) {
        this.cancelRequested = true;
        appContainer.generateNote.cancel();
      }
    })
  }
}
