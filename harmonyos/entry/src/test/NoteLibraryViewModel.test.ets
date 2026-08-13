import { describe, expect, it } from '@ohos/hypium';
import { NoteLibraryViewModel } from '../main/ets/features/library/NoteLibraryViewModel';
import type { NoteRecord } from '../main/ets/models/LlmModels';

function note(id: string, title: string, markdown: string, createdAt: number): NoteRecord {
  return {
    id: id, bvid: `BV${id.padEnd(10, '0')}`, cid: createdAt,
    noteTitle: title, videoTitle: `来源${title}`, markdown: markdown, createdAt: createdAt
  };
}

export default function noteLibraryViewModelTest(): void {
  describe('NoteLibraryViewModel', () => {
    it('filtersAndSortsNotes', 0, () => {
      const viewModel: NoteLibraryViewModel = new NoteLibraryViewModel();
      const notes: NoteRecord[] = [
        note('1', 'Beta', '苹果正文', 20), note('2', 'Alpha', '香蕉正文', 10)
      ];
      expect(viewModel.visible(notes, '', 'newest')[0].noteTitle).assertEqual('Beta');
      expect(viewModel.visible(notes, '', 'oldest')[0].noteTitle).assertEqual('Alpha');
      expect(viewModel.visible(notes, '', 'title')[0].noteTitle).assertEqual('Alpha');
      expect(viewModel.visible(notes, '香蕉', 'newest').length).assertEqual(1);
      expect(viewModel.excerpt('# 标题\n\n- 正文').includes('#')).assertFalse();
    });
  });
}
