import type { SubtitlePreview } from '../../models/BiliModels';
import type { GenerationTask } from '../../models/GenerationTask';
import type { NoteStyle } from '../../models/LlmModels';

export interface GenerationTaskRepository {
  create(preview: SubtitlePreview, profileId: string, style: NoteStyle): Promise<GenerationTask>;
  list(): Promise<GenerationTask[]>;
  getById(taskId: string): Promise<GenerationTask | undefined>;
  save(task: GenerationTask): Promise<void>;
  remove(taskId: string): Promise<void>;
  recoverInterrupted(): Promise<void>;
  clearAll(): Promise<void>;
}
