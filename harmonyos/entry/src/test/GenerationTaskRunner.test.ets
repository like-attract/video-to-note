import { describe, expect, it } from '@ohos/hypium';
import { MemoryGenerationRepository } from '../main/ets/data/memory/MemoryGenerationRepository';
import { MemoryGenerationTaskRepository } from '../main/ets/data/memory/MemoryGenerationTaskRepository';
import { MemoryLlmProfileRepository } from '../main/ets/data/memory/MemoryLlmProfileRepository';
import { MemoryNoteRepository } from '../main/ets/data/memory/MemoryNoteRepository';
import { GenerateNoteUseCase } from '../main/ets/domain/generation/GenerateNoteUseCase';
import { GenerationTaskRunner } from '../main/ets/domain/generation/GenerationTaskRunner';
import type { GenerationProgress, NoteGenerator } from '../main/ets/domain/generation/NoteGenerator';
import type { BiliPage, BiliVideo, SubtitlePreview, TranscriptSegment } from '../main/ets/models/BiliModels';
import type { GenerationTask } from '../main/ets/models/GenerationTask';
import type { LlmConfig, LlmProfile } from '../main/ets/models/LlmModels';

class FakeNoteGenerator implements NoteGenerator {
  calls: number = 0;
  async generate(_config: LlmConfig, _video: BiliVideo, _page: BiliPage,
    _segments: TranscriptSegment[], onProgress?: GenerationProgress): Promise<string> {
    this.calls += 1;
    if (onProgress !== undefined) onProgress(60, '正在生成');
    return '# 测试笔记';
  }
  cancelCurrent(): void {}
}

function preview(): SubtitlePreview {
  const page: BiliPage = { cid: 1, page: 1, part: 'P1', durationSeconds: 60 };
  return {
    video: { bvid: 'BV1test', title: '测试视频', ownerName: '作者', coverUrl: '', pages: [page] },
    page: page,
    track: { language: 'zh', displayName: '中文', url: '', isAiGenerated: false },
    segments: [{ startSeconds: 0, endSeconds: 2, content: '测试字幕' }]
  };
}

export default function generationTaskRunnerTest(): void {
  describe('GenerationTaskRunner', () => {
    it('persistsSuccessAndUsesTaskIdAsNoteId', 0, async () => {
      const tasks: MemoryGenerationTaskRepository = new MemoryGenerationTaskRepository();
      const profiles: MemoryLlmProfileRepository = new MemoryLlmProfileRepository();
      const notes: MemoryNoteRepository = new MemoryNoteRepository();
      const generator: FakeNoteGenerator = new FakeNoteGenerator();
      const profile: LlmProfile = { id: 'profile', name: '测试', providerName: 'deepseek',
        baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat', compatibilityMode: 'auto',
        reasoningEffort: 'auto', hasApiKey: false };
      await profiles.save(profile, 'sk-test');
      const task: GenerationTask = await tasks.create(preview(), profile.id, 'faithful');
      const useCase: GenerateNoteUseCase = new GenerateNoteUseCase(
        generator, new MemoryGenerationRepository(), notes);
      const runner: GenerationTaskRunner = new GenerationTaskRunner(tasks, profiles, useCase, notes);
      await runner.start(task.id);
      const saved: GenerationTask | undefined = await tasks.getById(task.id);
      expect(saved?.status).assertEqual('succeeded');
      expect(saved?.noteId).assertEqual(task.id);
      expect((await notes.list()).length).assertEqual(1);
      expect((await notes.list())[0].generation?.model).assertEqual('deepseek-chat');
      expect((await notes.list())[0].generation?.style).assertEqual('faithful');
      await runner.start(task.id);
      expect(generator.calls).assertEqual(1);
    });

    it('marksRunningTaskInterruptedDuringRecovery', 0, async () => {
      const tasks: MemoryGenerationTaskRepository = new MemoryGenerationTaskRepository();
      const task: GenerationTask = await tasks.create(preview(), 'profile', 'faithful');
      task.status = 'running'; await tasks.save(task); await tasks.recoverInterrupted();
      expect((await tasks.getById(task.id))?.status).assertEqual('interrupted');
    });

    it('runsQueuedTasksInCreationOrder', 0, async () => {
      const tasks: MemoryGenerationTaskRepository = new MemoryGenerationTaskRepository();
      const profiles: MemoryLlmProfileRepository = new MemoryLlmProfileRepository();
      const notes: MemoryNoteRepository = new MemoryNoteRepository();
      const generator: FakeNoteGenerator = new FakeNoteGenerator();
      const profile: LlmProfile = { id: 'profile', name: '测试', providerName: 'deepseek',
        baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat', compatibilityMode: 'auto',
        reasoningEffort: 'auto', hasApiKey: false };
      await profiles.save(profile, 'sk-test');
      const first: GenerationTask = await tasks.create(preview(), profile.id, 'faithful');
      const second: GenerationTask = await tasks.create(preview(), profile.id, 'concise');
      const runner: GenerationTaskRunner = new GenerationTaskRunner(tasks, profiles,
        new GenerateNoteUseCase(generator, new MemoryGenerationRepository(), notes), notes);
      await runner.start(first.id);
      expect(generator.calls).assertEqual(2);
      expect((await tasks.getById(second.id))?.status).assertEqual('succeeded');
    });

    it('cancelsAQueuedTaskWithoutCallingTheModel', 0, async () => {
      const tasks: MemoryGenerationTaskRepository = new MemoryGenerationTaskRepository();
      const profiles: MemoryLlmProfileRepository = new MemoryLlmProfileRepository();
      const notes: MemoryNoteRepository = new MemoryNoteRepository();
      const generator: FakeNoteGenerator = new FakeNoteGenerator();
      const task: GenerationTask = await tasks.create(preview(), 'profile', 'faithful');
      const runner: GenerationTaskRunner = new GenerationTaskRunner(tasks, profiles,
        new GenerateNoteUseCase(generator, new MemoryGenerationRepository(), notes), notes);
      await runner.cancel(task.id);
      expect((await tasks.getById(task.id))?.status).assertEqual('cancelled');
      expect(generator.calls).assertEqual(0);
    });
  });
}
