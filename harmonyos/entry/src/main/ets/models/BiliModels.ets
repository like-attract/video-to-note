export interface BiliSession {
  cookieHeader: string;
  refreshToken?: string;
  createdAt: number;
}

export interface BiliPage {
  cid: number;
  page: number;
  part: string;
  durationSeconds: number;
}

export interface BiliVideo {
  bvid: string;
  title: string;
  ownerName: string;
  coverUrl: string;
  pages: BiliPage[];
}

export interface SubtitlePreview {
  video: BiliVideo;
  page: BiliPage;
  track: SubtitleTrack;
  segments: TranscriptSegment[];
}

export interface SubtitleTrack {
  language: string;
  displayName: string;
  url: string;
  isAiGenerated: boolean;
}

export interface TranscriptSegment {
  startSeconds: number;
  endSeconds: number;
  content: string;
}
