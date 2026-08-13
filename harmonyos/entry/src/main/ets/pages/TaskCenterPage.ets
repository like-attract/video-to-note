import { AppRoute, NoteRouteParam, TaskRouteParam } from '../app/AppRoutes';
import { appContainer } from '../app/AppContainer';
import { AppColor, AppSpace } from '../core/theme/AppTheme';
import type { GenerationTask } from '../models/GenerationTask';

@Component
export struct TaskCenterPage {
  pathStack: NavPathStack = new NavPathStack();
  @State tasks: GenerationTask[] = [];
  @State status: string = '';
  private unsubscribe?: () => void;

  private async load(): Promise<void> {
    try { this.tasks = await appContainer.generationTasks.list(); }
    catch (_error) { this.status = '读取任务记录失败。'; }
  }
  private open(task: GenerationTask): void {
    if (task.status === 'succeeded' && task.noteId.length > 0) {
      this.pathStack.pushPath(new NavPathInfo(AppRoute.Note, new NoteRouteParam(task.noteId)));
    } else {
      this.pathStack.pushPath(new NavPathInfo(AppRoute.Processing, new TaskRouteParam(task.id)));
    }
  }
  private async remove(taskId: string): Promise<void> {
    await appContainer.generationTasks.remove(taskId); await this.load();
  }
  private statusLabel(task: GenerationTask): string {
    if (task.status === 'queued') return '等待生成';
    if (task.status === 'running') return `生成中 ${Math.round(task.progress)}%`;
    if (task.status === 'succeeded') return '已完成';
    if (task.status === 'interrupted') return '已中断，可重试';
    if (task.status === 'failed') return '生成失败';
    return '已取消';
  }

  build() {
    NavDestination() {
      Column({ space: 14 }) {
        Row({ space: 8 }) {
          Button('返回').fontSize(13).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
            .onClick(() => this.pathStack.pop())
          Column({ space: 2 }) {
            Text('任务中心').fontSize(26).fontWeight(FontWeight.Bold).width('100%')
            Text('离开生成页后，应用运行期间任务会继续').fontSize(12)
              .fontColor(AppColor.TextSecondary).width('100%')
          }.layoutWeight(1)
        }.width('100%')
        if (this.tasks.length === 0) {
          Column({ space: 8 }) {
            Text('暂无生成任务').fontSize(20).fontWeight(FontWeight.Medium)
            Text('从首页新建一篇视频笔记。').fontSize(13).fontColor(AppColor.TextSecondary)
          }.layoutWeight(1).justifyContent(FlexAlign.Center)
        } else {
          List({ space: 10 }) {
            ForEach(this.tasks, (task: GenerationTask) => {
              ListItem() {
                Column({ space: 8 }) {
                  Text(task.preview.video.title).fontSize(17).fontWeight(FontWeight.Medium)
                    .width('100%').maxLines(2)
                  Text(`${this.statusLabel(task)} · ${task.message}`).fontSize(13)
                    .fontColor(AppColor.TextSecondary).width('100%').maxLines(2)
                  if (task.status === 'running') {
                    Progress({ value: task.progress, total: 100, type: ProgressType.Linear })
                      .width('100%').color(AppColor.Primary)
                  }
                  Row({ space: 8 }) {
                    Button(task.status === 'succeeded' ? '打开笔记' : '查看任务').layoutWeight(2)
                      .backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
                      .onClick(() => this.open(task))
                    if (task.status !== 'running' && task.status !== 'queued') {
                      Button('清除记录').layoutWeight(1).backgroundColor(AppColor.Control)
                        .fontColor(AppColor.Text).onClick(() => this.remove(task.id))
                    }
                  }.width('100%')
                }.width('100%').padding(AppSpace.Card).backgroundColor(AppColor.Surface)
                  .borderRadius(AppSpace.Radius).border({ width: 1, color: AppColor.Border })
              }
            }, (task: GenerationTask) => task.id)
          }.width('100%').layoutWeight(1)
        }
        if (this.status.length > 0) Text(this.status).fontSize(12).fontColor(AppColor.Danger)
      }.width('100%').height('100%').padding(AppSpace.Page).backgroundColor(AppColor.Background)
    }.hideTitleBar(true)
      .onShown(() => {
        this.load();
        this.unsubscribe = appContainer.taskRunner.subscribe((_task: GenerationTask): void => { this.load(); });
      })
      .onHidden(() => { if (this.unsubscribe !== undefined) this.unsubscribe(); })
  }
}
