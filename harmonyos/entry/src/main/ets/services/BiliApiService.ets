import { http } from '@kit.NetworkKit';
import type { BiliPage, BiliSession, BiliVideo, SubtitleTrack, TranscriptSegment } from '../models/BiliModels';
import { WbiKeys, WbiSigner } from './WbiSigner';
import { RequestScope } from '../core/network/RequestScope';
import { NetworkPolicy } from '../core/network/NetworkPolicy';

interface BiliOwnerData { name: string; }
interface BiliPageData { cid: number; page: number; part: string; duration: number; }
interface BiliVideoData {
  bvid: string; title: string; pic: string; duration: number;
  owner: BiliOwnerData; pages: BiliPageData[];
}
interface BiliVideoEnvelope { code: number; message: string; data: BiliVideoData; }
interface WbiImageData { img_url: string; sub_url: string; }
interface NavData { wbi_img: WbiImageData; }
interface NavEnvelope { code: number; message: string; data: NavData; }
interface SubtitleTrackData { lan: string; lan_doc: string; subtitle_url: string; }
interface SubtitleData { subtitles: SubtitleTrackData[]; }
interface PlayerData { subtitle: SubtitleData; }
interface PlayerEnvelope { code: number; message: string; data: PlayerData; }
interface SubtitleItemData { from: number; to: number; content: string; }
interface SubtitleEnvelope { body: SubtitleItemData[]; }

const API_ORIGIN: string = 'https://api.bilibili.com';
const WEB_ORIGIN: string = 'https://www.bilibili.com';
const LANGUAGE_ORDER: string[] = [
  'ai-zh', 'zh-hans', 'zh-cn', 'zh', 'zh-hant', 'zh-tw', 'ai-en', 'en', 'ai-ja', 'ja'
];

class ShortLinkRedirectInterceptor implements http.HttpInterceptor {
  interceptorType: http.InterceptorType = http.InterceptorType.REDIRECTION;
  latestUrl: string = '';
  blockedUrl: string = '';

  async interceptorHandle(reqContext: http.HttpRequestContext,
    _rspContext: http.HttpResponse): Promise<http.ChainContinue> {
    if (!BiliApiService.isAllowedBiliUrl(reqContext.url)) {
      this.blockedUrl = reqContext.url;
      return false;
    }
    this.latestUrl = reqContext.url;
    return true;
  }
}

export class BiliApiService {
  private readonly signer: WbiSigner = new WbiSigner();
  private readonly requests: RequestScope = new RequestScope();

  cancelCurrent(): void { this.requests.cancel(); }

  async getVideo(input: string, session: BiliSession): Promise<BiliVideo> {
    const bvid: string = await this.resolveBvid(input);
    const envelope: BiliVideoEnvelope = JSON.parse(await this.getText(
      `${API_ORIGIN}/x/web-interface/view?bvid=${encodeURIComponent(bvid)}`,
      this.apiHeaders(session)
    )) as BiliVideoEnvelope;
    this.requireSuccess(envelope.code, envelope.message, '获取视频信息');
    if (envelope.data === undefined || envelope.data.pages === undefined || envelope.data.pages.length === 0) {
      throw new Error('视频没有可用的分 P 信息。');
    }
    const pages: BiliPage[] = envelope.data.pages.map((item: BiliPageData) => {
      const page: BiliPage = {
        cid: item.cid,
        page: item.page,
        part: item.part || `P${item.page}`,
        durationSeconds: item.duration
      };
      return page;
    });
    return {
      bvid: envelope.data.bvid || bvid,
      title: envelope.data.title,
      ownerName: envelope.data.owner?.name || '',
      coverUrl: envelope.data.pic,
      pages: pages
    };
  }

