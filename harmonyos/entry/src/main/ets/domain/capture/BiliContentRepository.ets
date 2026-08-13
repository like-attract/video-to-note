import type { BiliSession, BiliVideo, SubtitleTrack, TranscriptSegment } from '../../models/BiliModels';

export interface BiliContentRepository {
  getVideo(input: string, session: BiliSession): Promise<BiliVideo>;
  getSubtitleTracks(video: BiliVideo, cid: number, session: BiliSession): Promise<SubtitleTrack[]>;
  downloadTranscript(track: SubtitleTrack, video: BiliVideo,
    session: BiliSession): Promise<TranscriptSegment[]>;
  cancelCurrent(): void;
}
