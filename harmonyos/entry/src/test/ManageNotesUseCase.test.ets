import { describe, expect, it } from '@ohos/hypium';
import { ManageNotesUseCase } from '../main/ets/domain/notes/ManageNotesUseCase';
import { MemoryNoteRepository } from '../main/ets/data/memory/MemoryNoteRepository';
import { MemoryGenerationRepository } from '../main/ets/data/memory/MemoryGenerationRepository';
import type { NoteRecord } from '../main/ets/models/LlmModels';
import { workStore } from '../main/ets/stores/WorkStore';

export default function manageNotesUseCaseTest(): void {
  describe('ManageNotesUseCase', () => {
    it('opensRenamesAndDeletesWithoutChangingSource', 0, async () => {
      workStore.clearAll();
      const notes: MemoryNoteRepository = new MemoryNoteRepository();
      const generation: MemoryGenerationRepository = new MemoryGenerationRepository();
      const useCase: ManageNotesUseCase = new ManageNotesUseCase(notes, generation);
      const original: NoteRecord = {
        id: 'note-1', bvid: 'BV1234567890', cid: 1, noteTitle: '原笔记标题',
        videoTitle: 'B站原视频标题', markdown: '# 正文', createdAt: 100
      };
      await notes.save(original);
      await useCase.rename(original.id, '新笔记标题');
      const renamed: NoteRecord | undefined = await notes.getById(original.id);
      expect(renamed?.noteTitle).assertEqual('新笔记标题');
      expect(renamed?.videoTitle).assertEqual('B站原视频标题');
      expect(renamed?.markdown).assertEqual('# 正文');
      await useCase.open(original.id);
      expect((await generation.getWorkspace()).note?.id).assertEqual(original.id);
      await useCase.delete(original.id);
      expect((await notes.list()).length).assertEqual(0);
      expect((await generation.getWorkspace()).note).assertUndefined();
    });
  });
}
