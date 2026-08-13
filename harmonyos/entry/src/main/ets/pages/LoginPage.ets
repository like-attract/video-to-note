import { AppRoute } from '../app/AppRoutes';
import type { BiliSession } from '../models/BiliModels';
import { appContainer } from '../app/AppContainer';
import { toUserMessage } from '../core/errors/AppError';
import { QrLoginChallenge, QrLoginPollResult, QrLoginState } from '../domain/auth/BiliAuthGateway';
import { AppColor, AppSpace } from '../core/theme/AppTheme';

@Component
export struct LoginPage {
  pathStack: NavPathStack = new NavPathStack();
  @State status: string = '尚未生成二维码';
  @State qrCodeUrl: string = '';
  @State isLoading: boolean = false;
  @State isPolling: boolean = false;

  private qrCodeKey: string = '';
  private pollTimer: number = -1;
  private session?: BiliSession;
  private disposed: boolean = false;

  aboutToAppear(): void {
    this.disposed = false;
  }

  aboutToDisappear(): void {
    this.disposed = true;
    this.stopPolling();
  }

  private async createChallenge(): Promise<void> {
    if (this.isLoading) {
      return;
    }
    this.stopPolling();
    this.isLoading = true;
    this.qrCodeUrl = '';
    this.qrCodeKey = '';
    this.session = undefined;
    this.status = '正在连接 B 站…';
    try {
      const challenge: QrLoginChallenge = await appContainer.login.createChallenge();
      if (this.disposed) {
        return;
      }
      this.qrCodeUrl = challenge.qrCodeUrl;
      this.qrCodeKey = challenge.qrCodeKey;
      this.status = `请在 ${challenge.expiresInSeconds} 秒内扫码并确认`;
      this.isPolling = true;
      this.schedulePoll(300);
    } catch (error) {
      if (!this.disposed) {
        this.status = `生成二维码失败：${this.errorMessage(error)}`;
      }
    } finally {
      if (!this.disposed) {
        this.isLoading = false;
      }
    }
  }

  private schedulePoll(delayMilliseconds: number): void {
    this.clearPollTimer();
    this.pollTimer = setTimeout(() => {
      this.pollOnce();
    }, delayMilliseconds);
  }

  private async pollOnce(): Promise<void> {
    if (!this.isPolling || this.qrCodeKey.length === 0) {
      return;
    }
    try {
      const result: QrLoginPollResult = await appContainer.login.poll(this.qrCodeKey);
      if (this.disposed) {
        return;
      }
      this.status = result.message;
      if (result.state === QrLoginState.Succeeded) {
        this.session = result.session;
        this.stopPolling();
        if (result.session !== undefined) {
          setTimeout(() => {
            if (!this.disposed) {
              this.pathStack.replacePath(new NavPathInfo(AppRoute.Subtitle, undefined));
            }
          }, 500);
        }
        return;
      }
      if (result.state === QrLoginState.Expired) {
        this.stopPolling();
        return;
      }
      this.schedulePoll(1500);
    } catch (error) {
      this.stopPolling();
      if (!this.disposed) {
        this.status = `轮询登录状态失败：${this.errorMessage(error)}`;
      }
    }
  }

  private stopPolling(): void {
    this.isPolling = false;
    this.clearPollTimer();
  }

  private clearPollTimer(): void {
    if (this.pollTimer >= 0) {
      clearTimeout(this.pollTimer);
      this.pollTimer = -1;
    }
  }

  private errorMessage(error: Object): string {
    return toUserMessage(error);
  }

  build() {
    NavDestination() {
      Column({ space: 20 }) {
      Row({ space: 8 }) {
        Button($r('app.string.action_back')).fontSize(13).onClick(() => this.pathStack.pop())
        Text($r('app.string.login_title')).fontSize(26).fontWeight(FontWeight.Bold).layoutWeight(1)
      }.width('100%')
      Text($r('app.string.login_help'))
        .fontSize(15).fontColor(AppColor.TextSecondary).width('100%')

      Column() {
        if (this.qrCodeUrl.length > 0) {
          QRCode(this.qrCodeUrl)
            .width(208)
            .height(208)
            .color(AppColor.Qr)
            .backgroundColor(AppColor.QrSurface)
        } else if (this.isLoading) {
          LoadingProgress().width(56).height(56).color(AppColor.Primary)
        } else {
          Text($r('app.string.login_qr_placeholder')).fontSize(18).fontColor(AppColor.TextMuted)
        }
      }
      .width(240).height(240)
      .justifyContent(FlexAlign.Center)
      .backgroundColor(AppColor.QrSurface)
      .border({ width: 1, color: AppColor.Border })
      .borderRadius(16)

      Text(this.status).fontSize(14).fontColor(AppColor.TextSecondary).textAlign(TextAlign.Center)

      Button(this.qrCodeUrl.length > 0 ? '重新生成二维码' : '生成登录二维码')
        .width('100%')
        .borderRadius(AppSpace.ControlRadius)
        .backgroundColor(AppColor.Primary)
        .fontColor(AppColor.OnPrimary)
        .enabled(!this.isLoading)
        .onClick(() => {
          this.createChallenge();
        })
      }
      .width('100%').height('100%')
      .padding(AppSpace.Page)
      .backgroundColor(AppColor.Background)
    }
    .hideTitleBar(true)
    .onShown(() => { this.disposed = false; })
    .onHidden(() => { this.disposed = true; this.stopPolling(); appContainer.login.cancel(); })
  }
}
