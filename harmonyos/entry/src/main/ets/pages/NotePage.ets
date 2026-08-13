import { common } from '@kit.AbilityKit';
import { pasteboard } from '@kit.BasicServicesKit';
import { systemShare } from '@kit.ShareKit';
import { appContainer } from '../app/AppContainer';
import type { NoteRecord } from '../models/LlmModels';
import { MarkdownBlock, MarkdownPresenter } from '../services/MarkdownPresenter';
import { AppColor, AppSpace } from '../core/theme/AppTheme';

@Component
export struct NotePage {
  pathStack: NavPathStack = new NavPathStack();
  noteId: string = '';
  @State markdown: string = '';
  @State blocks: MarkdownBlock[] = [];
  @State showSource: boolean = false;
  @State status: string = '可复制或通过系统分享发送到华为笔记等应用。';
  private readonly presenter: MarkdownPresenter = new MarkdownPresenter();
  private title: string = 'VideoToNo 视频笔记';

  aboutToAppear(): void {
    this.restoreNote();
  }

  private async restoreNote(): Promise<void> {
    const note: NoteRecord | undefined = this.noteId.length > 0 ?
      await appContainer.notes.getById(this.noteId) :
      (await appContainer.generation.getWorkspace()).note;
    this.markdown = note?.markdown || '# 暂无笔记\n\n请返回并重新生成。';
    this.title = note === undefined ? 'VideoToNo 视频笔记' : note.noteTitle;
    this.blocks = this.presenter.parse(this.markdown);
  }

  private async copyMarkdown(): Promise<void> {
    try {
      const data: pasteboard.PasteData = pasteboard.createData(
        pasteboard.MIMETYPE_TEXT_PLAIN, this.markdown);
      await pasteboard.getSystemPasteboard().setData(data);
      this.status = 'Markdown 原文已复制。';
    } catch (error) {
      this.status = error instanceof Error ? `复制失败：${error.message}` : '复制失败。';
    }
  }

  private async shareToHarmony(): Promise<void> {
    try {
      const record: systemShare.SharedRecord = {
        utd: 'general.plain-text',
        content: this.markdown,
        title: this.title,
        description: '由 VideoToNo 生成的 Markdown 时间轴笔记'
      };
      const data: systemShare.SharedData = new systemShare.SharedData(record);
      const controller: systemShare.ShareController = new systemShare.ShareController(data);
      const context: common.UIAbilityContext = getContext(this) as common.UIAbilityContext;
      await controller.show(context, { selectionMode: systemShare.SelectionMode.SINGLE });
      this.status = '已打开系统分享；可在真机上选择华为笔记。';
    } catch (error) {
      this.status = error instanceof Error ? `分享失败：${error.message}` : '分享失败。';
    }
  }

  @Builder
  private renderBlock(block: MarkdownBlock) {
    if (block.kind === 'heading') {
      Text(block.text)
        .fontSize(block.level === 1 ? 24 : (block.level === 2 ? 20 : 17))
        .fontWeight(FontWeight.Bold).fontColor(AppColor.Text).width('100%')
        .margin({ top: block.level === 1 ? 8 : 5, bottom: 3 })
    } else if (block.kind === 'bullet') {
      Row({ space: 8 }) {
        Text('•').fontSize(17).fontColor(AppColor.Primary).alignSelf(ItemAlign.Start)
        Text(block.text).fontSize(15).fontColor(AppColor.PreviewText).layoutWeight(1)
      }.width('100%')
    } else if (block.kind === 'ordered') {
      Text(block.text).fontSize(15).fontColor(AppColor.PreviewText).width('100%').padding({ left: 12 })
    } else if (block.kind === 'quote') {
      Text(block.text).fontSize(14).fontColor(AppColor.TextSecondary).width('100%')
        .padding({ left: 12, top: 8, right: 8, bottom: 8 })
        .border({ width: { left: 3 }, color: AppColor.Primary }).backgroundColor(AppColor.QuoteBackground)
    } else if (block.kind === 'code') {
      Text(block.text).fontFamily('monospace').fontSize(13).fontColor(AppColor.PreviewText).width('100%')
        .padding(10).backgroundColor(AppColor.CodeBackground).borderRadius(8)
    } else if (block.kind === 'divider') {
      Divider().color(AppColor.Border).width('100%').margin({ top: 5, bottom: 5 })
    } else {
      Text(block.text).fontSize(15).fontColor(AppColor.PreviewText).lineHeight(24).width('100%')
    }
  }

  build() {
    NavDestination() {
      Column({ space: 14 }) {
      Row({ space: 8 }) {
        Button($r('app.string.action_back')).fontSize(13).onClick(() => this.pathStack.pop())
        Text($r('app.string.note_title')).fontSize(24).fontWeight(FontWeight.Bold).layoutWeight(1)
      }.width('100%')
      Text($r('app.string.flow_step_note'))
        .fontSize(12).fontColor(AppColor.Primary).fontWeight(FontWeight.Medium).width('100%')
      Row({ space: 8 }) {
        Button(this.showSource ? '预览' : '原文').fontSize(13).layoutWeight(1)
          .onClick(() => { this.showSource = !this.showSource; })
        Button($r('app.string.action_copy')).fontSize(13).layoutWeight(1).onClick(() => this.copyMarkdown())
        Button($r('app.string.action_share_harmony')).fontSize(13).layoutWeight(2)
          .backgroundColor(AppColor.Primary).onClick(() => this.shareToHarmony())
      }.width('100%')
      if (this.showSource) {
        TextArea({ text: this.markdown }).fontFamily('monospace').width('100%').layoutWeight(1)
      } else {
        List({ space: 9 }) {
          ForEach(this.blocks, (block: MarkdownBlock, index: number) => {
            ListItem() { this.renderBlock(block) }.width('100%')
          }, (block: MarkdownBlock, index: number) => `${index}-${block.kind}-${block.text}`)
        }.width('100%').layoutWeight(1).padding(AppSpace.Card)
          .backgroundColor(AppColor.Surface).borderRadius(AppSpace.Radius)
          .border({ width: 1, color: AppColor.Border })
      }
      Text(this.status).fontSize(12).fontColor(AppColor.TextMuted).width('100%')
      }.width('100%').height('100%').padding(AppSpace.Page).backgroundColor(AppColor.Background)
    }
    .hideTitleBar(true)
    .onShown(() => { this.restoreNote(); })
  }
}
