import { http } from '@kit.NetworkKit';
import type { BiliSession } from '../models/BiliModels';
import type { QrLoginChallenge, QrLoginPollResult } from '../domain/auth/BiliAuthGateway';
import { QrLoginState } from '../domain/auth/BiliAuthGateway';
import { RequestScope } from '../core/network/RequestScope';
import { NetworkPolicy } from '../core/network/NetworkPolicy';

const QR_GENERATE_URL: string =
  'https://passport.bilibili.com/x/passport-login/web/qrcode/generate';
const QR_POLL_URL: string =
  'https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=';
const REQUEST_HEADERS: Record<string, string> = {
  'Accept': 'application/json, text/plain, */*',
  'User-Agent': 'Mozilla/5.0 (Linux; HarmonyOS) AppleWebKit/537.36 Mobile Safari/537.36',
  'Referer': 'https://www.bilibili.com/'
};

interface QrGenerateData {
  url: string;
  qrcode_key: string;
}

interface QrGenerateResponse {
  code: number;
  message: string;
  data: QrGenerateData;
}

interface QrPollData {
  code: number;
  message: string;
  refresh_token?: string;
}

interface QrPollResponse {
  code: number;
  message: string;
  data: QrPollData;
}

interface RawHttpResult {
  body: string;
  responseCode: number;
  cookies: string;
  serializedHeaders: string;
}

export class BiliLoginService {
  private readonly requests: RequestScope = new RequestScope();

  cancelCurrent(): void { this.requests.cancel(); }
  async createChallenge(): Promise<QrLoginChallenge> {
    const response: RawHttpResult = await this.get(QR_GENERATE_URL);
    const payload: QrGenerateResponse = JSON.parse(response.body) as QrGenerateResponse;
    if (payload.code !== 0 || payload.data === undefined || payload.data.url.length === 0 ||
      payload.data.qrcode_key.length === 0) {
      throw new Error(`B站二维码接口返回异常：${payload.message || payload.code.toString()}`);
    }
    return {
      qrCodeUrl: payload.data.url,
      qrCodeKey: payload.data.qrcode_key,
      expiresInSeconds: 180
    };
  }

  async poll(qrCodeKey: string): Promise<QrLoginPollResult> {
    if (qrCodeKey.length === 0) {
      throw new Error('二维码登录标识为空，请重新生成二维码。');
    }
    const response: RawHttpResult = await this.get(`${QR_POLL_URL}${encodeURIComponent(qrCodeKey)}`);
    const payload: QrPollResponse = JSON.parse(response.body) as QrPollResponse;
    if (payload.code !== 0 || payload.data === undefined) {
      throw new Error(`B站登录接口返回异常：${payload.message || payload.code.toString()}`);
    }

    switch (payload.data.code) {
      case 86101:
        return { state: QrLoginState.WaitingForScan, message: '等待扫码' };
      case 86090:
        return { state: QrLoginState.WaitingForConfirmation, message: '已扫码，请在 B 站客户端确认' };
      case 86038:
        return { state: QrLoginState.Expired, message: '二维码已过期，请重新生成' };
      case 0: {
        const rawCookieSources: string = `${response.cookies}\n${response.serializedHeaders}`;
        const cookieHeader: string = this.toCookieHeader(rawCookieSources);
        if (!cookieHeader.includes('SESSDATA=')) {
          const foundNames: string[] = this.findCookieNames(rawCookieSources);
          const diagnostic: string = foundNames.length > 0 ? foundNames.join('、') : '无 Cookie';
          throw new Error(`登录成功，但没有提取到 SESSDATA（响应包含：${diagnostic}）。`);
        }
        return {
          state: QrLoginState.Succeeded,
          message: '登录成功，已在内存中建立 B 站会话',
          session: {
            cookieHeader: cookieHeader,
            refreshToken: payload.data.refresh_token,
            createdAt: Date.now()
          }
        };
      }
      default:
        throw new Error(`未知的扫码状态：${payload.data.code} ${payload.data.message}`);
    }
  }

  async clearSession(): Promise<void> {
    // TODO(PoC-4): delete the HUKS-encrypted cookie jar and refresh token.
  }

  private async get(url: string): Promise<RawHttpResult> {
    const response: http.HttpResponse = await this.requests.request(url, {
        method: http.RequestMethod.GET,
        header: REQUEST_HEADERS,
        expectDataType: http.HttpDataType.STRING,
        connectTimeout: NetworkPolicy.ConnectTimeoutMs,
        readTimeout: NetworkPolicy.BiliReadTimeoutMs,
        maxLimit: NetworkPolicy.MaxResponseBytes
    });
    const statusCode: number = response.responseCode as number;
    if (statusCode < 200 || statusCode >= 300) {
      throw new Error(`B站网络请求失败，HTTP ${statusCode}`);
    }
    return {
      body: this.requireText(response), responseCode: statusCode,
      cookies: response.cookies || '', serializedHeaders: JSON.stringify(response.header)
    };
  }

  private requireText(response: http.HttpResponse): string {
    if (typeof response.result !== 'string') {
      throw new Error('B站接口返回了无法解析的数据格式。');
    }
    return response.result;
  }

  private toCookieHeader(rawCookies: string): string {
    const cookieNames: string[] = [
      'SESSDATA', 'bili_jct', 'buvid3', 'buvid4', 'DedeUserID', 'DedeUserID__ckMd5', 'sid'
    ];
    const pairs: string[] = [];
    cookieNames.forEach((name: string) => {
      const escapedName: string = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const pattern: RegExp = new RegExp(
        `(?:^|[^A-Za-z0-9_])${escapedName}=([^;,\\r\\n"'\\]\\s]+)`
      );
      const match: RegExpMatchArray | null = rawCookies.match(pattern);
      if (match !== null && match[1] !== undefined && match[1].length > 0) {
        pairs.push(`${name}=${match[1]}`);
      }
    });
    return pairs.join('; ');
  }

  private findCookieNames(rawCookies: string): string[] {
    const names: string[] = [];
    const pattern: RegExp = /(?:^|[^A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)=/g;
    let match: RegExpExecArray | null = pattern.exec(rawCookies);
    while (match !== null) {
      const name: string = match[1];
      const lowerName: string = name.toLowerCase();
      const isAttribute: boolean = lowerName === 'path' || lowerName === 'domain' ||
        lowerName === 'expires' || lowerName === 'max-age' || lowerName === 'samesite';
      if (!isAttribute && !names.includes(name)) {
        names.push(name);
      }
      match = pattern.exec(rawCookies);
    }
    return names;
  }
}
