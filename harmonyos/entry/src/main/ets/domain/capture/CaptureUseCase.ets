import { AppError, AppErrorCode } from '../../core/errors/AppError';
import type { BiliPage, BiliSession, BiliVideo, SubtitlePreview,
  SubtitleTrack, TranscriptSegment } from '../../models/BiliModels';
import type { BiliSessionRepository } from '../auth/BiliSessionRepository';
import type { GenerationRepository } from '../generation/GenerationRepository';
import type { BiliContentRepository } from './BiliContentRepository';

export class CaptureUseCase {
  private readonly content: BiliContentRepository;
  private readonly sessions: BiliSessionRepository;
  private readonly generation: GenerationRepository;
  constructor(content: BiliContentRepository, sessions: BiliSessionRepository,
    generation: GenerationRepository) {
    this.content = content;
    this.sessions = sessions;
    this.generation = generation;
  }

  async hasSession(): Promise<boolean> { return (await this.sessions.getCurrent()) !== undefined; }

  async loadVideo(input: string): Promise<BiliVideo> {
    const session: BiliSession = await this.requireSession();
    try {
      return await this.content.getVideo(input, session);
    } catch (error) {
      await this.clearExpiredSession(error as Object);
      throw error instanceof Error ? error :
        new AppError(AppErrorCode.ApiError, '获取视频信息失败。', true);
    }
  }

  async loadSubtitle(video: BiliVideo, page: BiliPage): Promise<SubtitlePreview> {
    const session: BiliSession = await this.requireSession();
    try {
      const tracks: SubtitleTrack[] = await this.content.getSubtitleTracks(video, page.cid, session);
      if (tracks.length === 0) throw new AppError(AppErrorCode.NoSubtitle, '该分 P 没有可用字幕。');
      const track: SubtitleTrack = tracks[0];
      const segments: TranscriptSegment[] = await this.content.downloadTranscript(track, video, session);
      const preview: SubtitlePreview = { video: video, page: page, track: track, segments: segments };
      await this.generation.savePreview(preview);
      return preview;
    } catch (error) {
      await this.clearExpiredSession(error as Object);
      throw error instanceof Error ? error :
        new AppError(AppErrorCode.ApiError, '获取字幕失败。', true);
    }
  }

  cancel(): void { this.content.cancelCurrent(); }

  private async requireSession(): Promise<BiliSession> {
    const session: BiliSession | undefined = await this.sessions.getCurrent();
    if (session === undefined)
      throw new AppError(AppErrorCode.NotLoggedIn, '登录会话不存在，请重新扫码。');
    return session;
  }

  private async clearExpiredSession(error: Object): Promise<void> {
    if (error instanceof AppError && error.code === AppErrorCode.SessionExpired) {
      await this.sessions.clear();
    }
  }
}
