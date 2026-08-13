import { AppError, AppErrorCode } from '../../core/errors/AppError';
import type { SubtitlePreview } from '../../models/BiliModels';
import type { LlmConfig, NoteRecord } from '../../models/LlmModels';
import type { NoteRepository } from '../notes/NoteRepository';
import type { GenerationRepository } from './GenerationRepository';
import type { GenerationProgress, NoteGenerator } from './NoteGenerator';

export class GenerateNoteUseCase {
  private readonly generator: NoteGenerator;
  private readonly workspace: GenerationRepository;
  private readonly notes: NoteRepository;
  constructor(generator: NoteGenerator, workspace: GenerationRepository,
    notes: NoteRepository) {
    this.generator = generator;
    this.workspace = workspace;
    this.notes = notes;
  }

  async execute(config: LlmConfig, onProgress?: GenerationProgress,
    previewOverride?: SubtitlePreview, noteId?: string): Promise<NoteRecord> {
    const preview: SubtitlePreview | undefined = previewOverride ||
      (await this.workspace.getWorkspace()).preview;
    if (preview === undefined)
      throw new AppError(AppErrorCode.InvalidInput, '字幕数据不存在，请返回重新获取。');
    const markdown: string = await this.generator.generate(
      config, preview.video, preview.page, preview.segments, onProgress);
    const note: NoteRecord = {
      id: noteId || `${preview.video.bvid}-${preview.page.cid}-${Date.now()}`,
      bvid: preview.video.bvid,
      cid: preview.page.cid,
      noteTitle: preview.video.title,
      videoTitle: preview.video.title,
      markdown: markdown,
      createdAt: Date.now(),
      generation: {
        providerName: config.providerName, model: config.model, style: config.style,
        reasoningEffort: config.reasoningEffort
      }
    };
    await this.workspace.saveNote(note);
    await this.notes.save(note);
    return note;
  }

  cancel(): void { this.generator.cancelCurrent(); }
}
