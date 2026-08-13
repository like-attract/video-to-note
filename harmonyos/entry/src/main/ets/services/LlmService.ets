import { http } from '@kit.NetworkKit';
import type { BiliPage, BiliVideo, TranscriptSegment } from '../models/BiliModels';
import type { LlmCompatibilityMode, LlmConfig, NoteStyle, ReasoningEffort } from '../models/LlmModels';
import { resolveCompatibilityMode } from '../models/LlmCompatibility';
import { TranscriptService } from './TranscriptService';
import { RequestScope } from '../core/network/RequestScope';
import { NetworkPolicy } from '../core/network/NetworkPolicy';

interface ChatMessage { role: string; content: string; }
interface ChatRequest {
  model: string;
  messages: ChatMessage[];
  max_tokens?: number;
  max_completion_tokens?: number;
  reasoning_effort?: string;
  thinking?: ThinkingSwitch;
  enable_thinking?: boolean;
}
interface ThinkingSwitch { type?: string; }
interface ChatChoiceMessage { content: string; }
interface ChatChoice { message: ChatChoiceMessage; finish_reason?: string; }
interface ChatResponse { choices: ChatChoice[]; }
interface ErrorDetail { message?: string; }
interface ErrorResponse { error?: ErrorDetail; message?: string; }
export type LlmProgressCallback = (progress: number, message: string) => void;

const SYSTEM_PROMPT: string =
  '你是视频笔记编辑。忠实理解语境与作者意图，允许结合上下文保守修正明显的口误、笔误和语音转写错误。';
const CHUNK_CHARACTERS: number = 9000;
const MERGE_CHARACTERS: number = 14000;

export class LlmService {
  private readonly transcriptService: TranscriptService = new TranscriptService();
  private readonly requests: RequestScope = new RequestScope();

  cancelCurrent(): void { this.requests.cancel(); }

  async summarize(config: LlmConfig, video: BiliVideo, page: BiliPage,
    segments: TranscriptSegment[], onProgress?: LlmProgressCallback): Promise<string> {
    this.validateConfig(config);
    if (segments.length === 0) throw new Error('没有可供总结的字幕。');
    onProgress?.(2, '正在分析字幕内容');
    const chunks: TranscriptSegment[][] = this.chunkSegments(segments);
    let source: string = '';
    if (chunks.length === 1) {
      source = this.transcriptService.toTimestampedText(chunks[0]);
    } else {
      let notes: string[] = [];
      for (let index: number = 0; index < chunks.length; index++) {
        const chunkProgress: number = 5 + Math.floor(52 * index / chunks.length);
        onProgress?.(chunkProgress, `正在整理字幕 ${index + 1}/${chunks.length}`);
        notes.push(await this.complete(config,
          this.chunkPrompt(video.title, index + 1, chunks.length, chunks[index]), 1200, 'notes'));
      }
      let level: number = 0;
      while (this.totalCharacters(notes) > MERGE_CHARACTERS) {
        const groups: string[][] = this.groupNotes(notes);
        if (groups.length >= notes.length) break;
        level++;
        const merged: string[] = [];
        for (let index: number = 0; index < groups.length; index++) {
          const mergeProgress: number = Math.min(75, 61 + level * 4 +
            Math.floor(4 * (index + 1) / groups.length));
          onProgress?.(mergeProgress, `正在归并第 ${level} 层内容 ${index + 1}/${groups.length}`);
          merged.push(await this.complete(config,
            this.mergePrompt(video.title, level, index + 1, groups.length, groups[index]), 1400, 'notes'));
        }
        notes = merged;
      }
      source = notes.map((note: string, index: number) =>
        `### 片段 ${index + 1}\n${note}`).join('\n\n');
    }
    onProgress?.(78, '正在生成完整 Markdown 笔记');
    const maxTokens: number = config.style === 'concise' ? 2400 : 4600;
    let markdown: string = await this.complete(config,
      this.notePrompt(video, page, source, config.style), maxTokens, 'notes');
    markdown = this.stripCodeFence(markdown);
    onProgress?.(91, '完整笔记初稿已生成');
    if (config.style === 'detailed') {
      onProgress?.(93, '正在补充点评与分析');
      const analysis: string = await this.complete(config,
        this.analysisPrompt(video.title, markdown), 3200, 'analysis');
      markdown = `${markdown.trim()}\n\n${this.stripCodeFence(analysis).trim()}`;
    }
    onProgress?.(99, '正在整理最终笔记');
    return `${markdown.trim()}\n\n---\n来源：https://www.bilibili.com/video/${video.bvid}?p=${page.page}`;
  }

