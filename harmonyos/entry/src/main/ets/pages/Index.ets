import { AppRoute, NoteRouteParam, TaskRouteParam } from '../app/AppRoutes';
import { LoginPage } from './LoginPage';
import { SubtitlePage } from './SubtitlePage';
import { ProcessingPage } from './ProcessingPage';
import { NotePage } from './NotePage';
import { SettingsPage } from './SettingsPage';
import { TaskCenterPage } from './TaskCenterPage';
import { AppColor, AppSpace } from '../core/theme/AppTheme';
import { appContainer } from '../app/AppContainer';
import { AppBootstrapState } from '../domain/startup/AppBootstrapUseCase';
import { toUserMessage } from '../core/errors/AppError';
import type { NoteRecord } from '../models/LlmModels';
import { NoteLibraryViewModel, NoteSortMode } from '../features/library/NoteLibraryViewModel';

@Entry
@Component
struct Index {
  @State hasSession: boolean = false;
  @State isLoading: boolean = true;
  @State status: string = '';
  @State notes: NoteRecord[] = [];
  @State query: string = '';
  @State sortMode: NoteSortMode = 'newest';
  @State renameId: string = '';
  @State renameValue: string = '';
  @State deleteId: string = '';
  @StorageLink('incomingShare') @Watch('onIncomingShareChanged') incomingShare: string = '';
  @StorageLink('incomingShareError') @Watch('onIncomingShareErrorChanged') incomingShareError: string = '';
  private readonly pathStack: NavPathStack = new NavPathStack();
  private readonly viewModel: NoteLibraryViewModel = new NoteLibraryViewModel();
  private loadInFlight: boolean = false;

  aboutToAppear(): void {
    this.loadLibrary();
    this.consumeIncomingShare();
    this.consumeIncomingShareError();
  }

  private onIncomingShareChanged(): void { this.consumeIncomingShare(); }

  private onIncomingShareErrorChanged(): void { this.consumeIncomingShareError(); }

  private consumeIncomingShare(): void {
    const input: string = this.incomingShare.trim();
    if (input.length === 0) return;
    this.incomingShare = '';
    AppStorage.setOrCreate<string>('captureDraft', input);
    this.openIncomingShare();
  }

  private consumeIncomingShareError(): void {
    const message: string = this.incomingShareError.trim();
    if (message.length === 0) return;
    this.incomingShareError = '';
    this.status = message;
  }

  private async openIncomingShare(): Promise<void> {
    try {
      await appContainer.generation.clearTransient();
      this.hasSession = (await appContainer.sessions.getCurrent()) !== undefined;
      const route: string = this.hasSession ? AppRoute.Subtitle : AppRoute.Login;
      this.status = this.hasSession ? '已接收 B站分享，链接已填入字幕页。' :
        '已接收 B站分享；登录后会自动填入链接。';
      this.pathStack.pushPath(new NavPathInfo(route, undefined));
    } catch (error) {
      this.status = `处理分享内容失败：${toUserMessage(error as Object)}`;
    }
  }

  private async loadLibrary(): Promise<void> {
    if (this.loadInFlight) return;
    this.loadInFlight = true;
    this.isLoading = true;
    this.status = '';
    try {
      const bootstrap: AppBootstrapState = await appContainer.bootstrap.restore();
      this.hasSession = bootstrap.hasSession;
      this.notes = await appContainer.manageNotes.list();
      if (bootstrap.recoveryMessage.length > 0) this.status = bootstrap.recoveryMessage;
    } catch (error) {
      this.status = `加载笔记库失败：${toUserMessage(error as Object)}`;
    } finally {
      this.isLoading = false;
      this.loadInFlight = false;
    }
  }

  private visibleNotes(): NoteRecord[] {
    return this.viewModel.visible(this.notes, this.query, this.sortMode);
  }

  private async createNote(): Promise<void> {
    try {
      await appContainer.generation.clearTransient();
      AppStorage.setOrCreate<string>('captureDraft', '');
      this.hasSession = (await appContainer.sessions.getCurrent()) !== undefined;
      const route: string = this.hasSession ? AppRoute.Subtitle : AppRoute.Login;
      this.pathStack.pushPath(new NavPathInfo(route, undefined));
    } catch (error) {
      this.status = `检查登录状态失败：${toUserMessage(error as Object)}`;
    }
  }

  private async openNote(note: NoteRecord): Promise<void> {
    try {
      await appContainer.manageNotes.open(note.id);
      this.pathStack.pushPath(new NavPathInfo(AppRoute.Note, new NoteRouteParam(note.id)));
    } catch (error) {
      this.status = toUserMessage(error as Object);
      await this.loadLibrary();
    }
  }

