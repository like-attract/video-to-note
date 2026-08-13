import { describe, expect, it } from '@ohos/hypium';
import { MemoryLlmProfileRepository } from '../main/ets/data/memory/MemoryLlmProfileRepository';
import type { LlmConfig, LlmProfile } from '../main/ets/models/LlmModels';

function profile(id: string, name: string): LlmProfile {
  return {
    id: id, name: name, providerName: 'deepseek', baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-chat', compatibilityMode: 'deepseek', reasoningEffort: 'high', hasApiKey: false
  };
}

export default function memoryLlmProfileRepositoryTest(): void {
  describe('MemoryLlmProfileRepository', () => {
    it('selectsFirstProfileAsDefaultAndResolvesRunStyle', 0, async () => {
      const repository: MemoryLlmProfileRepository = new MemoryLlmProfileRepository();
      await repository.save(profile('one', '主配置'), 'sk-one');
      expect(await repository.getDefaultId()).assertEqual('one');
      const config: LlmConfig = await repository.resolve('one', 'concise');
      expect(config.apiKey).assertEqual('sk-one');
      expect(config.style).assertEqual('concise');
      expect(config.reasoningEffort).assertEqual('high');
      expect(config.compatibilityMode).assertEqual('deepseek');
    });

    it('fallsBackWhenDefaultProfileIsDeleted', 0, async () => {
      const repository: MemoryLlmProfileRepository = new MemoryLlmProfileRepository();
      await repository.save(profile('one', '配置一'), 'sk-one');
      await repository.save(profile('two', '配置二'), 'sk-two');
      await repository.setDefault('two');
      await repository.remove('two');
      expect(await repository.getDefaultId()).assertEqual('one');
      expect((await repository.list()).length).assertEqual(1);
    });

    it('keepsExistingSecretWhenEditingWithBlankKey', 0, async () => {
      const repository: MemoryLlmProfileRepository = new MemoryLlmProfileRepository();
      await repository.save(profile('one', '原名称'), 'sk-one');
      await repository.save(profile('one', '新名称'));
      expect((await repository.resolve('one', 'faithful')).apiKey).assertEqual('sk-one');
      expect((await repository.list())[0].name).assertEqual('新名称');
    });

    it('keepsAutoReasoningWhenProfileIsEdited', 0, async () => {
      const repository: MemoryLlmProfileRepository = new MemoryLlmProfileRepository();
      const saved: LlmProfile = profile('auto', '自动思考');
      saved.reasoningEffort = 'auto';
      saved.compatibilityMode = 'auto';
      await repository.save(saved, 'sk-auto');
      const edited: LlmProfile = profile('auto', '自动思考（编辑）');
      edited.reasoningEffort = 'auto';
      edited.compatibilityMode = 'auto';
      await repository.save(edited);
      expect((await repository.list())[0].reasoningEffort).assertEqual('auto');
      expect((await repository.resolve('auto', 'faithful')).reasoningEffort).assertEqual('auto');
    });
  });
}
