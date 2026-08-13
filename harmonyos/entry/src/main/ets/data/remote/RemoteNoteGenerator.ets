import type { NoteGenerator, GenerationProgress } from '../../domain/generation/NoteGenerator';
import type { BiliPage, BiliVideo, TranscriptSegment } from '../../models/BiliModels';
import type { LlmConfig } from '../../models/LlmModels';
import { mapRemoteError } from '../../core/errors/RemoteErrorMapper';
import { AppError, AppErrorCode } from '../../core/errors/AppError';
import { NetworkPolicy } from '../../core/network/NetworkPolicy';
import { LlmService } from '../../services/LlmService';

export class RemoteNoteGenerator implements NoteGenerator {
  private readonly service: LlmService = new LlmService();
  async generate(config: LlmConfig, video: BiliVideo, page: BiliPage,
    segments: TranscriptSegment[], onProgress?: GenerationProgress): Promise<string> {
    try { return await this.service.summarize(config, video, page, segments, onProgress); }
    catch (error) {
      const mapped: AppError = mapRemoteError(error);
      if (mapped.code === AppErrorCode.NetworkTimeout) {
        const minutes: number = Math.round(NetworkPolicy.LlmReadTimeoutMs / 60000);
        throw new AppError(AppErrorCode.NetworkTimeout,
          `模型生成等待超过 ${minutes} 分钟。可稍后重试；若持续发生，请检查中转服务的请求时限。`, true);
      }
      throw mapped;
    }
  }
  cancelCurrent(): void { this.service.cancelCurrent(); }
}
