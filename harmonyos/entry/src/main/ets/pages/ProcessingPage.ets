import { AppRoute, NoteRouteParam } from '../app/AppRoutes';
import { appContainer } from '../app/AppContainer';
import { AppColor, AppSpace } from '../core/theme/AppTheme';
import type { GenerationTask } from '../models/GenerationTask';

@Component
export struct ProcessingPage {
  pathStack: NavPathStack = new NavPathStack();
  taskId: string = '';
  @State task: GenerationTask | undefined = undefined;
  @State status: string = '';
  private unsubscribe?: () => void;

  private async load(): Promise<void> {
    try { this.task = await appContainer.generationTasks.getById(this.taskId); }
    catch (_error) { this.status = '读取生成任务失败。'; }
  }
  private async startQueued(): Promise<void> {
    await this.load();
    const current: GenerationTask | undefined = this.task;
    if (current === undefined) return;
    if (current.status === 'queued') appContainer.taskRunner.start(current.id);
  }
  private retry(): void { appContainer.taskRunner.start(this.taskId); }
  private cancel(): void { appContainer.taskRunner.cancel(this.taskId); }
  private openNote(): void {
    const noteId: string = this.task?.noteId || '';
    if (noteId.length > 0)
      this.pathStack.replacePath(new NavPathInfo(AppRoute.Note, new NoteRouteParam(noteId)));
  }
  private canRetry(): boolean {
    return this.task?.status === 'interrupted' ||
      (this.task?.status === 'failed' && this.task?.retryable === true);
  }

  build() {
    NavDestination() {
      Column({ space: 18 }) {
        Row({ space: 8 }) {
          Button('返回').fontSize(13).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
            .onClick(() => this.pathStack.pop())
          Text('生成任务').fontSize(26).fontWeight(FontWeight.Bold).layoutWeight(1)
        }.width('100%')
        Text('离开此页不会取消；应用被系统关闭后可回来重试。')
          .fontSize(12).fontColor(AppColor.Primary).width('100%')

        if (this.task !== undefined) {
          Column({ space: 8 }) {
            Text(this.task?.preview.video.title || '').fontSize(18).fontWeight(FontWeight.Medium)
              .width('100%').maxLines(2)
            Text(`P${this.task?.preview.page.page || 1} · 第 ${this.task?.attempt || 0} 次尝试`)
              .fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
          }.width('100%').padding(AppSpace.Card).backgroundColor(AppColor.Surface)
            .borderRadius(AppSpace.Radius).border({ width: 1, color: AppColor.Border })

          Progress({ value: this.task?.progress || 0, total: 100, type: ProgressType.Linear })
            .width('100%').color(AppColor.Primary)
          Row() {
            Text(this.task?.message || '').fontSize(14).fontColor(AppColor.TextSecondary).layoutWeight(1)
            Text(`${Math.round(this.task?.progress || 0)}%`).fontSize(14).fontColor(AppColor.Primary)
          }.width('100%')
          Blank().layoutWeight(1)
          if (this.task?.status === 'running' || this.task?.status === 'queued') {
            Button('取消生成').width('100%').backgroundColor(AppColor.Control).fontColor(AppColor.Text)
              .onClick(() => this.cancel())
          } else if (this.task?.status === 'succeeded') {
            Button('打开笔记').width('100%').backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
              .onClick(() => this.openNote())
          } else if (this.canRetry()) {
            Button('重新生成').width('100%').backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
              .onClick(() => this.retry())
          } else if (this.task?.status === 'failed') {
            Button('检查模型配置').width('100%').backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
              .onClick(() => this.pathStack.pushPath(new NavPathInfo(AppRoute.Settings, undefined)))
          }
        } else {
          Text(this.status || '任务不存在。').fontSize(14).fontColor(AppColor.Danger)
        }
      }.width('100%').height('100%').padding(AppSpace.Page).backgroundColor(AppColor.Background)
    }.hideTitleBar(true)
      .onShown(() => {
        if (this.unsubscribe !== undefined) this.unsubscribe();
        this.unsubscribe = appContainer.taskRunner.subscribe((updated: GenerationTask): void => {
          if (updated.id === this.taskId) this.load();
        });
        this.startQueued();
      })
      .onHidden(() => { if (this.unsubscribe !== undefined) this.unsubscribe(); })
  }
}
