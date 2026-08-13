import { describe, expect, it } from '@ohos/hypium';
import { MemoryNoteRepository } from '../main/ets/data/memory/MemoryNoteRepository';
import type { NoteRecord } from '../main/ets/models/LlmModels';

export default function memoryNoteRepositoryTest(): void {
  describe('MemoryNoteRepository', () => {
    it('saves updates and deletes notes', 0, async () => {
      const repository: MemoryNoteRepository = new MemoryNoteRepository();
      const note: NoteRecord = {
        id: 'note-1', bvid: 'BV1234567890', cid: 1,
        noteTitle: '我的笔记', videoTitle: '标题', markdown: '# 初稿', createdAt: 1
      };
      await repository.save(note);
      await repository.save({
        id: note.id, bvid: note.bvid, cid: note.cid,
        noteTitle: note.noteTitle, videoTitle: note.videoTitle,
        markdown: '# 更新', createdAt: note.createdAt
      });
      expect((await repository.list()).length).assertEqual(1);
      expect((await repository.getById(note.id))?.markdown).assertEqual('# 更新');
      await repository.renameTitle(note.id, '新标题');
      expect((await repository.getById(note.id))?.noteTitle).assertEqual('新标题');
      await repository.deleteById(note.id);
      expect((await repository.list()).length).assertEqual(0);
    });
  });
}
