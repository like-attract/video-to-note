import type { BiliContentRepository } from '../../domain/capture/BiliContentRepository';
import type { BiliSession, BiliVideo, SubtitleTrack, TranscriptSegment } from '../../models/BiliModels';
import { mapRemoteError } from '../../core/errors/RemoteErrorMapper';
import { BiliApiService } from '../../services/BiliApiService';

export class RemoteBiliContentRepository implements BiliContentRepository {
  private readonly service: BiliApiService = new BiliApiService();
  async getVideo(input: string, session: BiliSession): Promise<BiliVideo> {
    try { return await this.service.getVideo(input, session); }
    catch (error) { throw mapRemoteError(error); }
  }
  async getSubtitleTracks(video: BiliVideo, cid: number,
    session: BiliSession): Promise<SubtitleTrack[]> {
    try { return await this.service.getSubtitleTracks(video, cid, session); }
    catch (error) { throw mapRemoteError(error); }
  }
  async downloadTranscript(track: SubtitleTrack, video: BiliVideo,
    session: BiliSession): Promise<TranscriptSegment[]> {
    try { return await this.service.downloadTranscript(track, video, session); }
    catch (error) { throw mapRemoteError(error); }
  }
  cancelCurrent(): void { this.service.cancelCurrent(); }
}