  async getSubtitleTracks(video: BiliVideo, cid: number,
    session: BiliSession): Promise<SubtitleTrack[]> {
    const keys: WbiKeys = await this.getWbiKeys(session);
    const params: Map<string, string | number> = new Map();
    params.set('bvid', video.bvid);
    params.set('cid', cid);
    const signed = this.signer.sign(params, keys, Math.floor(Date.now() / 1000));
    const playerUrl: string = `${API_ORIGIN}/x/player/wbi/v2?${signed.queryString}` +
      `&w_rid=${signed.wRid}`;
    const envelope: PlayerEnvelope = JSON.parse(
      await this.getText(playerUrl, this.apiHeaders(session))
    ) as PlayerEnvelope;
    this.requireSuccess(envelope.code, envelope.message, '获取字幕列表');
    const rawTracks: SubtitleTrackData[] = envelope.data?.subtitle?.subtitles || [];
    const tracks: SubtitleTrack[] = rawTracks
      .filter((item: SubtitleTrackData) => item.subtitle_url?.length > 0)
      .map((item: SubtitleTrackData) => {
        const track: SubtitleTrack = {
          language: item.lan,
          displayName: item.lan_doc || item.lan,
          url: item.subtitle_url.startsWith('//') ? `https:${item.subtitle_url}` : item.subtitle_url,
          isAiGenerated: item.lan.startsWith('ai-')
        };
        return track;
      });
    return tracks.sort((left: SubtitleTrack, right: SubtitleTrack) =>
      this.languageRank(left.language) - this.languageRank(right.language));
  }

  async downloadTranscript(track: SubtitleTrack, video: BiliVideo,
    session: BiliSession): Promise<TranscriptSegment[]> {
    const headers: Record<string, string> = this.apiHeaders(session);
    headers['Origin'] = WEB_ORIGIN;
    headers['Referer'] = `${WEB_ORIGIN}/video/${video.bvid}`;
    const envelope: SubtitleEnvelope = JSON.parse(await this.getText(track.url, headers)) as SubtitleEnvelope;
    const body: SubtitleItemData[] = envelope.body || [];
    const segments: TranscriptSegment[] = [];
    body.forEach((item: SubtitleItemData) => {
      const content: string = (item.content || '').replace(/<[^>]+>/g, '')
        .replace(/\s+/g, ' ').trim();
      const start: number = Math.max(0, Number(item.from) || 0);
      const endValue: number = Number(item.to);
      const end: number = Math.max(start, Number.isFinite(endValue) ? endValue : start);
      if (content.length > 0) {
        segments.push({ startSeconds: start, endSeconds: end, content: content });
      }
    });
    if (segments.length === 0) {
      throw new Error('字幕文件为空或格式不受支持。');
    }
    return segments;
  }

  extractBvid(input: string): string {
    const match: RegExpMatchArray | null = input.trim().match(/BV[0-9A-Za-z]{10}/i);
    if (match === null) {
      throw new Error('没有找到有效的 BV 号。');
    }
    return `BV${match[0].slice(2)}`;
  }

  async resolveBvid(input: string): Promise<string> {
    const directMatch: RegExpMatchArray | null = input.trim().match(/BV[0-9A-Za-z]{10}/i);
    if (directMatch !== null) {
      return `BV${directMatch[0].slice(2)}`;
    }
    const shortLink: string = this.extractShortLink(input);
    return await this.resolveShortLink(shortLink);
  }

  private extractShortLink(input: string): string {
    const match: RegExpMatchArray | null = input.match(/https:\/\/b23\.tv\/[A-Za-z0-9_-]+(?:\?[^\s]*)?/i);
    if (match === null) {
      throw new Error('请输入 BV 号、B 站完整链接或 b23.tv 短链接。');
    }
    return match[0];
  }