  private validateConfig(config: LlmConfig): void {
    if (!config.baseUrl.trim().startsWith('https://')) {
      throw new Error('Base URL 必须使用 HTTPS。');
    }
    if (config.model.trim().length === 0) throw new Error('请输入模型名称。');
    if (config.apiKey.trim().length === 0) throw new Error('请输入 API Key。');
  }

  private async complete(config: LlmConfig, prompt: string, maxTokens: number,
    stage: string, effortOverride?: ReasoningEffort, allowEmptyRetry: boolean = true): Promise<string> {
    const messages: ChatMessage[] = [
      { role: 'system', content: SYSTEM_PROMPT },
      { role: 'user', content: prompt }
    ];
    const body: ChatRequest = { model: config.model.trim(), messages: messages };
    const effort: ReasoningEffort = effortOverride === undefined ?
      this.stageEffort(config.reasoningEffort, config.style, stage) : effortOverride;
    const compatibility: LlmCompatibilityMode = resolveCompatibilityMode(config);
    if (compatibility === 'openai' && config.model.trim().startsWith('gpt-5')) {
      body.max_completion_tokens = maxTokens;
    } else {
      body.max_tokens = compatibility === 'deepseek' && effort !== 'off' ?
        Math.max(maxTokens * 3, 12000) : maxTokens;
    }
    this.applyReasoning(body, config, effort);
    const response: http.HttpResponse = await this.requests.request(this.endpoint(config.baseUrl), {
        method: http.RequestMethod.POST,
        header: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'Authorization': `Bearer ${config.apiKey.trim()}`
        },
        extraData: JSON.stringify(body),
        expectDataType: http.HttpDataType.STRING,
        usingCache: false,
        connectTimeout: NetworkPolicy.ConnectTimeoutMs,
        readTimeout: NetworkPolicy.LlmReadTimeoutMs,
        maxLimit: NetworkPolicy.MaxResponseBytes
    });
    const raw: string = typeof response.result === 'string' ? response.result : String(response.result);
    const status: number = response.responseCode as number;
    if (status < 200 || status >= 300) throw new Error(this.httpError(status, raw));
    const parsed: ChatResponse = JSON.parse(raw) as ChatResponse;
    if (parsed.choices === undefined || parsed.choices.length === 0 ||
      parsed.choices[0].message?.content?.trim().length === 0) {
      const finishReason: string = parsed.choices?.[0]?.finish_reason || 'unknown';
      if (allowEmptyRetry && effort !== 'off' && this.canControlReasoning(config)) {
        return await this.complete(config, prompt, maxTokens, stage, 'off', false);
      }
      throw new Error(`模型未返回正文（finish_reason=${finishReason}）。`);
    }
    return parsed.choices[0].message.content.trim();
  }

  private applyReasoning(body: ChatRequest, config: LlmConfig, effort: ReasoningEffort): void {
    if (effort === 'auto') return;
    const enabled: boolean = effort !== 'off';
    const compatibility: LlmCompatibilityMode = resolveCompatibilityMode(config);
    if (compatibility === 'deepseek') {
      body.thinking = { type: enabled ? 'enabled' : 'disabled' };
      if (enabled) body.reasoning_effort = effort === 'max' ? 'max' : 'high';
    } else if (compatibility === 'glm') {
      body.thinking = { type: enabled ? 'enabled' : 'disabled' };
    } else if (compatibility === 'qwen') {
      body.enable_thinking = enabled;
    } else if (compatibility === 'openai') {
      body.reasoning_effort = effort === 'off' ? 'none' : (effort === 'max' ? 'xhigh' : effort);
    }
  }

  private stageEffort(selected: ReasoningEffort, style: NoteStyle, stage: string): ReasoningEffort {
    return selected;
  }

  private canControlReasoning(config: LlmConfig): boolean {
    const compatibility: LlmCompatibilityMode = resolveCompatibilityMode(config);
    return compatibility === 'deepseek' || compatibility === 'openai' ||
      compatibility === 'glm' || compatibility === 'qwen';
  }

  private endpoint(baseUrl: string): string {
    const normalized: string = baseUrl.trim().replace(/\/+$/, '');
    return normalized.endsWith('/chat/completions') ? normalized : `${normalized}/chat/completions`;
  }

  private httpError(status: number, raw: string): string {
    let detail: string = '';
    try {
      const errorBody: ErrorResponse = JSON.parse(raw) as ErrorResponse;
      detail = errorBody.error?.message || errorBody.message || '';
    } catch (_error) {}
    if (status === 401 || status === 403) return `API Key 无效或无权限（HTTP ${status}）。${detail}`;
    if (status === 404) return `LLM 地址或模型端点不存在（HTTP 404）。${detail}`;
    if (status === 429) return `LLM 请求受限或余额不足（HTTP 429）。${detail}`;
    return `LLM 请求失败（HTTP ${status}）。${detail}`;
  }

  private chunkSegments(segments: TranscriptSegment[]): TranscriptSegment[][] {
    const chunks: TranscriptSegment[][] = [];
    let current: TranscriptSegment[] = [];
    let size: number = 0;
    segments.forEach((segment: TranscriptSegment) => {
      const cost: number = segment.content.length + 32;
      if (current.length > 0 && size + cost > CHUNK_CHARACTERS) {
        chunks.push(current);
        current = [];
        size = 0;
      }
      current.push(segment);
      size += cost;
    });
    if (current.length > 0) chunks.push(current);
    return chunks;
  }

  private groupNotes(notes: string[]): string[][] {
    const groups: string[][] = [];
    let current: string[] = [];
    let size: number = 0;
    notes.forEach((note: string) => {
      if (current.length > 0 && size + note.length > MERGE_CHARACTERS) {
        groups.push(current); current = []; size = 0;
      }
      current.push(note); size += note.length;
    });
    if (current.length > 0) groups.push(current);
    return groups;
  }

  private totalCharacters(values: string[]): number {
    return values.reduce((total: number, value: string) => total + value.length, 0);
  }

  private chunkPrompt(title: string, index: number, total: number,
    segments: TranscriptSegment[]): string {
    return `视频标题：${title}\n这是第 ${index}/${total} 个连续片段。\n\n` +
      '请忠实压缩这个片段，供后续撰写完整笔记使用。保留重要事实、例子、论证关系和原有时间点；' +
      '结合上下文理解表达意图，不做评价，不补充外部知识。\n\n转录：\n' +
      this.transcriptService.toTimestampedText(segments);
  }

  private mergePrompt(title: string, level: number, index: number,
    total: number, notes: string[]): string {
    return `视频标题：${title}\n这是长视频内容的第 ${level} 层归并，第 ${index}/${total} 组。\n\n` +
      '请合并以下连续片段笔记，去除重复但保留观点、依据、例子、时间点和前后关系。' +
      '不要评价，不补充外部知识，只输出供最终成稿使用的连续材料。\n\n片段笔记：\n' +
      notes.join('\n\n');
  }

  private notePrompt(video: BiliVideo, page: BiliPage, source: string, style: NoteStyle): string {
    const task: string = style === 'concise' ?
      '写一份精炼的 Markdown 摘要，说明视频谈了什么、作者的主要观点，并列出少量最有用的时间点。' :
      '写一份翔实、自然的 Markdown 视频笔记。完整复原内容脉络、具体例子、核心观点及其依据，并在有帮助时加入关键时间点。不要加入外部知识或评价。';
    return task + '\n\n' +
      `视频标题：${video.title}\n作者：${video.ownerName || '未知'}\n` +
      `分P：P${page.page} ${page.part}\n文字来源：B站字幕\n\n` +
      '以准确理解语境和作者立场为先。时间点只能取自材料。可在上下文支持时直接修正明显的口误、' +
      '笔误或字幕错误；只有歧义会影响结论且无法可靠判断时，才在正文采用最可能的解释，并在文末用' +
      ' Markdown 脚注集中说明。不要在正文反复插入“原文如此”或“疑为字幕错误”。' +
      '结构按内容自然组织，不必凑固定模板。' +
      `标题使用：# 视频笔记：《${video.title}》\n\n材料：\n${source}`;
  }

  private analysisPrompt(title: string, draft: string): string {
    return '请针对这份视频笔记所对应的视频内容本身，补写一段独立的点评与分析，只输出可直接追加到笔记后的 Markdown。\n\n' +
      '点评对象是视频里的内容，而不是笔记的写法：分析作者观点、论据、逻辑、结论可靠性、信息价值、' +
      '适用场景和值得进一步思考的问题。可补充稳定背景知识，但必须标明“模型补充，未联网核验”。' +
      `不要评价笔记排版或文笔。\n\n标题：${title}\n\n已有笔记：\n${draft}`;
  }

  private stripCodeFence(value: string): string {
    const trimmed: string = value.trim();
    if (trimmed.startsWith('```markdown') && trimmed.endsWith('```'))
      return trimmed.slice(11, -3).trim();
    if (trimmed.startsWith('```') && trimmed.endsWith('```'))
      return trimmed.slice(3, -3).trim();
    return trimmed;
  }
}
