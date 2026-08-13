import { abilityDelegatorRegistry } from '@kit.TestKit';
import { common } from '@kit.AbilityKit';
import { describe, it, expect } from '@ohos/hypium';
import { SecureSecretStore } from '../../../main/ets/data/local/SecureSecretStore';
import { SecureBiliSessionRepository } from '../../../main/ets/data/local/SecureBiliSessionRepository';
import { PersistentLlmProfileRepository } from '../../../main/ets/data/local/PersistentLlmProfileRepository';
import { RdbNoteRepository } from '../../../main/ets/data/local/RdbNoteRepository';
import type { BiliSession } from '../../../main/ets/models/BiliModels';
import type { LlmConfig, LlmProfile, NoteRecord } from '../../../main/ets/models/LlmModels';
import { sessionStore } from '../../../main/ets/stores/SessionStore';
import { relationalStore } from '@kit.ArkData';

async function abilityContext(): Promise<common.Context> {
  return (await abilityDelegatorRegistry.getAbilityDelegator().getCurrentTopAbility()).context;
}

export default function persistenceTest() {
  describe('PersistenceTest', () => {
    it('restoresEncryptedSessionAcrossRepositoryInstances', 0, async () => {
      const secretName: string = `test_session_secrets_${Date.now()}`;
      const context: common.Context = await abilityContext();
      const secrets: SecureSecretStore = new SecureSecretStore(
        context, secretName, `videotono.test.session.${Date.now()}`);
      const first: SecureBiliSessionRepository = new SecureBiliSessionRepository(secrets);
      const session: BiliSession = {
        cookieHeader: 'SESSDATA=test-value; bili_jct=test-csrf',
        refreshToken: 'test-refresh', createdAt: Date.now()
      };
      await first.save(session);
      sessionStore.clear();
      const second: SecureBiliSessionRepository = new SecureBiliSessionRepository(secrets);
      const restored: BiliSession | undefined = await second.getCurrent();
      expect(restored?.cookieHeader).assertEqual(session.cookieHeader);
      expect(restored?.refreshToken).assertEqual(session.refreshToken);
      await second.clear();
      expect(await new SecureBiliSessionRepository(secrets).getCurrent()).assertUndefined();
      await secrets.clearAndDeleteKey();
    });

    it('restoresLlmProfilesAndEncryptedApiKey', 0, async () => {
      const suffix: string = Date.now().toString();
      const context: common.Context = await abilityContext();
      const secrets: SecureSecretStore = new SecureSecretStore(
        context, `test_llm_secrets_${suffix}`, `videotono.test.llm.${suffix}`);
      const profilesName: string = `test_llm_profiles_${suffix}`;
      const legacyName: string = `test_llm_legacy_${suffix}`;
      const first: PersistentLlmProfileRepository = new PersistentLlmProfileRepository(
        context, secrets, profilesName, legacyName);
      const saved: LlmProfile = {
        id: 'primary', name: '主配置', providerName: 'deepseek',
        baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat',
        reasoningEffort: 'auto', hasApiKey: false
      };
      await first.save(saved, 'sk-test');
      await first.setDefault(saved.id);
      const second: PersistentLlmProfileRepository = new PersistentLlmProfileRepository(
        context, secrets, profilesName, legacyName);
      const restored: LlmProfile[] = await second.list();
      expect(restored.length).assertEqual(1);
      expect(restored[0].name).assertEqual(saved.name);
      expect(restored[0].reasoningEffort).assertEqual('auto');
      expect(await second.getDefaultId()).assertEqual(saved.id);
      const runtime: LlmConfig = await second.resolve(saved.id, 'detailed');
      expect(runtime.apiKey).assertEqual('sk-test');
      expect(runtime.style).assertEqual('detailed');
      await second.remove(saved.id);
      expect((await second.list()).length).assertEqual(0);
      expect(await secrets.get('llm.profile.primary.apiKey')).assertUndefined();
      await second.clearAll();
      await secrets.clearAndDeleteKey();
    });

    it('persistsAndClearsNotesInRdb', 0, async () => {
      const databaseName: string = `test_notes_${Date.now()}.db`;
      const context: common.Context = await abilityContext();
      const first: RdbNoteRepository = new RdbNoteRepository(context, databaseName);
      const note: NoteRecord = {
        id: `note-${Date.now()}`, bvid: 'BV1xx411c7mD', cid: 42,
        noteTitle: '我的持久化笔记', videoTitle: '持久化测试',
        markdown: '# 测试\n\n[00:01] 正文', createdAt: Date.now()
      };
      await first.save(note);
      const second: RdbNoteRepository = new RdbNoteRepository(context, databaseName);
      expect((await second.getById(note.id))?.markdown).assertEqual(note.markdown);
      expect((await second.list()).length).assertEqual(1);
      await second.renameTitle(note.id, '重命名后的笔记');
      const renamed: NoteRecord | undefined = await second.getById(note.id);
      expect(renamed?.noteTitle).assertEqual('重命名后的笔记');
      expect(renamed?.videoTitle).assertEqual(note.videoTitle);
      expect(renamed?.markdown).assertEqual(note.markdown);
      await second.clearAll();
      expect((await second.list()).length).assertEqual(0);
    });

    it('migratesV1AndInterruptedV2Schemas', 0, async () => {
      const context: common.Context = await abilityContext();
      const v1Name: string = `test_notes_v1_${Date.now()}.db`;
      const v1: relationalStore.RdbStore = await relationalStore.getRdbStore(context, {
        name: v1Name, securityLevel: relationalStore.SecurityLevel.S1
      });
      await v1.executeSql('CREATE TABLE notes (' +
        'id TEXT PRIMARY KEY, bvid TEXT NOT NULL, cid INTEGER NOT NULL, ' +
        'video_title TEXT NOT NULL, markdown TEXT NOT NULL, created_at INTEGER NOT NULL)');
      await v1.executeSql("INSERT INTO notes VALUES ('old','BV1234567890',1,'旧视频','# 旧正文',1)");
      v1.version = 1;
      const migrated: NoteRecord | undefined =
        await new RdbNoteRepository(context, v1Name).getById('old');
      expect(migrated?.noteTitle).assertEqual('旧视频');
      expect(migrated?.markdown).assertEqual('# 旧正文');

      const interruptedName: string = `test_notes_interrupted_${Date.now()}.db`;
      const interrupted: relationalStore.RdbStore = await relationalStore.getRdbStore(context, {
        name: interruptedName, securityLevel: relationalStore.SecurityLevel.S1
      });
      await interrupted.executeSql('CREATE TABLE notes (' +
        'id TEXT PRIMARY KEY, bvid TEXT NOT NULL, cid INTEGER NOT NULL, ' +
        "video_title TEXT NOT NULL, markdown TEXT NOT NULL, created_at INTEGER NOT NULL, " +
        "note_title TEXT NOT NULL DEFAULT '')");
      await interrupted.executeSql(
        "INSERT INTO notes VALUES ('half','BV1234567890',1,'中断视频','# 正文',1,'')");
      interrupted.version = 1;
      const recovered: NoteRecord | undefined =
        await new RdbNoteRepository(context, interruptedName).getById('half');
      expect(recovered?.noteTitle).assertEqual('中断视频');
    });
  });
}
