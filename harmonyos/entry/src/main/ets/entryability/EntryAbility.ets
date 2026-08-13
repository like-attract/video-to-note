import { AbilityConstant, ConfigurationConstant, UIAbility, Want } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { systemShare } from '@kit.ShareKit';
import { appContainer } from '../app/AppContainer';
import { IncomingShareParser, SharedRecordLike } from '../features/share/IncomingShareParser';

export default class EntryAbility extends UIAbility {
  private readonly shareParser: IncomingShareParser = new IncomingShareParser();

  onCreate(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    this.context.getApplicationContext()
      .setColorMode(ConfigurationConstant.ColorMode.COLOR_MODE_NOT_SET);
    this.receiveSharedInput(want);
  }

  onNewWant(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    this.receiveSharedInput(want);
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    appContainer.initialize(this.context);
    windowStage.loadContent('pages/Index');
  }

  private async receiveSharedInput(want: Want): Promise<void> {
    if (want.action !== 'ohos.want.action.sendData') return;
    try {
      const sharedData: systemShare.SharedData = await systemShare.getSharedData(want);
      const records: SharedRecordLike[] = sharedData.getRecords();
      const input: string = this.shareParser.extractBiliInput(records);
      if (input.length === 0) {
        AppStorage.setOrCreate<string>('incomingShareError', '分享内容中没有识别到 B站链接或 BV 号。');
        return;
      }
      AppStorage.setOrCreate<string>('incomingShareError', '');
      AppStorage.setOrCreate<string>('incomingShare', input);
    } catch (_error) {
      AppStorage.setOrCreate<string>('incomingShareError', '读取系统分享内容失败，请复制链接后在应用内粘贴。');
    }
  }
}
