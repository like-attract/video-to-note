import type { SubtitlePreview } from '../../models/BiliModels';
import type { GenerationTask } from '../../models/GenerationTask';
import type { NoteStyle } from '../../models/LlmModels';
import type { GenerationTaskRepository } from '../../domain/generation/GenerationTaskRepository';

export class MemoryGenerationTaskRepository implements GenerationTaskRepository {
  private tasks: GenerationTask[] = [];
  async create(preview: SubtitlePreview, profileId: string, style: NoteStyle): Promise<GenerationTask> {
    const now: number = Date.now();
    const task: GenerationTask = { id: `task-${now}-${Math.floor(Math.random() * 100000)}`,
      status: 'queued', stage: 'queued', progress: 0,
      message: '等待生成', preview: preview, profileId: profileId, style: style, noteId: '', attempt: 0,
      errorCode: '', errorMessage: '', retryable: true, createdAt: now, updatedAt: now };
    this.tasks.unshift(task); return task;
  }
  async list(): Promise<GenerationTask[]> { return this.tasks.slice(); }
  async getById(taskId: string): Promise<GenerationTask | undefined> {
    return this.tasks.find((task: GenerationTask) => task.id === taskId);
  }
  async save(task: GenerationTask): Promise<void> {
    const index: number = this.tasks.findIndex((item: GenerationTask) => item.id === task.id);
    if (index >= 0) this.tasks[index] = task; else this.tasks.unshift(task);
  }
  async remove(taskId: string): Promise<void> {
    this.tasks = this.tasks.filter((task: GenerationTask) => task.id !== taskId);
  }
  async recoverInterrupted(): Promise<void> {
    this.tasks.forEach((task: GenerationTask) => {
      if (task.status === 'running') {
        task.status = 'interrupted'; task.stage = 'interrupted'; task.message = '任务已中断'; task.retryable = true;
      }
    });
  }
  async clearAll(): Promise<void> { this.tasks = []; }
}
