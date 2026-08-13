import type { BiliPage, BiliVideo, TranscriptSegment } from '../../models/BiliModels';
import type { LlmConfig } from '../../models/LlmModels';

export type GenerationProgress = (progress: number, message: string) => void;

export interface NoteGenerator {
  generate(config: LlmConfig, video: BiliVideo, page: BiliPage,
    segments: TranscriptSegment[], onProgress?: GenerationProgress): Promise<string>;
  cancelCurrent(): void;
}
