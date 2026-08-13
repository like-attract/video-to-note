export interface ModelPreset {
  id: string;
  label: string;
}

export interface ProviderPreset {
  id: string;
  label: string;
  baseUrl: string;
  defaultModel: string;
  models: ModelPreset[];
}

export const CUSTOM_MODEL_ID: string = '__custom__';

export const LLM_PROVIDERS: ProviderPreset[] = [
  {
    id: 'deepseek', label: 'DeepSeek', baseUrl: 'https://api.deepseek.com',
    defaultModel: 'deepseek-v4-flash', models: [
      { id: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
      { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' }
    ]
  },
  {
    id: 'openai', label: 'OpenAI', baseUrl: 'https://api.openai.com/v1',
    defaultModel: 'gpt-5.6-terra', models: [
      { id: 'gpt-5.6-sol', label: 'GPT-5.6 Sol' },
      { id: 'gpt-5.6-terra', label: 'GPT-5.6 Terra' },
      { id: 'gpt-5.6-luna', label: 'GPT-5.6 Luna' }
    ]
  },
  {
    id: 'glm', label: '智谱 GLM', baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    defaultModel: 'glm-4.5-flash', models: [
      { id: 'glm-5.2', label: 'GLM-5.2' },
      { id: 'glm-4.5-flash', label: 'GLM-4.5-Flash（免费）' }
    ]
  },
  {
    id: 'qwen', label: '通义千问', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    defaultModel: 'qwen3.7-plus', models: [
      { id: 'qwen3.7-max', label: 'Qwen3.7 Max' },
      { id: 'qwen3.7-plus', label: 'Qwen3.7 Plus' },
      { id: 'qwen3.7-flash', label: 'Qwen3.7 Flash' }
    ]
  },
  {
    id: 'moonshot', label: '月之暗面 Kimi', baseUrl: 'https://api.moonshot.cn/v1',
    defaultModel: 'kimi-k3', models: [
      { id: 'kimi-k3', label: 'Kimi K3' },
      { id: 'kimi-k2.6', label: 'Kimi K2.6' },
      { id: 'kimi-k2.7-code-highspeed', label: 'Kimi K2.7 Code Highspeed' }
    ]
  },
  { id: 'custom', label: '自定义接口', baseUrl: '', defaultModel: CUSTOM_MODEL_ID, models: [] }
];

export function getProviderPreset(id: string): ProviderPreset {
  return LLM_PROVIDERS.find((item: ProviderPreset) => item.id === id) || LLM_PROVIDERS[5];
}