  private beginRename(note: NoteRecord): void {
    this.renameId = note.id;
    this.renameValue = note.noteTitle;
    this.deleteId = '';
  }

  private async saveRename(): Promise<void> {
    try {
      await appContainer.manageNotes.rename(this.renameId, this.renameValue);
      this.renameId = '';
      this.status = '笔记标题已更新。';
      this.notes = await appContainer.manageNotes.list();
    } catch (error) {
      this.status = toUserMessage(error as Object);
    }
  }

  private async deleteNote(id: string): Promise<void> {
    try {
      await appContainer.manageNotes.delete(id);
      this.deleteId = '';
      this.status = '笔记已删除。';
      this.notes = await appContainer.manageNotes.list();
    } catch (error) {
      this.status = `删除失败：${toUserMessage(error as Object)}`;
    }
  }

  private noteIdFrom(param: Object): string {
    const noteParam: NoteRouteParam = param as NoteRouteParam;
    return noteParam.noteId || '';
  }
  private taskIdFrom(param: Object): string {
    const taskParam: TaskRouteParam = param as TaskRouteParam;
    return taskParam.taskId || '';
  }

  @Builder
  private pageMap(name: string, param: Object): void {
    if (name === AppRoute.Login) {
      LoginPage({ pathStack: this.pathStack })
    } else if (name === AppRoute.Subtitle) {
      SubtitlePage({ pathStack: this.pathStack })
    } else if (name === AppRoute.Processing) {
      ProcessingPage({ pathStack: this.pathStack, taskId: this.taskIdFrom(param) })
    } else if (name === AppRoute.Note) {
      NotePage({ pathStack: this.pathStack, noteId: this.noteIdFrom(param) })
    } else if (name === AppRoute.Settings) {
      SettingsPage({ pathStack: this.pathStack })
    } else if (name === AppRoute.Tasks) {
      TaskCenterPage({ pathStack: this.pathStack })
    }
  }

  @Builder
  private noteCard(note: NoteRecord): void {
    Column({ space: 9 }) {
      Text(note.noteTitle).fontSize(18).fontWeight(FontWeight.Medium).width('100%').maxLines(2)
      if (note.noteTitle !== note.videoTitle) {
        Text(`来源：${note.videoTitle}`).fontSize(12).fontColor(AppColor.TextMuted).width('100%').maxLines(1)
      }
      Text(this.viewModel.excerpt(note.markdown) || '暂无正文摘要')
        .fontSize(14).fontColor(AppColor.TextSecondary).width('100%').maxLines(3)
      Row({ space: 8 }) {
        Text(this.viewModel.formatDate(note.createdAt))
          .fontSize(11).fontColor(AppColor.TextMuted).layoutWeight(1)
        Text(note.bvid).fontSize(11).fontColor(AppColor.TextMuted)
      }.width('100%')

      if (this.renameId === note.id) {
        TextInput({ text: this.renameValue, placeholder: '输入新标题' })
          .width('100%').onChange((value: string) => { this.renameValue = value; })
          .onSubmit(() => this.saveRename())
        Row({ space: 8 }) {
          Button('取消').layoutWeight(1).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
            .onClick(() => { this.renameId = ''; })
          Button('保存').layoutWeight(1).enabled(this.renameValue.trim().length > 0)
            .backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
            .onClick(() => this.saveRename())
        }.width('100%')
      } else if (this.deleteId === note.id) {
        Text('确定删除这篇笔记吗？此操作无法撤销。')
          .fontSize(13).fontColor(AppColor.Danger).width('100%')
        Row({ space: 8 }) {
          Button('取消').layoutWeight(1).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
            .onClick(() => { this.deleteId = ''; })
          Button('确认删除').layoutWeight(1).backgroundColor(AppColor.Danger).fontColor(AppColor.OnDanger)
            .onClick(() => this.deleteNote(note.id))
        }.width('100%')
      } else {
        Row({ space: 8 }) {
          Button('打开').layoutWeight(2).backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
            .onClick(() => this.openNote(note))
          Button('重命名').layoutWeight(1).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
            .onClick(() => this.beginRename(note))
          Button('删除').layoutWeight(1).backgroundColor(AppColor.Control).fontColor(AppColor.Danger)
            .onClick(() => { this.deleteId = note.id; this.renameId = ''; })
        }.width('100%')
      }
    }
    .width('100%').padding(AppSpace.Card)
    .backgroundColor(AppColor.Surface).borderRadius(AppSpace.Radius)
    .border({ width: 1, color: AppColor.Border })
  }

