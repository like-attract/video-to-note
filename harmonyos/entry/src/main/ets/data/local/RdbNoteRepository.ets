import { relationalStore } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import type { NoteRepository } from '../../domain/notes/NoteRepository';
import type { NoteRecord } from '../../models/LlmModels';

const NOTE_TABLE: string = 'notes';

export class RdbNoteRepository implements NoteRepository {
  private readonly context: common.Context;
  private readonly databaseName: string;
  private storePromise?: Promise<relationalStore.RdbStore>;

  constructor(context: common.Context, databaseName: string = 'videotono.db') {
    this.context = context;
    this.databaseName = databaseName;
  }

  async list(): Promise<NoteRecord[]> {
    const store: relationalStore.RdbStore = await this.getStore();
    const predicates: relationalStore.RdbPredicates =
      new relationalStore.RdbPredicates(NOTE_TABLE).orderByDesc('created_at');
    const result: relationalStore.ResultSet = await store.query(predicates);
    const notes: NoteRecord[] = [];
    try {
      while (result.goToNextRow()) notes.push(this.readNote(result));
      return notes;
    } finally {
      result.close();
    }
  }

  async getById(id: string): Promise<NoteRecord | undefined> {
    const store: relationalStore.RdbStore = await this.getStore();
    const predicates: relationalStore.RdbPredicates =
      new relationalStore.RdbPredicates(NOTE_TABLE).equalTo('id', id);
    const result: relationalStore.ResultSet = await store.query(predicates);
    try {
      return result.goToNextRow() ? this.readNote(result) : undefined;
    } finally {
      result.close();
    }
  }

  async save(note: NoteRecord): Promise<void> {
    const values: relationalStore.ValuesBucket = {
      id: note.id,
      bvid: note.bvid,
      cid: note.cid,
      note_title: note.noteTitle,
      video_title: note.videoTitle,
      markdown: note.markdown,
      created_at: note.createdAt
    };
    await (await this.getStore()).insert(
      NOTE_TABLE, values, relationalStore.ConflictResolution.ON_CONFLICT_REPLACE);
  }

  async renameTitle(id: string, title: string): Promise<void> {
    const values: relationalStore.ValuesBucket = { note_title: title };
    const predicates: relationalStore.RdbPredicates =
      new relationalStore.RdbPredicates(NOTE_TABLE).equalTo('id', id);
    await (await this.getStore()).update(values, predicates);
  }

  async deleteById(id: string): Promise<void> {
    const predicates: relationalStore.RdbPredicates =
      new relationalStore.RdbPredicates(NOTE_TABLE).equalTo('id', id);
    await (await this.getStore()).delete(predicates);
  }

  async clearAll(): Promise<void> {
    await (await this.getStore()).delete(new relationalStore.RdbPredicates(NOTE_TABLE));
  }

  private getStore(): Promise<relationalStore.RdbStore> {
    if (this.storePromise === undefined) this.storePromise = this.openStore();
    return this.storePromise;
  }

  private async openStore(): Promise<relationalStore.RdbStore> {
    const config: relationalStore.StoreConfig = {
      name: this.databaseName,
      securityLevel: relationalStore.SecurityLevel.S1
    };
    const store: relationalStore.RdbStore = await relationalStore.getRdbStore(this.context, config);
    if (store.version < 1) {
      await store.executeSql(
        'CREATE TABLE IF NOT EXISTS notes (' +
        'id TEXT PRIMARY KEY, bvid TEXT NOT NULL, cid INTEGER NOT NULL, ' +
        'note_title TEXT NOT NULL, video_title TEXT NOT NULL, ' +
        'markdown TEXT NOT NULL, created_at INTEGER NOT NULL)');
    }
    if (store.version < 2) {
      if (!await this.hasColumn(store, 'note_title')) {
        await store.executeSql(
          "ALTER TABLE notes ADD COLUMN note_title TEXT NOT NULL DEFAULT ''");
      }
      await store.executeSql(
        "UPDATE notes SET note_title = video_title WHERE note_title = ''");
      store.version = 2;
    }
    return store;
  }

  private async hasColumn(store: relationalStore.RdbStore, column: string): Promise<boolean> {
    const result: relationalStore.ResultSet = await store.querySql(`PRAGMA table_info(${NOTE_TABLE})`);
    try {
      const nameIndex: number = result.getColumnIndex('name');
      while (result.goToNextRow()) {
        if (result.getString(nameIndex) === column) return true;
      }
      return false;
    } finally {
      result.close();
    }
  }

  private readNote(result: relationalStore.ResultSet): NoteRecord {
    return {
      id: result.getString(result.getColumnIndex('id')),
      bvid: result.getString(result.getColumnIndex('bvid')),
      cid: result.getLong(result.getColumnIndex('cid')),
      noteTitle: result.getString(result.getColumnIndex('note_title')),
      videoTitle: result.getString(result.getColumnIndex('video_title')),
      markdown: result.getString(result.getColumnIndex('markdown')),
      createdAt: result.getLong(result.getColumnIndex('created_at'))
    };
  }
}
