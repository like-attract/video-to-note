import type { NoteGenerator, GenerationProgress } from '../../domain/generation/NoteGenerator';
import type { BiliPage, BiliVideo, TranscriptSegment } from '../../models/BiliModels';
import type { LlmConfig } from '../../models/LlmModels';
import { mapRemoteError } from '../../core/errors/RemoteErrorMapper';
import { LlmService } from '../../services/LlmService';

export class RemoteNoteGenerator implements NoteGenerator {
  private readonly service: LlmService = new LlmService();
  async generate(config: LlmConfig, video: BiliVideo, page: BiliPage,
    segments: TranscriptSegment[], onProgress?: GenerationProgress): Promise<string> {
    try { return await this.service.summarize(config, video, page, segments, onProgress); }
    catch (error) { throw mapRemoteError(error); }
  }
  cancelCurrent(): void { this.service.cancelCurrent(); }
}