  private async resolveShortLink(shortLink: string): Promise<string> {
    const request: http.HttpRequest = http.createHttp();
    const interceptor: ShortLinkRedirectInterceptor = new ShortLinkRedirectInterceptor();
    const chain: http.HttpInterceptorChain = new http.HttpInterceptorChain();
    chain.addChain([interceptor]);
    chain.apply(request);
    try {
      await this.requests.requestWithTask(request, shortLink, {
        method: http.RequestMethod.GET,
        header: {
          'Accept': 'text/html,application/xhtml+xml',
          'User-Agent': 'Mozilla/5.0 (Linux; HarmonyOS) AppleWebKit/537.36 Mobile Safari/537.36'
        },
        expectDataType: http.HttpDataType.STRING,
        usingCache: false,
        maxRedirects: NetworkPolicy.ShortLinkMaxRedirects,
        maxLimit: 1024 * 1024,
        connectTimeout: NetworkPolicy.ConnectTimeoutMs,
        readTimeout: NetworkPolicy.BiliReadTimeoutMs
      });
      if (interceptor.blockedUrl.length > 0) {
        throw new Error('短链接跳转到了非 B 站官方域名，已拒绝继续访问。');
      }
      if (interceptor.latestUrl.length === 0) {
        throw new Error('短链接没有返回可识别的 B 站地址。');
      }
      return this.extractBvid(interceptor.latestUrl);
    } finally {}
  }

  static isAllowedBiliUrl(value: string): boolean {
    const match: RegExpMatchArray | null = value.match(/^https:\/\/([^/?#:]+)(?:[/?#:]|$)/i);
    if (match === null) {
      return false;
    }
    const host: string = match[1].toLowerCase();
    return host === 'b23.tv' || host === 'bilibili.com' || host.endsWith('.bilibili.com');
  }

  private async getWbiKeys(session: BiliSession): Promise<WbiKeys> {
    const envelope: NavEnvelope = JSON.parse(await this.getText(
      `${API_ORIGIN}/x/web-interface/nav`, this.apiHeaders(session)
    )) as NavEnvelope;
    this.requireSuccess(envelope.code, envelope.message, '获取 WBI 签名密钥');
    const imageKey: string = this.fileStem(envelope.data?.wbi_img?.img_url || '');
    const subKey: string = this.fileStem(envelope.data?.wbi_img?.sub_url || '');
    if (imageKey.length === 0 || subKey.length === 0) {
      throw new Error('B站没有返回有效的 WBI 签名密钥。');
    }
    return { imageKey: imageKey, subKey: subKey };
  }

  private fileStem(url: string): string {
    const lastPart: string = url.split('/').pop() || '';
    return lastPart.split('.')[0] || '';
  }

  private languageRank(language: string): number {
    const rank: number = LANGUAGE_ORDER.indexOf(language);
    return rank >= 0 ? rank : LANGUAGE_ORDER.length;
  }

  private apiHeaders(session: BiliSession): Record<string, string> {
    return {
      'Accept': 'application/json, text/plain, */*',
      'User-Agent': 'Mozilla/5.0 (Linux; HarmonyOS) AppleWebKit/537.36 Mobile Safari/537.36',
      'Referer': `${WEB_ORIGIN}/`,
      'Origin': WEB_ORIGIN,
      'Cookie': session.cookieHeader
    };
  }

  private async getText(url: string, headers: Record<string, string>): Promise<string> {
    const response: http.HttpResponse = await this.requests.request(url, {
        method: http.RequestMethod.GET,
        header: headers,
        expectDataType: http.HttpDataType.STRING,
        usingCache: false,
        connectTimeout: NetworkPolicy.ConnectTimeoutMs,
        readTimeout: NetworkPolicy.BiliReadTimeoutMs,
        maxLimit: NetworkPolicy.MaxResponseBytes
    });
    const statusCode: number = response.responseCode as number;
    if (statusCode < 200 || statusCode >= 300) {
      throw new Error(`网络请求失败，HTTP ${statusCode}`);
    }
    if (typeof response.result !== 'string') throw new Error('接口返回了无法解析的数据格式。');
    return response.result;
  }

  private requireSuccess(code: number, message: string, action: string): void {
    if (code === -101) {
      throw new Error(`${action}失败：登录状态无效，请重新扫码。`);
    }
    if (code !== 0) {
      throw new Error(`${action}失败：${message || code.toString()}（${code}）`);
    }
  }
}
