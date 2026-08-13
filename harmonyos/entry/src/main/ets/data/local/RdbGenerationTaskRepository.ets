import { common } from '@kit.AbilityKit';
import { relationalStore } from '@kit.ArkData';
import type { SubtitlePreview } from '../../models/BiliModels';
import type { GenerationTask } from '../../models/GenerationTask';
import type { NoteStyle } from '../../models/LlmModels';
import type { GenerationTaskRepository } from '../../domain/generation/GenerationTaskRepository';

const TASK_TABLE: string = 'generation_tasks';

export class RdbGenerationTaskRepository implements GenerationTaskRepository {
  private readonly context: common.Context;
  private storePromise?: Promise<relationalStore.RdbStore>;
  constructor(context: common.Context) { this.context = context; }

  async create(preview: SubtitlePreview, profileId: string, style: NoteStyle): Promise<GenerationTask> {
    const now: number = Date.now();
    const task: GenerationTask = {
      id: `task-${now}-${Math.floor(Math.random() * 100000)}`, status: 'queued', stage: 'queued',
      progress: 0, message: '等待生成', preview: preview, profileId: profileId, style: style,
      noteId: '', attempt: 0, errorCode: '', errorMessage: '', retryable: true,
      createdAt: now, updatedAt: now
    };
    await this.save(task); return task;
  }
  async list(): Promise<GenerationTask[]> {
    const predicates: relationalStore.RdbPredicates =
      new relationalStore.RdbPredicates(TASK_TABLE).orderByDesc('updated_at');
    const result: relationalStore.ResultSet = await (await this.getStore()).query(predicates);
    const tasks: GenerationTask[] = [];
    try { while (result.goToNextRow()) tasks.push(this.readTask(result)); return tasks; }
    finally { result.close(); }
  }
  async getById(taskId: string): Promise<GenerationTask | undefined> {
    const predicates: relationalStore.RdbPredicates =
      new relationalStore.RdbPredicates(TASK_TABLE).equalTo('id', taskId);
    const result: relationalStore.ResultSet = await (await this.getStore()).query(predicates);
    try { return result.goToNextRow() ? this.readTask(result) : undefined; }
    finally { result.close(); }
  }
  async save(task: GenerationTask): Promise<void> {
    task.updatedAt = Date.now();
    const values: relationalStore.ValuesBucket = {
      id: task.id, status: task.status, updated_at: task.updatedAt, payload: JSON.stringify(task)
    };
    await (await this.getStore()).insert(TASK_TABLE, values,
      relationalStore.ConflictResolution.ON_CONFLICT_REPLACE);
  }
  async remove(taskId: string): Promise<void> {
    await (await this.getStore()).delete(
      new relationalStore.RdbPredicates(TASK_TABLE).equalTo('id', taskId));
  }
  async recoverInterrupted(): Promise<void> {
    const tasks: GenerationTask[] = await this.list();
    for (let index: number = 0; index < tasks.length; index++) {
      const task: GenerationTask = tasks[index];
      if (task.status === 'running') {
        task.status = 'interrupted'; task.stage = 'interrupted';
        task.message = '上次生成被系统中断，可以重新生成'; task.retryable = true;
        await this.save(task);
      }
    }
  }
  async clearAll(): Promise<void> {
    await (await this.getStore()).delete(new relationalStore.RdbPredicates(TASK_TABLE));
  }
  private getStore(): Promise<relationalStore.RdbStore> {
    if (this.storePromise === undefined) this.storePromise = this.openStore();
    return this.storePromise;
  }
  private async openStore(): Promise<relationalStore.RdbStore> {
    const config: relationalStore.StoreConfig = {
      name: 'videotono_tasks.db', securityLevel: relationalStore.SecurityLevel.S1
    };
    const store: relationalStore.RdbStore = await relationalStore.getRdbStore(this.context, config);
    if (store.version < 1) {
      await store.executeSql('CREATE TABLE IF NOT EXISTS generation_tasks (' +
        'id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at INTEGER NOT NULL, payload TEXT NOT NULL)');
      store.version = 1;
    }
    return store;
  }
  private readTask(result: relationalStore.ResultSet): GenerationTask {
    return JSON.parse(result.getString(result.getColumnIndex('payload'))) as GenerationTask;
  }
}
