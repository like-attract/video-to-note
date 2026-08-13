import type { SubtitlePreview } from './BiliModels';
import type { NoteStyle } from './LlmModels';

export type GenerationTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' |
  'cancelled' | 'interrupted';

export interface GenerationTask {
  id: string;
  status: GenerationTaskStatus;
  stage: string;
  progress: number;
  message: string;
  preview: SubtitlePreview;
  profileId: string;
  style: NoteStyle;
  noteId: string;
  attempt: number;
  errorCode: string;
  errorMessage: string;
  retryable: boolean;
  createdAt: number;
  updatedAt: number;
}
