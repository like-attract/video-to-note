import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';

export class PreferenceEntry {
  key: string;
  value: string;
  constructor(key: string, value: string) {
    this.key = key;
    this.value = value;
  }
}

export class PreferencesStore {
  private readonly context: common.Context;
  private readonly name: string;
  private preferencesPromise?: Promise<preferences.Preferences>;

  constructor(context: common.Context, name: string) {
    this.context = context;
    this.name = name;
  }

  private getPreferences(): Promise<preferences.Preferences> {
    if (this.preferencesPromise === undefined) {
      this.preferencesPromise = preferences.getPreferences(this.context, { name: this.name });
    }
    return this.preferencesPromise;
  }

  async getString(key: string, fallback: string = ''): Promise<string> {
    const store: preferences.Preferences = await this.getPreferences();
    const value: preferences.ValueType = await store.get(key, fallback);
    return typeof value === 'string' ? value : fallback;
  }

  async putStrings(entries: PreferenceEntry[]): Promise<void> {
    const store: preferences.Preferences = await this.getPreferences();
    for (let index: number = 0; index < entries.length; index++) {
      await store.put(entries[index].key, entries[index].value);
    }
    await store.flush();
  }

  async remove(key: string): Promise<void> {
    const store: preferences.Preferences = await this.getPreferences();
    await store.delete(key);
    await store.flush();
  }

  async removeMany(keys: string[]): Promise<void> {
    const store: preferences.Preferences = await this.getPreferences();
    for (let index: number = 0; index < keys.length; index++) await store.delete(keys[index]);
    await store.flush();
  }

  async clear(): Promise<void> {
    const store: preferences.Preferences = await this.getPreferences();
    await store.clear();
    await store.flush();
  }
}
