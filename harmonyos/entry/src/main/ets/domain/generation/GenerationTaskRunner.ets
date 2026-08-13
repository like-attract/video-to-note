import { AppError, AppErrorCode, toUserMessage } from '../../core/errors/AppError';
import type { GenerationTask } from '../../models/GenerationTask';
import type { LlmConfig, NoteRecord } from '../../models/LlmModels';
import type { LlmProfileRepository } from './LlmProfileRepository';
import type { GenerationTaskRepository } from './GenerationTaskRepository';
import { GenerateNoteUseCase } from './GenerateNoteUseCase';
import type { NoteRepository } from '../notes/NoteRepository';

export type GenerationTaskListener = (task: GenerationTask) => void;

export class GenerationTaskRunner {
  private readonly tasks: GenerationTaskRepository;
  private readonly profiles: LlmProfileRepository;
  private readonly generateNote: GenerateNoteUseCase;
  private readonly notes: NoteRepository;
  private activeTaskId: string = '';
  private listeners: GenerationTaskListener[] = [];

  constructor(tasks: GenerationTaskRepository, profiles: LlmProfileRepository,
    generateNote: GenerateNoteUseCase, notes: NoteRepository) {
    this.tasks = tasks; this.profiles = profiles; this.generateNote = generateNote; this.notes = notes;
  }

  subscribe(listener: GenerationTaskListener): () => void {
    this.listeners.push(listener);
    return (): void => { this.listeners = this.listeners.filter((item: GenerationTaskListener) => item !== listener); };
  }

  async reconcile(): Promise<void> { await this.tasks.recoverInterrupted(); }

  async start(taskId: string): Promise<void> {
    if (this.activeTaskId.length > 0) return;
    this.activeTaskId = taskId;
    try {
      const task: GenerationTask | undefined = await this.tasks.getById(taskId);
      if (task === undefined || task.status === 'succeeded' || task.status === 'cancelled') return;
      const existing: NoteRecord | undefined = await this.notes.getById(task.id);
      if (existing !== undefined) {
        task.status = 'succeeded'; task.stage = 'succeeded'; task.progress = 100;
        task.message = '笔记生成完成'; task.noteId = existing.id; task.retryable = false;
        await this.persist(task); return;
      }
      await this.run(task);
    } finally {
      this.activeTaskId = '';
      await this.startNextQueued();
    }
  }

  private async run(task: GenerationTask): Promise<void> {
    task.status = 'running'; task.stage = 'preparing'; task.progress = 0;
    task.message = '正在读取模型配置…'; task.errorCode = ''; task.errorMessage = '';
    task.attempt += 1; await this.persist(task);
    try {
      const config: LlmConfig = await this.profiles.resolve(task.profileId, task.style);
      task.stage = 'generating'; task.message = '正在生成笔记…'; await this.persist(task);
      let persistedProgress: number = task.progress;
      let persistedMessage: string = task.message;
      const note: NoteRecord = await this.generateNote.execute(config,
        (progress: number, message: string): void => {
          task.progress = Math.max(task.progress, progress); task.message = message;
          this.notify(task);
          if (task.progress >= persistedProgress + 2 || message !== persistedMessage) {
            persistedProgress = task.progress; persistedMessage = message;
            this.tasks.save(task);
          }
        }, task.preview, task.id);
      task.status = 'succeeded'; task.stage = 'succeeded'; task.progress = 100;
      task.message = '笔记生成完成'; task.noteId = note.id; task.retryable = false;
      await this.persist(task);
    } catch (error) {
      const cancelled: boolean = error instanceof AppError && error.code === AppErrorCode.Cancelled;
      task.status = cancelled ? 'cancelled' : 'failed';
      task.stage = task.status; task.errorCode = error instanceof AppError ? error.code : AppErrorCode.Unknown;
      task.errorMessage = toUserMessage(error as Object); task.message = cancelled ? '生成已取消' : task.errorMessage;
      task.retryable = !cancelled && (!(error instanceof AppError) || error.retryable ||
        error.code === AppErrorCode.Unknown || error.code === AppErrorCode.ApiError);
      await this.persist(task);
    }
  }

  async cancel(taskId: string): Promise<void> {
    if (this.activeTaskId === taskId) {
      this.generateNote.cancel(); return;
    }
    const task: GenerationTask | undefined = await this.tasks.getById(taskId);
    if (task === undefined || task.status !== 'queued') return;
    task.status = 'cancelled'; task.stage = 'cancelled'; task.message = '生成已取消';
    task.retryable = false; await this.persist(task);
  }

  cancelActive(): void {
    if (this.activeTaskId.length > 0) this.generateNote.cancel();
  }

  private async startNextQueued(): Promise<void> {
    const queued: GenerationTask | undefined = (await this.tasks.list())
      .filter((task: GenerationTask) => task.status === 'queued')
      .sort((left: GenerationTask, right: GenerationTask) => left.createdAt - right.createdAt)[0];
    if (queued !== undefined) await this.start(queued.id);
  }

  private async persist(task: GenerationTask): Promise<void> {
    await this.tasks.save(task); this.notify(task);
  }
  private notify(task: GenerationTask): void {
    this.listeners.forEach((listener: GenerationTaskListener) => listener(task));
  }
}