  build() {
    Navigation(this.pathStack) {
      Column({ space: 12 }) {
        Row({ space: 10 }) {
          Column({ space: 3 }) {
            Text($r('app.string.library_title')).fontSize(28).fontWeight(FontWeight.Bold).width('100%')
            Text(this.hasSession ? 'B站已登录' : '未登录；仍可阅读本地笔记')
              .fontSize(12).fontColor(AppColor.TextSecondary).width('100%')
          }.layoutWeight(1)
          Button('任务').fontSize(13).backgroundColor(AppColor.Control).fontColor(AppColor.Text)
            .onClick(() => this.pathStack.pushPath(new NavPathInfo(AppRoute.Tasks, undefined)))
          Button($r('app.string.action_settings')).fontSize(13)
            .backgroundColor(AppColor.Control).fontColor(AppColor.Text)
            .onClick(() => this.pathStack.pushPath(new NavPathInfo(AppRoute.Settings, undefined)))
        }.width('100%')

        Column({ space: 8 }) {
          Text('把视频变成可回看的知识').fontSize(12).fontColor(AppColor.PrimaryStrong).width('100%')
          Text('从一个视频开始\n构建你的知识库')
            .fontSize(24).fontWeight(FontWeight.Bold).fontColor(AppColor.Text).width('100%')
          Text('提取 B站字幕，使用你的模型生成带时间轴的 Markdown 笔记。')
            .fontSize(13).fontColor(AppColor.TextSecondary).width('100%')
          Button('新建视频笔记').width('100%').borderRadius(AppSpace.ControlRadius)
            .backgroundColor(AppColor.Primary).fontColor(AppColor.OnPrimary)
            .onClick(() => this.createNote())
        }.width('100%').padding(AppSpace.Card).backgroundColor(AppColor.Hero)
          .borderRadius(22).border({ width: 1, color: AppColor.Border })

        Search({ value: this.query, placeholder: $r('app.string.library_search_placeholder') })
          .width('100%').backgroundColor(AppColor.Surface).borderRadius(AppSpace.ControlRadius)
          .onChange((value: string) => { this.query = value; })

        Row({ space: 7 }) {
          Button('最新').fontSize(12).layoutWeight(1)
            .backgroundColor(this.sortMode === 'newest' ? AppColor.Primary : AppColor.Control)
            .fontColor(this.sortMode === 'newest' ? AppColor.OnPrimary : AppColor.Text)
            .onClick(() => { this.sortMode = 'newest'; })
          Button('最早').fontSize(12).layoutWeight(1)
            .backgroundColor(this.sortMode === 'oldest' ? AppColor.Primary : AppColor.Control)
            .fontColor(this.sortMode === 'oldest' ? AppColor.OnPrimary : AppColor.Text)
            .onClick(() => { this.sortMode = 'oldest'; })
          Button('标题').fontSize(12).layoutWeight(1)
            .backgroundColor(this.sortMode === 'title' ? AppColor.Primary : AppColor.Control)
            .fontColor(this.sortMode === 'title' ? AppColor.OnPrimary : AppColor.Text)
            .onClick(() => { this.sortMode = 'title'; })
        }.width('100%')

        if (this.isLoading) {
          Column({ space: 10 }) {
            LoadingProgress().width(42).height(42).color(AppColor.Primary)
            Text('正在加载笔记库…').fontSize(13).fontColor(AppColor.TextSecondary)
          }.layoutWeight(1).justifyContent(FlexAlign.Center)
        } else if (this.visibleNotes().length === 0) {
          Column({ space: 10 }) {
            Text(this.notes.length === 0 ? '还没有笔记' : '没有匹配的笔记')
              .fontSize(20).fontWeight(FontWeight.Medium)
            Text(this.notes.length === 0 ? '从 B站视频字幕生成第一篇 Markdown 时间轴笔记。' : '尝试更换搜索词。')
              .fontSize(14).fontColor(AppColor.TextSecondary).textAlign(TextAlign.Center)
          }.layoutWeight(1).justifyContent(FlexAlign.Center).padding(32)
        } else {
          List({ space: 11 }) {
            ForEach(this.visibleNotes(), (note: NoteRecord) => {
              ListItem() { this.noteCard(note) }.width('100%')
            }, (note: NoteRecord) => note.id)
          }.width('100%').layoutWeight(1).scrollBar(BarState.Auto)
        }

        if (this.status.length > 0) {
          Text(this.status).fontSize(12).fontColor(AppColor.TextSecondary).width('100%')
        }
      }
      .width('100%').height('100%').padding(AppSpace.Page).backgroundColor(AppColor.Background)
    }
    .mode(NavigationMode.Stack)
    .navDestination(this.pageMap)
    .hideTitleBar(true)
    .onAppear(() => { this.loadLibrary(); })
    .onNavBarStateChange((isVisible: boolean) => { if (isVisible) this.loadLibrary(); })
  }
}
