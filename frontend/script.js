const API_BASE = '/api';
const POLL_DELAY_MS = 2000;
const POLL_TIMEOUT_MS = 12000;
const MAX_POLL_ERRORS = 5;
const CUSTOM_MODEL_ID = '__custom__';
// 档案下拉里自定义档案的取值前缀（内置 Provider 直接用 provider 名）。
const CUSTOM_PROFILE_PREFIX = 'custom:';
// 所有偏好集中在一个命名空间对象里写入，"浏览器存储中没有任何密钥"因此是
// 单个函数（persistPrefs）的性质，而不是 13 个散装键的逐处审查结果。
const PREFS_KEY = 'vtn_prefs';
const PREFS_SCHEMA = 2;
const PREFS_DEBOUNCE_MS = 250;
// 旧版本每个设置一个散装键，仅用于一次性迁移，读取后立即删除。
const LEGACY_PREFERENCE_KEYS = [
    'llm_provider',
    'llm_model_id',
    'llm_model',
    'custom_base_url',
    'custom_model_name',
    'whisper_model',
    'screenshot_interval',
    'include_screenshots',
    'use_gpu',
    'summary_style',
    'processing_mode',
    'reasoning_effort',
    'theme'
];
const LEGACY_WHISPER_CONFIRM_PREFIX = 'whisper_confirm_';
// 字段名命中即拒绝写入浏览器存储（API Key 只允许待在当前页面内存中）。
const SECRET_FIELD_PATTERN = /key|token|secret|sessdata|jct|buvid|authorization|cookie/i;
const LEGACY_SENSITIVE_KEYS = [
    'custom_api_key',
    'bili_sessdata',
    'bili_jct',
    'bili_buvid3',
    'deepseek_api_key',
    'openai_api_key',
    'qwen_api_key',
    'zhipu_api_key',
    'moonshot_api_key',
    'save_api_key',
    'llm_api_key'
];

const PROVIDER_CONFIG = {
    deepseek: {
        name: 'DeepSeek',
        baseUrl: 'https://api.deepseek.com',
        defaultModel: 'deepseek-v4-flash',
        models: [
            ['deepseek-v4-flash', 'DeepSeek V4 Flash'],
            ['deepseek-v4-pro', 'DeepSeek V4 Pro']
        ]
    },
    openai: {
        name: 'OpenAI',
        baseUrl: 'https://api.openai.com/v1',
        defaultModel: 'gpt-5.6-terra',
        models: [
            ['gpt-5.6-sol', 'GPT-5.6 Sol'],
            ['gpt-5.6-terra', 'GPT-5.6 Terra'],
            ['gpt-5.6-luna', 'GPT-5.6 Luna']
        ]
    },
    glm: {
        name: '智谱 GLM',
        baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
        defaultModel: 'glm-4.5-flash',
        models: [
            ['glm-5.2', 'GLM-5.2'],
            ['glm-4.5-flash', 'GLM-4.5-Flash（免费）']
        ]
    },
    qwen: {
        name: '通义千问',
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        defaultModel: 'qwen3.7-plus',
        models: [
            ['qwen3.7-max', 'Qwen3.7 Max'],
            ['qwen3.7-plus', 'Qwen3.7 Plus'],
            ['qwen3.7-flash', 'Qwen3.7 Flash']
        ]
    },
    moonshot: {
        name: '月之暗面 Kimi',
        baseUrl: 'https://api.moonshot.cn/v1',
        defaultModel: 'kimi-k3',
        models: [
            ['kimi-k3', 'Kimi K3'],
            ['kimi-k2.6', 'Kimi K2.6'],
            ['kimi-k2.7-code-highspeed', 'Kimi K2.7 Code Highspeed']
        ]
    },
    custom: {
        name: '自定义接口',
        baseUrl: '',
        defaultModel: CUSTOM_MODEL_ID,
        models: []
    }
};

const LEGACY_PROVIDER_MAP = {
    deepseek: 'deepseek',
    openai: 'openai',
    openai_gpt4: 'openai',
    openai_gpt35: 'openai',
    qwen: 'qwen',
    glm: 'glm',
    moonshot: 'moonshot',
    custom: 'custom'
};

let currentTaskId = null;
// 上传上限以本机后端 /api/health 下发为准；后端未就绪时按这个回退（与后端默认值一致）
const DEFAULT_MAX_UPLOAD_MB = 2048;
let maxUploadBytes = DEFAULT_MAX_UPLOAD_MB * 1024 * 1024;
let maxUploadLabel = `${DEFAULT_MAX_UPLOAD_MB / 1024} GB`;
let currentMarkdown = '';
let currentHtml = '';
let pollTimer = null;
let pollErrorCount = 0;
let isSubmitting = false;
let isTaskActive = false;
let isDownloading = false;
let isCancelling = false;
// 结果区当前展示的是字幕稿还是笔记：标题、按钮文案与进度步数都跟着它走
let isTranscriptTask = false;
let elapsedTimer = null;
let elapsedBaseSeconds = 0;
let elapsedSyncedAt = 0;
let prefs = defaultPrefs();
let persistTimer = null;
let persistJob = null;

document.addEventListener('DOMContentLoaded', () => {
    window.__videoToNoReady = false;
    // 每一步单独容错：即使浏览器里残留旧版缓存的 HTML（元素缺失），
    // 也只影响对应功能，不会让整页按钮全部失效。
    safeStep(removeLegacySecrets, '清理旧数据');
    safeStep(initPreferences, '读取偏好设置');
    safeStep(bindEvents, '绑定页面事件');
    safeStep(toggleSourceType, '初始化来源切换');
    loadAppVersion();
    loadRecentTasks(true);
    window.__videoToNoReady = true;
});

function safeStep(action, label) {
    try {
        action();
    } catch (error) {
        console.error(`[VideoToNo] ${label} 初始化失败：`, error);
    }
}

async function loadAppVersion() {
    try {
        const response = await fetch(`${API_BASE}/health`, { cache: 'no-store' });
        const data = await readResponse(response, '读取版本失败');
        if (data.version) byId('appVersion').textContent = `v${data.version}`;
        applyUploadLimit(data.max_upload_mb);
    } catch {
        // 保留 HTML 中的构建版本，服务短暂未就绪不影响页面使用。
        applyUploadLimit(null);
    }
}

function applyUploadLimit(maxUploadMb) {
    const mb = Number(maxUploadMb) > 0 ? Math.floor(Number(maxUploadMb)) : DEFAULT_MAX_UPLOAD_MB;
    maxUploadBytes = mb * 1024 * 1024;
    maxUploadLabel = mb >= 1024 && mb % 1024 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
    const fileInfo = byId('fileInfo');
    if (fileInfo) {
        fileInfo.textContent = `支持 B 站/抖音等视频链接与最大 ${maxUploadLabel} 的本地视频`;
    }
}

function bindEvents() {
    bindListener('resetConfigBtn', 'click', resetPreferences);
    bindListener('submitBtn', 'click', () => startSummary());
    bindListener('retryBtn', 'click', () => startSummary({ resumeCurrent: true }));
    bindListener('restartBtn', 'click', () => startSummary({ forceRestart: true }));
    // 字幕稿跑完后这个按钮的含义是"改用大模型出笔记"：先把输出类型切回笔记再提交
    bindListener('regenerateBtn', 'click', () => startSummary({
        resumeCurrent: true, outputMode: 'note'
    }));
    document.querySelectorAll('input[name="outputMode"]').forEach((input) => {
        input.addEventListener('change', handleOutputModeChange);
    });
    bindListener('cancelTaskBtn', 'click', cancelCurrentTask);
    bindListener('downloadMdBtn', 'click', downloadSummary);
    bindListener('copyNoteBtn', 'click', copyFullNote);
    bindListener('formatSelect', 'change', toggleImageLayout);
    bindListener('refreshRecentTasksBtn', 'click', () => loadRecentTasks());
    const recentTaskList = byId('recentTaskList');
    if (recentTaskList) {
        recentTaskList.addEventListener('click', (event) => {
            const deleteButton = event.target.closest('[data-delete-task-id]');
            if (deleteButton) {
                deleteStoppedTask(deleteButton.dataset.deleteTaskId);
                return;
            }
            const button = event.target.closest('[data-task-id]');
            if (button) openRecentTask(button.dataset.taskId);
        });
    }
    bindListener('llmProfile', 'change', handleProfileChange);
    bindListener('llmModel', 'change', handleModelChange);
    bindListener('llmTestBtn', 'click', testLlmConnection);
    bindListener('saveKeyBtn', 'click', saveApiKey);
    bindListener('clearKeyBtn', 'click', clearSavedKey);
    bindListener('customProfileAddBtn', 'click', addCustomProfile);
    bindListener('customProfileDeleteBtn', 'click', deleteCustomProfile);
    bindListener('manualModelBtn', 'click', manualImportWhisperModel);
    bindListener('sourceType', 'change', toggleSourceType);
    bindPreferenceAutoSave();
    const videoUrl = byId('videoUrl');
    if (videoUrl) {
        videoUrl.addEventListener('input', () => {
            window.clearTimeout(biliHintDebounce);
            biliHintDebounce = window.setTimeout(() => updateBiliHint(), 300);
        });
        videoUrl.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.isComposing) startSummary();
        });
    }
    bindListener('biliHintScanBtn', 'click', startBiliLogin);
    bindListener('biliHintManualBtn', 'click', revealBiliCredentials);
    bindListener('douyinLoginBtn', 'click', startDouyinLogin);
    bindListener('biliHintDismissBtn', 'click', dismissBiliHint);
    initBiliLogin();
    bindListener('includeScreenshots', 'change', toggleScreenshotSettings);
    bindListener('localFile', 'change', updateFileInfo);
    initThemeControl();
    window.addEventListener('beforeunload', () => {
        stopPolling();
        stopElapsedTimer();
        stopDouyinLoginPolling();
        stopManualImportPolling();
    });
}

function bindListener(id, event, handler) {
    const element = byId(id);
    if (element) {
        element.addEventListener(event, handler);
    } else {
        console.warn(`[VideoToNo] 页面缺少元素 #${id}，已跳过对应事件绑定`);
    }
}

function byId(id) {
    return document.getElementById(id);
}

const TASK_STATUS_LABELS = {
    uploaded: '待处理',
    pending: '准备中',
    queued: '排队中',
    processing: '处理中',
    cancelling: '取消中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
};

// 已结束 + 尚未开始处理的任务可以直接删；运行中的任务（queued/processing）只能先取消
const DELETABLE_TASK_STATUSES = ['failed', 'cancelled', 'uploaded', 'pending'];

async function loadRecentTasks(silent = false) {
    const refreshButton = byId('refreshRecentTasksBtn');
    refreshButton.disabled = true;
    try {
        const response = await fetch(`${API_BASE}/tasks`, { cache: 'no-store' });
        const data = await readResponse(response, '读取最近任务失败');
        renderRecentTasks(Array.isArray(data.tasks) ? data.tasks : []);
    } catch (error) {
        if (!silent) showToast(error.message, 'error');
    } finally {
        refreshButton.disabled = false;
    }
}

function renderRecentTasks(tasks) {
    const panel = byId('recentTasksPanel');
    const list = byId('recentTaskList');
    list.replaceChildren();
    panel.hidden = tasks.length === 0;
    tasks.forEach((task) => {
        const row = document.createElement('div');
        row.className = 'recent-task-row';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'recent-task-item';
        button.dataset.taskId = task.task_id;

        const title = document.createElement('strong');
        if (task.output === 'transcript') {
            // 标签放最前：长标题被省略号截断时，仍能看出这是一条字幕稿
            const kind = document.createElement('span');
            kind.className = 'recent-task-kind';
            kind.textContent = '转录';
            title.append(kind);
        }
        title.append(task.title || '未命名任务');
        const meta = document.createElement('span');
        const elapsed = formatElapsedCompact(task.elapsed_seconds);
        const created = formatTaskDate(task.created_at);
        meta.textContent = [created, elapsed].filter(Boolean).join(' · ');
        const status = document.createElement('em');
        status.className = `recent-task-status ${task.status || ''}`;
        status.textContent = TASK_STATUS_LABELS[task.status] || task.step_name || '未知';

        const text = document.createElement('span');
        text.className = 'recent-task-text';
        text.append(title, meta);
        button.append(text, status);
        row.append(button);
        // 排队中/处理中只能取消（后端拒绝删除运行中的任务）；未开始的任务可以直接删掉，
        // 否则上传后放弃的孤儿任务会永久占着最近任务列表
        if (DELETABLE_TASK_STATUSES.includes(task.status)) {
            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'recent-task-delete';
            deleteButton.dataset.deleteTaskId = task.task_id;
            const statusLabel = `${TASK_STATUS_LABELS[task.status] || ''}任务`;
            deleteButton.title = `删除${statusLabel}`;
            deleteButton.setAttribute('aria-label', `删除${statusLabel}：${task.title || '未命名任务'}`);
            deleteButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14M10 10v6m4-6v6"/></svg>';
            row.append(deleteButton);
        }
        list.append(row);
    });
}

async function deleteStoppedTask(taskId) {
    if (!window.confirm('将删除该任务及其音频、转录和截图，删除后无法继续复用。确定删除吗？')) return;
    try {
        const response = await fetch(`${API_BASE}/task/${encodeURIComponent(taskId)}`, {
            method: 'DELETE'
        });
        await readResponse(response, '删除任务失败');
        if (currentTaskId === taskId) {
            resetTaskView();
            byId('progressArea').hidden = true;
        }
        await loadRecentTasks(true);
        showToast('任务已删除', 'success');
    } catch (error) {
        showToast(`删除失败：${error.message}`, 'error');
        loadRecentTasks(true);
    }
}

function formatTaskDate(value) {
    const date = new Date(value || '');
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    }).format(date);
}

function formatElapsedCompact(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    if (!total) return '';
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    if (hours) return `${hours}时${minutes}分`;
    if (minutes) return `${minutes}分${seconds}秒`;
    return `${seconds}秒`;
}

async function openRecentTask(taskId) {
    try {
        const response = await fetch(`${API_BASE}/task/${encodeURIComponent(taskId)}`, {
            cache: 'no-store'
        });
        const task = await readResponse(response, '读取任务失败');
        resetTaskView();
        currentTaskId = taskId;
        if (task.source_url) {
            byId('sourceType').value = 'url';
            byId('videoUrl').value = task.source_url;
        } else if (task.uploaded_filename) {
            byId('sourceType').value = 'local';
        }
        toggleSourceType();
        updateProgress(task.progress || 0);
        updateStep(task.step || 0);
        setTranscriptTaskView(task.output === 'transcript');
        renderTaskAdvisory(task.advisory);
        if (Array.isArray(task.logs)) updateLogs(task.logs);

        if (task.status === 'completed') {
            await completeTask(task.result, task.elapsed_seconds);
        } else if (['queued', 'processing', 'cancelling'].includes(task.status)) {
            setTaskActive(true);
            startElapsedTimer(task.elapsed_seconds);
            setTaskState('processing', TASK_STATUS_LABELS[task.status]);
            byId('networkState').textContent = task.status === 'cancelling'
                ? '等待当前步骤停止'
                : '已连接到运行任务';
            schedulePoll(taskId, 0);
        } else {
            showStoppedTask(task);
        }
        byId('progressArea').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
        showToast(error.message, 'error');
        loadRecentTasks(true);
    }
}

function showStoppedTask(task) {
    stopElapsedTimer(task.elapsed_seconds);
    setTaskActive(false);
    const cancelled = task.status === 'cancelled';
    setTaskState(cancelled ? 'cancelled' : 'failed', TASK_STATUS_LABELS[task.status] || '已停止');
    byId('taskError').hidden = false;
    byId('taskErrorMessage').textContent = task.error || (cancelled
        ? '任务已取消；可以复用已有中间文件重新生成。'
        : '任务未完成；可以从已有中间文件继续。');
    byId('retryBtn').disabled = false;
    byId('restartBtn').disabled = false;
    byId('networkState').textContent = '历史任务';
    setSubmitButton(false, '重新提交');
}

function removeLegacySecrets() {
    LEGACY_SENSITIVE_KEYS.forEach((key) => localStorage.removeItem(key));
}

const GLOBAL_PREF_KEYS = [
    'whisper_model',
    'screenshot_interval',
    'include_screenshots',
    'use_gpu',
    'summary_style',
    'reasoning_effort',
    'processing_mode',
    'output_mode'
];

function defaultProvider() {
    return Object.keys(PROVIDER_CONFIG)[0];
}

function defaultPrefs() {
    return {
        schema: PREFS_SCHEMA,
        provider: defaultProvider(),
        custom_id: '',
        customs: [],
        by_provider: {},
        confirmations: { whisper_download: {} },
        global: {
            whisper_model: 'base',
            screenshot_interval: '10',
            include_screenshots: false,
            use_gpu: false,
            summary_style: 'detailed',
            reasoning_effort: 'auto',
            processing_mode: 'restart',
            output_mode: 'note'
        },
        ui: { theme: 'system' }
    };
}

function readStoredPrefs() {
    try {
        const raw = localStorage.getItem(PREFS_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : null;
    } catch (_error) {
        return null;
    }
}

// 递归丢弃敏感字段名的值，并把不可序列化的东西剔掉。
function sanitizeForStorage(value) {
    if (Array.isArray(value)) return value.map(sanitizeForStorage);
    if (value && typeof value === 'object') {
        const clean = {};
        Object.entries(value).forEach(([key, item]) => {
            if (SECRET_FIELD_PATTERN.test(key)) {
                console.warn(`[VideoToNo] 拒绝把敏感字段 ${key} 写入浏览器存储`);
                return;
            }
            const sanitized = sanitizeForStorage(item);
            if (sanitized !== undefined) clean[key] = sanitized;
        });
        return clean;
    }
    if (typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') {
        return value;
    }
    return undefined;
}

// 全文件唯一的存储写入入口。
function persistPrefs() {
    try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(sanitizeForStorage(prefs)));
    } catch (error) {
        console.warn('[VideoToNo] 偏好保存失败（浏览器可能禁用了本地存储）：', error);
    }
}

function schedulePersist(job) {
    window.clearTimeout(persistTimer);
    persistJob = job;
    persistTimer = window.setTimeout(() => {
        persistTimer = null;
        const pending = persistJob;
        persistJob = null;
        if (pending) pending();
    }, PREFS_DEBOUNCE_MS);
}

function flushPendingPrefs() {
    if (!persistTimer) return;
    window.clearTimeout(persistTimer);
    persistTimer = null;
    const pending = persistJob;
    persistJob = null;
    if (pending) pending();
}

function isPresetModel(provider, modelId) {
    const config = PROVIDER_CONFIG[provider];
    return Boolean(config && config.models.some(([id]) => id === modelId));
}

function legacyWhisperConfirms() {
    const confirmed = {};
    try {
        Object.keys(localStorage)
            .filter((key) => key.startsWith(LEGACY_WHISPER_CONFIRM_PREFIX))
            .forEach((key) => {
                if (localStorage.getItem(key) === '1') {
                    confirmed[key.slice(LEGACY_WHISPER_CONFIRM_PREFIX.length)] = true;
                }
                localStorage.removeItem(key);
            });
    } catch (error) {
        console.warn('[VideoToNo] 迁移下载确认记录失败：', error);
    }
    return confirmed;
}

function migrateLegacyPrefs() {
    if (readStoredPrefs()) return null;
    const next = defaultPrefs();
    const legacyProvider = localStorage.getItem('llm_provider')
        || LEGACY_PROVIDER_MAP[localStorage.getItem('llm_model')]
        || '';
    next.provider = PROVIDER_CONFIG[legacyProvider] ? legacyProvider : defaultProvider();
    const legacyModelId = localStorage.getItem('llm_model_id') || '';
    const legacyManual = (localStorage.getItem('custom_model_name') || '').trim();
    const legacyBaseUrl = (localStorage.getItem('custom_base_url') || '').trim();
    // custom 的两个键本来就只属于自定义接口，直接搬进那条档案不会串到别的档案；
    // 真正会串味的是被所有 Provider 共用的 llm_model_id，下面按档案逐个甄别。
    const customEntry = {
        id: 'c-1',
        label: '自定义接口',
        base_url: legacyBaseUrl,
        model_id: CUSTOM_MODEL_ID,
        manual_model: legacyManual
    };
    next.customs.push(customEntry);
    if (next.provider === 'custom') {
        next.custom_id = customEntry.id;
    } else if (isPresetModel(next.provider, legacyModelId)) {
        next.by_provider[next.provider] = { model_id: legacyModelId, manual_model: '' };
    } else if (legacyModelId === CUSTOM_MODEL_ID && legacyManual) {
        // 内置 Provider + 手填模型 ID：这两个键本来就成对使用，不属于串味的数据。
        next.by_provider[next.provider] = { model_id: CUSTOM_MODEL_ID, manual_model: legacyManual };
    }
    // 其余情况（llm_model_id 属于别的 Provider，即旧版共用单键造成的串写）直接丢弃：
    // 宁可让用户重选一次，也不会把 A 档案的模型 ID 变成 B 档案输入框里的内容。
    GLOBAL_PREF_KEYS.forEach((key) => {
        const value = localStorage.getItem(key);
        if (value !== null) next.global[key] = value;
    });
    next.global.include_screenshots = next.global.include_screenshots === 'true';
    next.global.use_gpu = next.global.use_gpu === 'true';
    if (!['auto', 'off', 'high', 'max'].includes(next.global.reasoning_effort)) {
        next.global.reasoning_effort = 'auto';
    }
    const theme = localStorage.getItem('theme');
    if (theme) next.ui.theme = theme;
    next.confirmations.whisper_download = legacyWhisperConfirms();
    LEGACY_PREFERENCE_KEYS.forEach((key) => localStorage.removeItem(key));
    return next;
}

function mergePrefs(stored) {
    if (!stored) return null;
    const next = defaultPrefs();
    if (typeof stored.provider === 'string') next.provider = stored.provider;
    if (typeof stored.custom_id === 'string') next.custom_id = stored.custom_id;
    if (Array.isArray(stored.customs)) {
        next.customs = stored.customs
            .filter((entry) => entry && typeof entry === 'object' && typeof entry.id === 'string')
            .map((entry) => ({
                id: entry.id,
                label: String(entry.label || ''),
                base_url: String(entry.base_url || ''),
                model_id: String(entry.model_id || ''),
                manual_model: String(entry.manual_model || '')
            }));
    }
    if (stored.by_provider && typeof stored.by_provider === 'object') {
        Object.entries(stored.by_provider).forEach(([provider, record]) => {
            if (!PROVIDER_CONFIG[provider] || !record || typeof record !== 'object') return;
            next.by_provider[provider] = {
                model_id: String(record.model_id || ''),
                manual_model: String(record.manual_model || '')
            };
        });
    }
    if (stored.global && typeof stored.global === 'object') {
        GLOBAL_PREF_KEYS.forEach((key) => {
            const value = stored.global[key];
            if (value !== undefined && value !== null) next.global[key] = value;
        });
    }
    if (stored.confirmations && typeof stored.confirmations === 'object') {
        const confirmed = stored.confirmations.whisper_download;
        if (confirmed && typeof confirmed === 'object') {
            Object.entries(confirmed).forEach(([model, value]) => {
                if (value === true) next.confirmations.whisper_download[model] = true;
            });
        }
    }
    if (stored.ui && typeof stored.ui.theme === 'string') next.ui.theme = stored.ui.theme;
    return next;
}

function initPreferences() {
    let stored = null;
    try {
        stored = migrateLegacyPrefs() || mergePrefs(readStoredPrefs());
    } catch (error) {
        // 隐私模式等禁用本地存储时，只丢"记住上次的选择"，页面必须照常可用。
        console.warn('[VideoToNo] 读取历史偏好失败，改用默认设置：', error);
    }
    prefs = stored || defaultPrefs();
    if (!PROVIDER_CONFIG[prefs.provider]) prefs.provider = defaultProvider();
    applyPreferencesToForm();
    persistPrefs();
}

function providerRecord(provider) {
    if (!prefs.by_provider[provider]) {
        prefs.by_provider[provider] = { model_id: '', manual_model: '' };
    }
    return prefs.by_provider[provider];
}

function newCustomEntry(label) {
    return {
        id: `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
        label: label || '自定义接口',
        base_url: '',
        model_id: '',
        manual_model: ''
    };
}

function ensureCustoms() {
    if (!prefs.customs.length) prefs.customs.push(newCustomEntry());
    return prefs.customs;
}

function activeCustom() {
    if (prefs.provider !== 'custom') return null;
    const entries = ensureCustoms();
    const entry = entries.find((item) => item.id === prefs.custom_id) || entries[0];
    prefs.custom_id = entry.id;
    return entry;
}

// 当前档案自己的记录：模型选择和手填地址都只属于它，不与其他档案共用字段。
function activeRecord() {
    return prefs.provider === 'custom' ? activeCustom() : providerRecord(prefs.provider);
}

function profileSelectValue() {
    return prefs.provider === 'custom'
        ? `${CUSTOM_PROFILE_PREFIX}${prefs.custom_id}`
        : prefs.provider;
}

function selectProfileByValue(value) {
    if (value.startsWith(CUSTOM_PROFILE_PREFIX)) {
        prefs.provider = 'custom';
        const id = value.slice(CUSTOM_PROFILE_PREFIX.length);
        if (prefs.customs.some((item) => item.id === id)) prefs.custom_id = id;
    } else if (PROVIDER_CONFIG[value]) {
        prefs.provider = value;
    }
}

function renderProfileOptions() {
    const select = byId('llmProfile');
    if (!select) return null;
    ensureCustoms();
    select.replaceChildren();
    Object.entries(PROVIDER_CONFIG).forEach(([provider, config]) => {
        if (provider === 'custom') return;
        select.add(new Option(config.name, provider));
    });
    prefs.customs.forEach((entry) => {
        const option = new Option(entry.label || '自定义接口', `${CUSTOM_PROFILE_PREFIX}${entry.id}`);
        option.title = entry.base_url || '尚未填写接口地址';
        select.add(option);
    });
    const wanted = profileSelectValue();
    const available = Array.from(select.options).map((option) => option.value);
    // 档案被删除或旧版 HTML 缺少选项时退回可用项，但用户选过的档案仍留在偏好里。
    select.value = available.includes(wanted) ? wanted : available[0];
    if (select.value !== wanted) selectProfileByValue(select.value);
    return select;
}

function applyPreferencesToForm() {
    renderProfileOptions();
    renderProfileForm();
    byId('whisperModel').value = prefs.global.whisper_model;
    snapshotWhisperLabels();
    refreshWhisperModelHints();
    byId('screenshotInterval').value = prefs.global.screenshot_interval;
    byId('includeScreenshots').checked = prefs.global.include_screenshots === true;
    byId('useGpu').checked = prefs.global.use_gpu === true;
    byId('summaryStyle').value = prefs.global.summary_style;
    byId('reasoningEffort').value = ['auto', 'off', 'high', 'max'].includes(prefs.global.reasoning_effort)
        ? prefs.global.reasoning_effort : 'auto';
    const processingModeInput = document.querySelector(
        `input[name="processingMode"][value="${prefs.global.processing_mode}"]`
    );
    if (processingModeInput) processingModeInput.checked = true;
    // 旧版 HTML 缓存里没有这组单选时，selectedOutputMode() 自然回落到笔记路线
    const outputModeInput = document.querySelector(
        `input[name="outputMode"][value="${prefs.global.output_mode}"]`
    );
    if (outputModeInput) outputModeInput.checked = true;
    applyOutputMode();
    toggleScreenshotSettings();
    applyTheme(prefs.ui.theme);
    refreshKeyStatus();
}

function initThemeControl() {
    byId('themeControl').addEventListener('click', (event) => {
        const button = event.target.closest('[data-theme-option]');
        if (!button) return;
        prefs.ui.theme = button.dataset.themeOption;
        persistPrefs();
        applyTheme(prefs.ui.theme);
    });
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('#themeControl [data-theme-option]').forEach((button) => {
        const isActive = button.dataset.themeOption === theme;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-pressed', String(isActive));
    });
}

function persistGlobals() {
    normalizeScreenshotInterval();
    const values = prefs.global;
    values.whisper_model = byId('whisperModel').value || values.whisper_model;
    values.screenshot_interval = byId('screenshotInterval').value;
    values.include_screenshots = byId('includeScreenshots').checked;
    values.use_gpu = byId('useGpu').checked;
    values.summary_style = byId('summaryStyle').value;
    values.reasoning_effort = byId('reasoningEffort').value;
    values.processing_mode = document.querySelector('input[name="processingMode"]:checked')?.value
        || values.processing_mode;
    values.output_mode = document.querySelector('input[name="outputMode"]:checked')?.value
        || values.output_mode;
    persistPrefs();
}

function persistProfileForm() {
    const record = activeRecord();
    if (!record) return;
    record.model_id = byId('llmModel').value || '';
    record.manual_model = byId('customModelName').value.trim();
    if (prefs.provider === 'custom') record.base_url = byId('customBaseUrl').value.trim();
    persistPrefs();
}

function handleModelChange() {
    toggleCustomConfig();
    persistProfileForm();
}

// 改动即记住：曾经只有点「保存偏好」才落盘，正常提交不保存，
// 于是重开页面必然弹回默认档案。
function bindPreferenceAutoSave() {
    ['whisperModel', 'summaryStyle', 'reasoningEffort', 'includeScreenshots', 'useGpu']
        .forEach((id) => bindListener(id, 'change', persistGlobals));
    document.querySelectorAll('input[name="processingMode"]').forEach((input) => {
        input.addEventListener('change', persistGlobals);
    });
    bindListener('screenshotInterval', 'change', persistGlobals);
    bindListener('screenshotInterval', 'input', () => schedulePersist(persistGlobals));
    ['customBaseUrl', 'customModelName'].forEach((id) => {
        const element = byId(id);
        if (!element) return;
        element.addEventListener('input', () => {
            toggleCustomConfig();
            schedulePersist(persistProfileFormAndKeyStatus);
        });
    });
    bindListener('customProfileLabel', 'input', handleCustomProfileLabelInput);
    bindListener('apiKey', 'input', updateKeyControls);
    window.addEventListener('pagehide', flushPendingPrefs);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') flushPendingPrefs();
    });
}

function persistProfileFormAndKeyStatus() {
    persistProfileForm();
    refreshKeyStatus();
}

function resetPreferences() {
    const message = '将把模型档案选择、识别参数和主题恢复为默认值。\n\n'
        + '只影响当前浏览器的界面偏好，不会清除本机已保存的 API Key。确定继续吗？';
    if (!window.confirm(message)) return;
    try {
        localStorage.removeItem(PREFS_KEY);
    } catch (error) {
        console.warn('[VideoToNo] 清除偏好失败：', error);
    }
    prefs = defaultPrefs();
    applyPreferencesToForm();
    persistPrefs();
    showToast('已恢复默认偏好', 'info');
}

function handleProfileChange() {
    const select = byId('llmProfile');
    if (!select) return;
    selectProfileByValue(select.value);
    // 换档案时清掉输入框里未保存的 Key：贴错档案比多贴一次危险得多。
    clearTypedApiKey();
    keyMatch = null;
    renderProfileForm();
    persistPrefs();
    refreshKeyStatus();
}

function renderProfileForm() {
    const record = activeRecord();
    byId('customBaseUrl').value = (record && record.base_url) || '';
    byId('customProfileLabel').value = (record && record.label) || '';
    populateModelOptions(record);
    updateKeyControls();
}

// 只读取当前档案自己的记录：preferred 不再可能来自另一个档案，
// 识别不了的模型 ID 只会落回本档案的「手动输入」选项，绝不写入任何输入框。
function populateModelOptions(record) {
    const provider = prefs.provider;
    const providerConfig = PROVIDER_CONFIG[provider] || PROVIDER_CONFIG[defaultProvider()];
    const modelSelect = byId('llmModel');
    modelSelect.replaceChildren();

    providerConfig.models.forEach(([modelId, label]) => {
        const option = new Option(label, modelId);
        option.title = modelId;
        modelSelect.add(option);
    });
    modelSelect.add(new Option('手动输入模型 ID…', CUSTOM_MODEL_ID));

    const preferred = (record && record.model_id) || providerConfig.defaultModel;
    const hasPreset = Boolean(preferred)
        && Array.from(modelSelect.options).some((option) => option.value === preferred);
    modelSelect.value = hasPreset ? preferred : CUSTOM_MODEL_ID;
    byId('customModelName').value = (record && record.manual_model) || '';
    toggleCustomConfig();
}

function toggleCustomConfig() {
    const customProvider = prefs.provider === 'custom';
    const customModel = byId('llmModel').value === CUSTOM_MODEL_ID;
    byId('customBaseUrlField').hidden = !customProvider;
    byId('customModelNameField').hidden = !customModel;
    byId('customApiConfig').hidden = !customProvider;
}

// ---- 自定义接口档案（可命名多份） ----

function handleCustomProfileLabelInput() {
    const record = activeCustom();
    if (!record) return;
    record.label = byId('customProfileLabel').value.trim();
    schedulePersist(() => {
        persistPrefs();
        renderProfileOptions();
        updateKeyControls();
    });
}

function addCustomProfile() {
    persistProfileForm();
    const current = activeCustom();
    const entry = newCustomEntry('新的自定义接口');
    if (current) entry.base_url = current.base_url;
    prefs.provider = 'custom';
    prefs.customs.push(entry);
    prefs.custom_id = entry.id;
    renderProfileOptions();
    renderProfileForm();
    persistPrefs();
    refreshKeyStatus();
    showToast('已新建自定义接口档案，填好名称和地址即可使用', 'info');
}

function deleteCustomProfile() {
    const entry = activeCustom();
    if (!entry) return;
    if (prefs.customs.length <= 1) {
        showToast('至少保留一个自定义接口档案', 'info');
        return;
    }
    const message = `删除档案「${entry.label}」？\n\n`
        + '只删除本浏览器里的名称、地址和模型选择；本机已保存的 API Key 按接口地址保管，'
        + '不会被清除（别的档案指向同一地址时仍可复用）。';
    if (!window.confirm(message)) return;
    prefs.customs = prefs.customs.filter((item) => item.id !== entry.id);
    prefs.custom_id = prefs.customs[0].id;
    renderProfileOptions();
    renderProfileForm();
    persistPrefs();
    refreshKeyStatus();
    showToast(`已删除档案「${entry.label}」`, 'success');
}

// ---- 本机已保存的 API Key（按接口地址保管，浏览器从不持有） ----

let keyStatus = { storage: null, error: null };
let keyMatch = null;
let keyStatusToken = 0;

function hasReusableKey() {
    return Boolean(keyMatch && keyMatch.key_state === 'saved');
}

// Key 输入框的唯一读取入口：页面元素缺失时退回空串，而不是在启动路径上抛错。
function typedApiKey() {
    const element = byId('apiKey');
    return element ? element.value.trim() : '';
}

function clearTypedApiKey() {
    const element = byId('apiKey');
    if (element) element.value = '';
}

// 让后端按它自己的地址规范化规则回答"这个端点存了 Key 没有"，
// 前端不复制一套规范化逻辑，就不会出现两边判断不一致。
async function refreshKeyStatus() {
    const token = ++keyStatusToken;
    const config = getSelectedModelConfig();
    if (!config || !config.baseUrl) {
        keyMatch = null;
        keyStatus = { storage: null, error: null };
        updateKeyControls();
        return;
    }
    const params = new URLSearchParams({ base_url: config.baseUrl, provider: config.provider });
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-keys?${params.toString()}`, { cache: 'no-store' }, 8000
        );
        const data = await readResponse(response, '读取本机 Key 状态失败');
        if (token !== keyStatusToken) return;
        keyStatus = { storage: data.storage || null, error: data.error || null };
        keyMatch = data.match || null;
    } catch (error) {
        if (token !== keyStatusToken) return;
        keyStatus = { storage: null, error: null };
        keyMatch = null;
    }
    updateKeyControls();
}

function updateKeyControls() {
    const chip = byId('keyState');
    if (!chip) return;
    const typed = typedApiKey();
    const config = getSelectedModelConfig();
    const insecure = Boolean(keyStatus.storage) && keyStatus.storage.secure === false;
    const suffix = insecure ? '（本机不支持加密，明文存储）' : '';
    let text;
    let state = 'muted';
    if (keyStatus.error === 'corrupt_keys_file') {
        text = '本机密钥档案已损坏，可删除 workspace/llm_keys.json 后重新保存';
        state = 'error';
    } else if (!config || !config.baseUrl) {
        text = '填好接口地址后才能确认本机是否已保存 Key';
    } else if (typed) {
        text = hasReusableKey()
            ? `已填入新 Key（本机现存的仍是 ${keyMatch.api_key_masked || ''}）`
            : '已填入 Key，未保存则只在本次页面会话内有效';
        state = 'warn';
    } else if (keyMatch && keyMatch.key_state === 'undecryptable') {
        text = '本机已存的 Key 无法解密（可能来自其他机器或其他 Windows 账户），请重填后保存';
        state = 'error';
    } else if (hasReusableKey()) {
        text = `本机已保存 ${keyMatch.api_key_masked || ''} · ${keyMatch.label || keyMatch.endpoint || ''}${suffix}`;
        state = 'ok';
    } else if (keyStatus.storage) {
        text = `该接口未保存 Key${suffix}`;
        state = 'muted';
    } else {
        text = '未连接本机服务，暂不确定是否已保存 Key';
        state = 'muted';
    }
    chip.textContent = text;
    chip.className = `key-chip ${state}`;
    const saveButton = byId('saveKeyBtn');
    if (saveButton) saveButton.disabled = !typed;
    const clearButton = byId('clearKeyBtn');
    if (clearButton) clearButton.hidden = !(keyMatch && keyMatch.saved);
}

async function saveApiKey() {
    const config = getSelectedModelConfig();
    const typed = typedApiKey();
    const button = byId('saveKeyBtn');
    if (!config || !typed) return;
    if (!config.baseUrl) {
        showToast('请先填写 API Base URL', 'error');
        return;
    }
    if (button) button.disabled = true;
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-keys`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider: config.provider,
                    base_url: config.baseUrl,
                    label: config.profileLabel,
                    model: config.model || null,
                    api_key: typed
                })
            },
            15000
        );
        await readResponse(response, '保存失败');
        clearTypedApiKey();
        showToast(`Key 已加密保存到本机：${config.profileLabel}`, 'success');
    } catch (error) {
        showToast(`保存失败：${error.message}`, 'error');
    }
    await refreshKeyStatus();
}

async function clearSavedKey() {
    const config = getSelectedModelConfig();
    if (!config || !config.baseUrl) return;
    const message = `将从本机删除接口地址 ${config.baseUrl} 的 API Key，删除后需重新填写。确定吗？`;
    if (!window.confirm(message)) return;
    const params = new URLSearchParams({ base_url: config.baseUrl, provider: config.provider });
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-keys?${params.toString()}`, { method: 'DELETE' }, 10000
        );
        await readResponse(response, '清除失败');
        showToast('已从本机清除该接口的 Key', 'success');
    } catch (error) {
        showToast(`清除失败：${error.message}`, 'error');
    }
    await refreshKeyStatus();
}

async function testLlmConnection() {
    const button = byId('llmTestBtn');
    const status = byId('llmTestStatus');
    const modelConfig = getSelectedModelConfig();
    if (!modelConfig) return;
    if (!modelConfig.baseUrl) {
        status.textContent = '请先填写 API Base URL';
        status.className = 'llm-test-status error';
        return;
    }
    const apiKey = typedApiKey();
    const body = {
        model_type: modelConfig.provider,
        base_url: modelConfig.baseUrl,
        model: modelConfig.model
    };
    if (apiKey) body.api_key = apiKey;
    button.disabled = true;
    status.textContent = '测试中…';
    status.className = 'llm-test-status testing';
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-test`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            },
            30_000
        );
        const data = await readResponse(response, '测试请求失败');
        const detail = data.ok ? data.message : (data.message || data.error || '连接失败');
        const source = data.key_source ? ` · Key：${data.key_source}` : '';
        status.textContent = `${detail}${source}`;
        status.className = data.ok ? 'llm-test-status ok' : 'llm-test-status error';
    } catch (error) {
        status.textContent = `测试失败：${error.message}`;
        status.className = 'llm-test-status error';
    } finally {
        button.disabled = false;
        refreshKeyStatus();
    }
}

function getSelectedModelConfig() {
    const provider = prefs.provider;
    const providerConfig = PROVIDER_CONFIG[provider];
    if (!providerConfig) return null;

    const selectedModel = byId('llmModel').value;
    const model = selectedModel === CUSTOM_MODEL_ID
        ? byId('customModelName').value.trim()
        : selectedModel;
    const baseUrl = provider === 'custom'
        ? byId('customBaseUrl').value.trim()
        : providerConfig.baseUrl;
    const profileLabel = provider === 'custom'
        ? ((activeCustom() || {}).label || '自定义接口')
        : providerConfig.name;
    const modelLabel = selectedModel === CUSTOM_MODEL_ID
        ? model
        : byId('llmModel').selectedOptions[0]?.textContent;
    return {
        provider,
        baseUrl,
        model,
        profileLabel,
        name: modelLabel ? `${providerConfig.name} · ${modelLabel}` : providerConfig.name
    };
}

function toggleSourceType() {
    const isLocal = byId('sourceType').value === 'local';
    byId('urlField').hidden = isLocal;
    byId('fileField').hidden = !isLocal;
    updateFileInfo();
    updateBiliHint();
}

// ---- 输出类型：生成笔记 / 仅转录字幕 ----
// 仅转录仍提交到 /api/summarize + output=transcript，而不是 /api/transcribe：
// 后者是 agent 取原料的路线，任务不进网页端「最近任务」。
function selectedOutputMode() {
    return document.querySelector('input[name="outputMode"]:checked')?.value || 'note';
}

function isTranscriptMode() {
    return selectedOutputMode() === 'transcript';
}

function handleOutputModeChange() {
    applyOutputMode();
    persistGlobals();
}

// 程序化切换输出类型（例如从字幕稿转去生成笔记），保持界面与偏好和单选一致
function setOutputMode(mode) {
    const input = document.querySelector(`input[name="outputMode"][value="${mode}"]`);
    if (!input || input.checked) return;
    input.checked = true;
    applyOutputMode();
    persistGlobals();
}

function applyOutputMode() {
    const transcript = isTranscriptMode();
    // 笔记专用设置整组藏掉（不是灰掉）：这条路上没有大模型调用，也没有第 5 步截图
    ['summaryStyleField', 'reasoningEffortField', 'includeScreenshotsField', 'screenshotIntervalField']
        .forEach((id) => {
            const element = byId(id);
            if (element) element.hidden = transcript;
        });
    const hint = byId('outputModeHint');
    if (hint) {
        hint.hidden = !transcript;
        hint.textContent = transcript
            ? '仅转录字幕：只读平台字幕或用本地 Whisper 转写，全程不调用大模型、不需要 API Key。'
                + '产出带时间轴的字幕稿，可直接复制或下载，之后仍能在它基础上生成笔记。'
            : '';
    }
    if (!isSubmitting && !isTaskActive) {
        setSubmitButton(false, transcript ? '开始转写' : '开始生成');
    }
}

let biliHintDebounce = null;
let biliPromptDismissed = false;
let douyinLoginTimer = null;
let douyinCookies = null;

function isBilibiliUrl(value) {
    return /bilibili\.com|b23\.tv|BV[0-9A-Za-z]{10}/.test(value);
}

function isDouyinUrl(value) {
    return /(^|\.)douyin\.com|iesdouyin\.com/.test(value);
}

function hasBiliCredentials() {
    return Boolean(
        byId('sessdata').value.trim()
        || byId('biliJct').value.trim()
        || byId('buvid3').value.trim()
    );
}

// ---- Whisper 未缓存确认的降噪逻辑 ----
// 1) 明显走平台字幕、用不到 Whisper 的提交不弹；
// 2) 已确认过一次下载的模型长期不再弹（localStorage 记忆；下拉框仍保留“未缓存”提示）。
function taskWillLikelyUseSubtitles(request) {
    if (!request || request.sourceType === 'local') return false;
    if (isBilibiliUrl(request.videoUrl) && hasBiliCredentials()) return true;
    if (isDouyinUrl(request.videoUrl) && douyinCookies) return true;
    return false;
}

function whisperDownloadConfirmed(modelId) {
    return prefs.confirmations.whisper_download[modelId] === true;
}

function rememberWhisperDownloadConfirm(modelId) {
    prefs.confirmations.whisper_download[modelId] = true;
    persistPrefs();
}

function updateBiliHint(forceShow = false) {
    const value = byId('videoUrl').value.trim();
    const notLocal = byId('sourceType').value !== 'local';
    const isBili = notLocal && isBilibiliUrl(value);
    const isDouyin = notLocal && isDouyinUrl(value);
    const hint = byId('biliHint');
    const actions = byId('biliHintActions');
    const biliScanButton = byId('biliHintScanBtn');
    const biliManualButton = byId('biliHintManualBtn');
    // 兼容旧版缓存的 HTML：提示条元素缺失时静默跳过，不影响其他功能
    if (!hint || !actions || !biliScanButton || !biliManualButton) return;
    const douyinButton = byId('douyinLoginBtn');
    const douyinStatus = byId('douyinLoginStatus');
    if (douyinButton) douyinButton.hidden = true;
    if (douyinStatus) douyinStatus.textContent = '';
    if (forceShow || (isBili && !hasBiliCredentials() && !biliPromptDismissed)) {
        byId('biliHintTitle').textContent = '检测到 B 站链接';
        byId('biliHintDesc').textContent = '填写访问凭据可优先使用 AI 字幕，无需等待本地转写。';
        actions.hidden = false;
        biliScanButton.hidden = false;
        biliManualButton.hidden = false;
        hint.hidden = false;
    } else if (forceShow || (isDouyin && !biliPromptDismissed)) {
        byId('biliHintTitle').textContent = '检测到抖音链接';
        byId('biliHintDesc').textContent = '先尝试直接解析；如果需要验证，可在本机浏览器完成后重试。';
        actions.hidden = false;
        biliScanButton.hidden = true;
        biliManualButton.hidden = true;
        if (douyinButton) douyinButton.hidden = false;
        hint.hidden = false;
    } else {
        hint.hidden = true;
    }
}

function revealBiliCredentials() {
    updateBiliHint(true);
    const section = byId('credentialsSection');
    section.open = true;
    section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    byId('sessdata').focus();
}

function dismissBiliHint() {
    biliPromptDismissed = true;
    updateBiliHint();
}

let biliLoginTimer = null;

function initBiliLogin() {
    bindListener('biliLoginBtn', 'click', startBiliLogin);
    bindListener('biliLoginCancelBtn', 'click', cancelBiliLogin);
}

async function startBiliLogin() {
    if (biliLoginTimer) return;
    try {
        const response = await fetch(`${API_BASE}/bili-login/start`, { method: 'POST' });
        const data = await readResponse(response, '启动扫码登录失败');
        if (!data.ok) {
            showToast(data.error || data.message || '启动失败', 'error');
            return;
        }
        if (data.state === 'ready' && data.cookies) {
            // 该浏览器配置已登录过：直接回填，无需等待弹窗
            fillBiliCredentials(data.cookies);
            cancelBiliLogin();
            return;
        }
        byId('biliLoginStatus').textContent = data.message || '请在弹出的窗口中扫码登录';
        byId('biliLoginCancelBtn').hidden = false;
        byId('biliLoginBtn').disabled = true;
        biliLoginTimer = window.setInterval(pollBiliLogin, 2000);
        pollBiliLogin();
    } catch (error) {
        showToast(`启动失败：${error.message}`, 'error');
    }
}

function fillBiliCredentials(cookies) {
    byId('sessdata').value = cookies.sessdata || '';
    byId('biliJct').value = cookies.bili_jct || '';
    byId('buvid3').value = cookies.buvid3 || '';
    const section = byId('credentialsSection');
    section.open = true;
    section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    showToast('B 站凭据已导入并填入下方表单', 'success');
    updateBiliHint();
}

async function pollBiliLogin() {
    try {
        const response = await fetch(`${API_BASE}/bili-login/status`, { cache: 'no-store' });
        const data = await readResponse(response, '读取登录状态失败');
        byId('biliLoginStatus').textContent = data.message || '';
        if (data.state === 'ready' && data.cookies) {
            stopBiliLoginPolling();
            fillBiliCredentials(data.cookies);
            cancelBiliLogin();
        } else if (['failed', 'timeout'].includes(data.state)) {
            stopBiliLoginPolling();
            showToast(data.message || '导入失败', 'error');
        }
    } catch {
        // 瞬时失败不停止轮询，避免扫码成功后错过回填
        byId('biliLoginStatus').textContent = '状态读取失败，正在重试…';
    }
}

function stopBiliLoginPolling() {
    if (biliLoginTimer !== null) {
        window.clearInterval(biliLoginTimer);
        biliLoginTimer = null;
    }
    byId('biliLoginBtn').disabled = false;
    byId('biliLoginCancelBtn').hidden = true;
    byId('biliLoginStatus').textContent = '';
}

async function cancelBiliLogin() {
    stopBiliLoginPolling();
    try {
        await fetch(`${API_BASE}/bili-login/cancel`, { method: 'POST' });
    } catch {
        // 后端不可达时忽略，浏览器窗口由用户自行关闭
    }
}

async function startDouyinLogin() {
    if (douyinLoginTimer) return;
    const button = byId('douyinLoginBtn');
    const status = byId('douyinLoginStatus');
    button.disabled = true;
    status.textContent = '正在打开抖音浏览器…';
    try {
        const response = await fetch(`${API_BASE}/douyin-login/start`, { method: 'POST' });
        const data = await readResponse(response, '启动抖音浏览器失败');
        if (!data.ok) {
            status.textContent = data.error || data.message || '启动失败';
            button.disabled = false;
            return;
        }
        status.textContent = data.message || '请在弹出的浏览器中完成登录或验证';
        douyinLoginTimer = window.setInterval(pollDouyinLogin, 2000);
        pollDouyinLogin();
    } catch (error) {
        status.textContent = `启动失败：${error.message}`;
        button.disabled = false;
    }
}

async function pollDouyinLogin() {
    try {
        const response = await fetch(`${API_BASE}/douyin-login/status`, { cache: 'no-store' });
        const data = await readResponse(response, '读取抖音浏览器状态失败');
        byId('douyinLoginStatus').textContent = data.message || '';
        if (data.state === 'ready' && data.cookies) {
            douyinCookies = data.cookies;
            stopDouyinLoginPolling();
            showToast('抖音浏览器验证完成，下一次提交会携带本机登录态', 'success');
        } else if (['failed', 'timeout'].includes(data.state)) {
            stopDouyinLoginPolling();
        }
    } catch {
        byId('douyinLoginStatus').textContent = '状态读取失败，正在重试…';
    }
}

function stopDouyinLoginPolling() {
    if (douyinLoginTimer !== null) {
        window.clearInterval(douyinLoginTimer);
        douyinLoginTimer = null;
    }
    byId('douyinLoginBtn').disabled = false;
}

function updateFileInfo() {
    if (byId('sourceType').value !== 'local') {
        byId('fileInfo').textContent = '支持常见视频链接；平台字幕可用时优先读取';
        return;
    }
    const file = byId('localFile').files[0];
    byId('fileInfo').textContent = file
        ? `${file.name} · ${formatBytes(file.size)}`
        : '支持常见视频格式，单个文件不超过 2 GB；大文件未勾选截图时自动只保留音频'
}

function toggleScreenshotSettings() {
    const enabled = byId('includeScreenshots').checked;
    byId('screenshotInterval').disabled = !enabled;
    byId('screenshotIntervalField').classList.toggle('is-muted', !enabled);
}

const WHISPER_MODEL_SIZES = {
    tiny: '~75MB',
    base: '~142MB',
    small: '~466MB',
    medium: '~1.5GB',
    'large-v3': '~3.1GB',
    turbo: '~1.6GB'
};

let whisperFallbackNotified = false;

function snapshotWhisperLabels() {
    Array.from(byId('whisperModel').options).forEach((option) => {
        option.dataset.baseLabel = option.textContent;
    });
}

async function refreshWhisperModelHints() {
    try {
        const response = await fetch(`${API_BASE}/whisper-models`, { cache: 'no-store' });
        const data = await readResponse(response, '读取模型状态失败');
        const byIdMap = {};
        (data.models || []).forEach((model) => { byIdMap[model.id] = model; });
        Array.from(byId('whisperModel').options).forEach((option) => {
            const model = byIdMap[option.value];
            const baseLabel = option.dataset.baseLabel || option.textContent;
            if (!model) return;
            option.dataset.status = model.status || 'missing';
            const size = WHISPER_MODEL_SIZES[option.value] || '';
            if (model.status === 'cached') {
                option.textContent = `${baseLabel}（已缓存）`;
            } else if (model.status === 'incomplete') {
                option.textContent = `${baseLabel}（缓存不完整，将自动补全）`;
            } else {
                option.textContent = `${baseLabel}（未缓存，首次使用需下载 ${size}）`;
            }
        });
    } catch {
        // 后端不可达时保持默认文案
    }
}

function formatBytes(bytes) {
    if (!bytes) return '0 MB';
    return `${(bytes / 1024 / 1024).toFixed(bytes > 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

// ---- Whisper 模型手动导入（大模型下载慢/反复失败时的替代方案）----
// 约定：把 4 个模型文件放入 workspace/_model_cache/manual/{model}/，
// 后端实时扫描该目录，放入后自动识别为「已缓存」。
const MANUAL_IMPORT_POLL_MS = 5000;
const MANUAL_IMPORT_POLL_MAX_TICKS = 24; // 约 2 分钟后停止轮询
let manualImportPollTimer = null;
let manualImportPollRemaining = 0;

function stopManualImportPolling() {
    if (manualImportPollTimer) {
        window.clearInterval(manualImportPollTimer);
        manualImportPollTimer = null;
    }
}

function startManualImportPolling(modelId) {
    stopManualImportPolling();
    manualImportPollRemaining = MANUAL_IMPORT_POLL_MAX_TICKS;
    manualImportPollTimer = window.setInterval(async () => {
        manualImportPollRemaining -= 1;
        if (isTaskActive || manualImportPollRemaining <= 0) {
            stopManualImportPolling();
            return;
        }
        await refreshWhisperModelHints();
        const option = byId('whisperModel').selectedOptions[0];
        if (option && option.value === modelId && option.dataset.status === 'cached') {
            stopManualImportPolling();
            showToast(`已识别手动导入的模型文件（${modelId}）`, 'success');
        }
    }, MANUAL_IMPORT_POLL_MS);
}

async function manualImportWhisperModel() {
    const option = byId('whisperModel').selectedOptions[0];
    if (!option) return;
    const modelId = option.value;
    const modelLabel = (option.dataset.baseLabel || option.textContent).split('（')[0];
    const confirmed = window.confirm(
        `将为模型「${modelLabel}」打开手动导入文件夹。\n\n`
        + '步骤：\n'
        + '1. 用浏览器从镜像站下载该模型的 4 个文件：\n'
        + `    config.json / model.bin / tokenizer.json / vocabulary.txt\n`
        + `    下载页：https://hf-mirror.com/Systran/faster-whisper-${modelId}/tree/main\n`
        + '2. 把 4 个文件放入即将打开的文件夹（不要改文件名）；\n'
        + '3. 文件就位后程序几秒内自动识别，下拉框会显示「已缓存」。\n\n'
        + '现在打开文件夹吗？'
    );
    if (!confirmed) return;
    try {
        const response = await fetch(`${API_BASE}/whisper-models/manual-folder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: modelId })
        });
        const data = await readResponse(response, '打开模型文件夹失败');
        if (data.opened) {
            showToast(`已打开导入文件夹：${data.path}`, 'success');
        } else {
            window.prompt('未能自动打开文件夹，请手动前往以下路径放入模型文件：', data.path);
        }
        startManualImportPolling(modelId);
    } catch (error) {
        showToast(error.message || '打开模型文件夹失败', 'error');
    }
}

function normalizeScreenshotInterval() {
    const input = byId('screenshotInterval');
    const value = Math.min(300, Math.max(5, Number.parseInt(input.value, 10) || 30));
    input.value = String(value);
    return value;
}

async function startSummary(options = {}) {
    if (isSubmitting || isTaskActive) {
        showToast('已有任务正在处理中', 'info');
        return;
    }
    flushPendingPrefs();   // 本次提交用的设置必须同步落盘，不能留在防抖里
    if (options.outputMode) setOutputMode(options.outputMode);

    const resumeTaskId = options.resumeCurrent ? currentTaskId : null;
    const selectedMode = document.querySelector('input[name="processingMode"]:checked')?.value || 'restart';
    const forceRestart = Boolean(options.forceRestart || (!resumeTaskId && selectedMode === 'restart'));
    const request = validateAndBuildRequestBase(resumeTaskId);
    if (!request) return;
    // 所选 Whisper 模型未完整缓存时，先确认下载（新用户首次使用）。
    // 以下情况跳过确认：复用转录/续跑（不用 Whisper）、明显走平台字幕的提交
    // （B 站已填凭据、抖音已验证）、已在任意会话确认过该模型下载的用户
    // （localStorage 长期记忆——前端无法预知本次是否真的会用到 Whisper，
    // 弹窗只负责首次告知下载体积，此后交任务内下载/失败提示）。
    if (!resumeTaskId && forceRestart && !taskWillLikelyUseSubtitles(request)) {
        const whisperOption = byId('whisperModel').selectedOptions[0];
        const status = whisperOption ? whisperOption.dataset.status : 'missing';
        const modelId = whisperOption ? whisperOption.value : '';
        if (
            whisperOption
            && status
            && status !== 'cached'
            && modelId
            && !whisperDownloadConfirmed(modelId)
        ) {
            const modelLabel = (whisperOption.dataset.baseLabel || whisperOption.textContent).split('（')[0];
            const size = WHISPER_MODEL_SIZES[modelId] || '';
            const message = status === 'incomplete'
                ? `所选模型「${modelLabel}」缓存不完整，将自动补全缺失文件（约 ${size}）。是否继续？`
                : `当前缺少所选模型「${modelLabel}」。首次使用需从镜像下载约 ${size}（下载完成后自动开始转写）。是否继续？`;
            if (!window.confirm(message)) {
                showToast('已取消，任务未提交', 'info');
                return;
            }
            rememberWhisperDownloadConfirm(modelId);
        }
    }

    // B 站链接且未填写凭据时，询问是否先补充（AI 字幕需要登录态）
    if (
        !biliPromptDismissed
        && request.sourceType !== 'local'
        && isBilibiliUrl(request.videoUrl)
        && !hasBiliCredentials()
    ) {
        updateBiliHint(true);
        if (window.confirm('检测到 B 站链接。填写访问凭据可优先使用 AI 字幕（更快、更准确）。是否现在填写？')) {
            revealBiliCredentials();
            return;
        }
        biliPromptDismissed = true;
        updateBiliHint();
    }

    resetTaskView();
    setSubmitting(true, request.sourceType === 'local' ? '正在上传' : '正在提交');

    try {
        const videoUrl = request.videoUrl;
        let uploadTaskId = null;

        if (request.sourceType === 'local' && !resumeTaskId) {
            addLog('正在上传本地视频');
            // 仅转录不会走截图阶段，上传时就不必让后端保留视频轨
            const wantScreenshots = !request.transcriptOnly && byId('includeScreenshots').checked;
            const uploadResult = await uploadLocalFile(request.file, wantScreenshots);
            // 文件位置由 upload_task_id 的任务记录携带；video_url 只放网络链接
            uploadTaskId = uploadResult.task_id;
            addLog(`上传完成：${uploadResult.filename || request.file.name}`, 'success');
        }

        const config = buildSummarizeConfig(
            videoUrl, uploadTaskId, resumeTaskId, forceRestart, request.transcriptOnly
        );
        addLog(request.transcriptOnly
            ? '正在提交转写任务（只到字幕稿为止，不调用大模型）'
            : `正在提交总结任务 · ${request.modelConfig.name}`);
        const response = await fetch(`${API_BASE}/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        const data = await readResponse(response, '提交任务失败');

        if (!data.task_id) throw new Error('后端未返回任务 ID');
        currentTaskId = data.task_id;
        setTranscriptTaskView(request.transcriptOnly);
        loadRecentTasks(true);
        if (data.reused_task_id) addLog(`已复用任务 ${data.reused_task_id} 的中间结果`, 'success');
        addLog(`任务已创建：${currentTaskId}`, 'success');
        setTaskActive(true);
        startElapsedTimer(0);
        setTaskState('processing', '处理中');
        setSubmitting(false, '处理中');
        schedulePoll(currentTaskId, 0);
    } catch (error) {
        if (resumeTaskId) currentTaskId = resumeTaskId;
        setSubmitting(false);
        failTask(error.message || '提交失败，请确认本地后端已启动');
    }
}

function validateAndBuildRequestBase(resumeTaskId = null) {
    const sourceType = byId('sourceType').value;
    const transcriptOnly = isTranscriptMode();
    const modelConfig = transcriptOnly ? null : getSelectedModelConfig();
    const apiKey = typedApiKey();

    // 仅转录路线不调用大模型：档案与 Key 都不是这条路的必要条件
    if (!transcriptOnly) {
        if (!modelConfig) return validationError('请选择有效的模型档案');
        if (!modelConfig.model) return validationError('请输入模型 ID');
        if (!modelConfig.baseUrl) return validationError('请输入 API Base URL');
        if (!apiKey && !hasReusableKey()) {
            return validationError(`请输入「${modelConfig.profileLabel}」的 API Key，或点「保存到本机」后复用`);
        }
    }

    if (sourceType === 'local') {
        const file = byId('localFile').files[0];
        if (resumeTaskId) return { sourceType, file: null, videoUrl: '', modelConfig, transcriptOnly };
        if (!file) return validationError('请选择本地视频文件');
        if (file.size > maxUploadBytes) return validationError(`文件不能超过 ${maxUploadLabel}`);
        return { sourceType, file, videoUrl: '', modelConfig, transcriptOnly };
    }

    const videoUrl = normalizeVideoInput(byId('videoUrl').value);
    if (!videoUrl) return validationError('请输入有效链接，或包含 B 站链接 / BV 号的分享文本');
    byId('videoUrl').value = videoUrl;
    return { sourceType, file: null, videoUrl, modelConfig, transcriptOnly };
}

// 宽松识别：完整 http(s) 链接原样返回；否则从分享文本提取 B 站/抖音链接（可缺省 scheme），
// 或裸 BV/av 号补全为视频页 URL；无法识别返回 null。与后端 normalize_video_input 同语义。
function normalizeVideoInput(raw) {
    const value = (raw || '').trim();
    if (!value) return null;
    try {
        const parsed = new URL(value);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return value;
    } catch { /* 非完整链接，走下方提取 */ }
    const link = value.match(/(?:https?:\/\/)?(?:www\.|m\.|v\.)?(?:bilibili\.com|b23\.tv|douyin\.com|iesdouyin\.com)\/\S+/i);
    if (link) {
        const cleaned = link[0].replace(/[.,;:!?。，；：！？、）】」』”/]+$/, '');
        return cleaned.startsWith('http') ? cleaned : `https://${cleaned}`;
    }
    const bv = value.match(/BV[0-9A-Za-z]{10}/);
    if (bv) return `https://www.bilibili.com/video/${bv[0]}`;
    const av = value.match(/av(\d+)/i);
    if (av) return `https://www.bilibili.com/video/av${av[1]}`;
    return null;
}

function validationError(message) {
    showToast(message, 'error');
    return null;
}

function buildSummarizeConfig(
    videoUrl, uploadTaskId, resumeTaskId = null, forceRestart = false, transcriptOnly = false
) {
    const config = {
        video_url: videoUrl,
        prefer_subtitles: true,
        whisper_model: byId('whisperModel').value,
        use_gpu: byId('useGpu').checked,
        processing_mode: forceRestart ? 'restart' : 'reuse'
    };

    if (transcriptOnly) {
        // 这条路上根本没有大模型调用：llm_config 是后端模型的必填字段，给空对象即可，
        // 笔记风格、推理强度和截图字段都不下发
        config.output = 'transcript';
        config.llm_config = {};
    } else {
        const modelConfig = getSelectedModelConfig();
        const typedKey = typedApiKey();
        const llmConfig = {
            model_type: modelConfig.provider,
            base_url: modelConfig.baseUrl,
            model: modelConfig.model
        };
        if (typedKey) llmConfig.api_key = typedKey;
        config.screenshot_interval = normalizeScreenshotInterval();
        config.include_screenshots = byId('includeScreenshots').checked;
        config.summary_style = byId('summaryStyle').value;
        config.reasoning_effort = byId('reasoningEffort').value;
        config.llm_config = llmConfig;
    }

    // 平台字幕（B 站 AI 字幕 / 抖音）两条路都要靠凭据，与是否调用大模型无关
    const sessdata = byId('sessdata').value.trim();
    if (sessdata) {
        config.bilibili_cookie = {
            sessdata,
            bili_jct: byId('biliJct').value.trim(),
            buvid3: byId('buvid3').value.trim()
        };
    }
    if (douyinCookies) {
        config.douyin_cookie = douyinCookies;
    }
    if (uploadTaskId) config.upload_task_id = uploadTaskId;
    if (resumeTaskId && !forceRestart) config.resume_task_id = resumeTaskId;
    return config;
}

async function uploadLocalFile(file, includeScreenshots = false) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('include_screenshots', includeScreenshots ? '1' : '0');
    const response = await fetch(`${API_BASE}/upload`, { method: 'POST', body: formData });
    return readResponse(response, '上传失败');
}

function schedulePoll(taskId, delay = POLL_DELAY_MS) {
    stopPolling();
    pollTimer = window.setTimeout(() => pollTask(taskId), delay);
}

async function pollTask(taskId) {
    pollTimer = null;
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/task/${encodeURIComponent(taskId)}`,
            { cache: 'no-store' },
            POLL_TIMEOUT_MS
        );
        const task = await readResponse(response, '读取任务状态失败');
        pollErrorCount = 0;
        syncElapsedTimer(task.elapsed_seconds);
        byId('networkState').textContent = task.step === 6
            ? task.progress_message || '模型正在生成笔记'
            : '连接正常';
        byId('networkState').classList.remove('network-warning');

        updateProgress(task.progress);
        updateStep(task.step);
        renderTaskAdvisory(task.advisory);
        if (Array.isArray(task.logs) && task.logs.length) updateLogs(task.logs);

        if (task.status === 'completed') {
            await completeTask(task.result, task.elapsed_seconds);
            return;
        }
        if (task.status === 'failed') {
            failTask(task.error || '后端未提供失败原因', task.elapsed_seconds);
            return;
        }
        if (task.status === 'cancelled') {
            markTaskCancelled(
                '任务已取消；已生成的中间文件仍保留在工作目录',
                task.elapsed_seconds
            );
            return;
        }
        schedulePoll(taskId);
    } catch (error) {
        pollErrorCount += 1;
        byId('networkState').textContent = `连接异常，重试 ${pollErrorCount}/${MAX_POLL_ERRORS}`;
        byId('networkState').classList.add('network-warning');
        addLog(`状态连接异常：${error.message}`, 'warning');

        if (pollErrorCount >= MAX_POLL_ERRORS) {
            failTask(`连续 ${MAX_POLL_ERRORS} 次无法读取任务状态。请确认本地后端仍在运行，然后重新提交。`);
            return;
        }
        schedulePoll(taskId, Math.min(POLL_DELAY_MS * pollErrorCount, 8000));
    }
}

function stopPolling() {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
}

function resetElapsedTimer() {
    stopElapsedTimer(0);
    elapsedBaseSeconds = 0;
    elapsedSyncedAt = Date.now();
    renderElapsedTime(0);
}

function startElapsedTimer(initialSeconds = 0) {
    stopElapsedTimer(initialSeconds);
    elapsedBaseSeconds = Math.max(0, Number(initialSeconds) || 0);
    elapsedSyncedAt = Date.now();
    renderElapsedTime(elapsedBaseSeconds);
    elapsedTimer = window.setInterval(() => {
        const seconds = elapsedBaseSeconds + (Date.now() - elapsedSyncedAt) / 1000;
        renderElapsedTime(seconds);
    }, 500);
}

function syncElapsedTimer(rawSeconds) {
    const seconds = Number(rawSeconds);
    if (!Number.isFinite(seconds) || seconds < 0) return;
    elapsedBaseSeconds = seconds;
    elapsedSyncedAt = Date.now();
    renderElapsedTime(seconds);
}

function stopElapsedTimer(finalSeconds = null) {
    if (elapsedTimer !== null) window.clearInterval(elapsedTimer);
    elapsedTimer = null;
    if (finalSeconds !== null) syncElapsedTimer(finalSeconds);
}

function renderElapsedTime(rawSeconds) {
    const total = Math.max(0, Math.floor(Number(rawSeconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    byId('elapsedTime').textContent = hours
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function resetTaskView() {
    stopPolling();
    resetElapsedTimer();
    setTaskActive(false);
    pollErrorCount = 0;
    whisperFallbackNotified = false;
    currentTaskId = null;
    currentMarkdown = '';
    currentHtml = '';
    setTranscriptTaskView(false);
    byId('progressArea').hidden = false;
    byId('resultArea').hidden = true;
    byId('taskError').hidden = true;
    byId('downloadMdBtn').disabled = true;
    byId('copyNoteBtn').disabled = true;
    byId('regenerateBtn').disabled = true;
    byId('outputNotice').hidden = true;
    byId('outputPath').textContent = '';
    renderTaskAdvisory(null);
    byId('networkState').textContent = '准备提交';
    byId('networkState').classList.remove('network-warning');
    clearLogs();
    updateProgress(0);
    updateStep(0);
    setTaskState('processing', '准备中');
}

// 结果区标题、复制按钮文案与进度步数都跟着"这一条是字幕稿还是笔记"变
function setTranscriptTaskView(on) {
    isTranscriptTask = Boolean(on);
    byId('progressArea').classList.toggle('transcript-mode', isTranscriptTask);
    byId('resultTitle').textContent = isTranscriptTask ? '字幕稿（带时间轴）' : '视频笔记';
    byId('copyNoteBtn').textContent = isTranscriptTask ? '复制字幕稿' : '复制笔记';
    byId('regenerateBtn').textContent = isTranscriptTask ? '基于字幕稿生成笔记' : '基于转录重新生成';
}

async function completeTask(result, elapsedSeconds = null) {
    stopPolling();
    stopElapsedTimer(elapsedSeconds ?? result?.processing_seconds ?? null);
    if (!await showResult(result)) return;
    setTaskActive(false);
    updateProgress(100);
    updateStep(7);
    byId('regenerateBtn').disabled = false;
    setTaskState('completed', isTranscriptTask ? '转录完成' : '已完成');
    byId('networkState').textContent = '任务完成';
    addLog(isTranscriptTask ? '带时间轴字幕稿已生成' : '视频笔记已生成', 'success');
    setSubmitButton(false, isTranscriptTask ? '再次转写' : '再次生成');
    showToast(isTranscriptTask ? '字幕稿已生成' : '视频笔记已生成', 'success');
    loadRecentTasks(true);
    // 任务期间可能下载/补全了 Whisper 模型，自动纠正下拉框缓存状态，避免下次提交误弹确认
    refreshWhisperModelHints();
}

function failTask(message, elapsedSeconds = null) {
    stopPolling();
    stopElapsedTimer(elapsedSeconds);
    setTaskActive(false);
    setTaskState('failed', '失败');
    byId('taskError').hidden = false;
    byId('taskErrorMessage').textContent = message;
    byId('networkState').textContent = '任务已停止';
    addLog(`任务失败：${message}`, 'error');
    setSubmitButton(false, '重新提交');
    showToast(message, 'error');
    loadRecentTasks(true);
    refreshWhisperModelHints();
}

async function cancelCurrentTask() {
    if (!currentTaskId || !isTaskActive || isCancelling) return;
    if (!window.confirm('取消当前任务？已生成的音频和转录文件会保留。')) return;

    isCancelling = true;
    const button = byId('cancelTaskBtn');
    button.disabled = true;
    button.textContent = '正在取消';
    try {
        const response = await fetch(`${API_BASE}/task/${encodeURIComponent(currentTaskId)}/cancel`, {
            method: 'POST'
        });
        const data = await readResponse(response, '取消任务失败');
        if (data.status === 'cancelled') {
            markTaskCancelled(
                '任务已取消；已生成的中间文件仍保留在工作目录',
                data.elapsed_seconds
            );
        } else {
            setTaskState('processing', '取消中');
            byId('networkState').textContent = '等待当前步骤停止';
            addLog('取消请求已发送；任务已停止，后台的下载 / 转写线程会在当前这一段结束后退出', 'warning');
            showToast('取消请求已发送', 'info');
        }
    } catch (error) {
        showToast(`取消失败：${error.message}`, 'error');
        button.disabled = false;
    } finally {
        isCancelling = false;
        button.textContent = isTaskActive ? '取消请求已发送' : '取消任务';
    }
}

function markTaskCancelled(message, elapsedSeconds = null) {
    stopPolling();
    stopElapsedTimer(elapsedSeconds);
    setTaskActive(false);
    setTaskState('cancelled', '已取消');
    byId('taskError').hidden = true;
    byId('networkState').textContent = '任务已取消';
    addLog(message, 'warning');
    setSubmitButton(false, '重新开始');
    showToast('任务已取消', 'info');
    loadRecentTasks(true);
    refreshWhisperModelHints();
}

function setSubmitting(active, label = '开始生成') {
    isSubmitting = active;
    setSubmitButton(active, label);
    byId('retryBtn').disabled = active;
    byId('restartBtn').disabled = active;
    byId('regenerateBtn').disabled = active || !currentMarkdown;
    setSourceControlsDisabled(active || isTaskActive);
}

function setTaskActive(active) {
    isTaskActive = active;
    setSourceControlsDisabled(active || isSubmitting);
    byId('submitBtn').disabled = active || isSubmitting;
    const cancelButton = byId('cancelTaskBtn');
    cancelButton.hidden = !active;
    cancelButton.disabled = !active || isCancelling;
}

function setSourceControlsDisabled(disabled) {
    byId('sourceType').disabled = disabled;
    byId('videoUrl').disabled = disabled;
    byId('localFile').disabled = disabled;
    byId('summaryStyle').disabled = disabled;
    byId('reasoningEffort').disabled = disabled;
    document.querySelectorAll('input[name="processingMode"], input[name="outputMode"]').forEach((input) => {
        input.disabled = disabled;
    });
}

function setSubmitButton(loading, label) {
    const button = byId('submitBtn');
    button.disabled = loading || isTaskActive;
    button.classList.toggle('is-loading', loading);
    button.querySelector('.btn-text').textContent = label;
}

function setTaskState(type, text) {
    const state = byId('taskState');
    state.className = `status-badge ${type}`;
    state.textContent = text;
}

function updateProgress(rawProgress) {
    const progress = Math.min(100, Math.max(0, Number(rawProgress) || 0));
    byId('progressFill').style.width = `${progress}%`;
    byId('progressPercent').textContent = `${Math.round(progress)}%`;
    const progressBar = document.querySelector('[role="progressbar"]');
    progressBar.setAttribute('aria-valuenow', String(Math.round(progress)));
}

function updateStep(rawStep) {
    const activeStep = Number(rawStep) || 0;
    document.querySelectorAll('.progress-steps .step').forEach((step, index) => {
        const number = index + 1;
        step.classList.toggle('completed', number < activeStep || activeStep > 6);
        step.classList.toggle('active', number === activeStep);
    });
    byId('progressArea').classList.toggle('llm-active', activeStep === 6 && isTaskActive);
}

function renderTaskAdvisory(message) {
    const advisory = byId('taskAdvisory');
    advisory.textContent = typeof message === 'string' ? message : '';
    advisory.hidden = !advisory.textContent;
}

function clearLogs() {
    byId('logArea').replaceChildren(createLogEntry('等待任务开始'));
}

function addLog(message, type = 'info') {
    const logArea = byId('logArea');
    logArea.appendChild(createLogEntry(message, type, true));
    logArea.scrollTop = logArea.scrollHeight;
}

function updateLogs(logs) {
    const logArea = byId('logArea');
    logArea.replaceChildren(...logs.map((log) => createLogEntry(String(log))));
    logArea.scrollTop = logArea.scrollHeight;
    if (
        !whisperFallbackNotified
        && logs.some((log) => String(log).includes('已自动降级'))
    ) {
        whisperFallbackNotified = true;
        showToast('注意：所选 Whisper 模型未缓存或无法加载，已自动降级为已缓存的模型', 'info');
        // 降级说明本次实际使用了 base，顺手刷新下拉框状态
        refreshWhisperModelHints();
    }
}

function createLogEntry(message, type = 'info', includeTime = false) {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = includeTime ? `[${new Date().toLocaleTimeString()}] ${message}` : message;
    return entry;
}

async function showResult(result) {
    if (!result || typeof result !== 'object') {
        failTask('任务完成，但后端未返回可用的结果');
        return false;
    }
    const transcript = result.output === 'transcript';
    setTranscriptTaskView(transcript);

    let markdown = result.markdown;
    if (transcript && typeof markdown !== 'string') {
        // 转录正文刻意不放进 result：它会被每 2 秒一次的轮询反复搬运并落盘，
        // 所以完成或从历史打开时现读磁盘上的 transcript.md
        try {
            markdown = await fetchTranscriptText();
        } catch (error) {
            failTask(error.message);
            return false;
        }
    }
    if (typeof markdown !== 'string') {
        failTask('任务完成，但后端未返回可用的 Markdown 内容');
        return false;
    }

    currentMarkdown = markdown;
    const content = byId('markdownContent');
    if (transcript) {
        currentHtml = '';
        renderTranscript(content, markdown);
    } else {
        currentHtml = replaceImagePaths(markdown);
        content.innerHTML = renderMarkdown(currentHtml);
    }
    byId('resultArea').hidden = false;
    byId('downloadMdBtn').disabled = false;
    byId('copyNoteBtn').disabled = false;
    const outputDirectory = typeof result.output_directory === 'string'
        ? result.output_directory.trim()
        : '';
    byId('outputPath').textContent = outputDirectory;
    byId('outputNotice').hidden = !outputDirectory;
    const archivedPath = typeof result.archived_path === 'string'
        ? result.archived_path.trim()
        : '';
    byId('archivedPath').textContent = archivedPath;
    byId('archivedNotice').hidden = !archivedPath;
    return true;
}

async function fetchTranscriptText() {
    if (!currentTaskId) throw new Error('没有可读取的转录任务');
    const response = await fetch(
        `${API_BASE}/task/${encodeURIComponent(currentTaskId)}/transcript?output_format=markdown`,
        { cache: 'no-store' }
    );
    const data = await readResponse(response, '读取字幕稿失败');
    if (typeof data.text !== 'string') throw new Error('后端未返回转录正文');
    return data.text;
}

// 一行一段字幕渲染成一行 DOM：时间轴单独成列，正文用 textContent 注入（不过 marked，
// 也没有 HTML 拼接），所以不需要再过一遍 DOMPurify。
function renderTranscript(content, text) {
    const rows = [];
    text.split(/\r?\n/).forEach((rawLine) => {
        const line = rawLine.trim();
        if (!line) return;
        const heading = line.match(/^#{1,6}\s+(.*)$/);
        if (heading) {
            const node = document.createElement('p');
            node.className = 'transcript-heading';
            node.textContent = heading[1];
            rows.push(node);
            return;
        }
        const timed = line.match(/^\[([^\]]+)\]\s*(.*)$/);
        if (!timed) {
            const node = document.createElement('p');
            node.className = 'transcript-plain';
            node.textContent = line;
            rows.push(node);
            return;
        }
        const row = document.createElement('div');
        row.className = 'transcript-line';
        const time = document.createElement('time');
        time.textContent = `[${timed[1]}]`;
        const body = document.createElement('span');
        body.textContent = timed[2];
        row.append(time, body);
        rows.push(row);
    });
    content.replaceChildren(...rows);
}

function replaceImagePaths(markdown) {
    if (!currentTaskId) return markdown;
    return markdown.replace(
        /!\[([^\]]*)\]\(\.\/images\/([^)]+)\)/g,
        `![$1](${API_BASE}/image/${encodeURIComponent(currentTaskId)}/$2)`
    );
}

function renderMarkdown(markdown) {
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
        return `<pre>${escapeHtml(markdown)}</pre>`;
    }
    return DOMPurify.sanitize(marked.parse(markdown), {
        USE_PROFILES: { html: true },
        FORBID_TAGS: [
            'button', 'embed', 'form', 'iframe', 'input', 'math', 'object',
            'option', 'select', 'style', 'svg', 'textarea'
        ],
        FORBID_ATTR: ['style']
    });
}

async function downloadSummary() {
    if (isDownloading || !currentMarkdown) return;
    const format = byId('formatSelect').value;
    if (format === 'png') {
        await exportSummaryImage();
        return;
    }
    if (format === 'markdown') {
        await downloadMarkdownFile();
        return;
    }

    const formats = {
        html: { content: convertToHtml(currentHtml || currentMarkdown), extension: '.html', mime: 'text/html' },
        json: { content: convertToJson(currentMarkdown), extension: '.json', mime: 'application/json' },
        txt: { content: stripMarkdown(currentMarkdown), extension: '.txt', mime: 'text/plain' }
    };
    const selected = formats[format] || { content: currentMarkdown, extension: '.md', mime: 'text/markdown' };
    triggerBlobDownload(new Blob([selected.content], { type: selected.mime }), selected.extension);
    showToast('下载已开始', 'success');
}

function toggleImageLayout() {
    byId('imageLayoutSelect').hidden = byId('formatSelect').value !== 'png';
}

async function copyFullNote() {
    if (!currentMarkdown) return validationError('没有可复制的笔记');
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(currentMarkdown);
        } else {
            copyTextFallback(currentMarkdown);
        }
        showToast('完整笔记已复制', 'success');
    } catch {
        try {
            copyTextFallback(currentMarkdown);
            showToast('完整笔记已复制', 'success');
        } catch (error) {
            showToast(`复制失败：${error.message}`, 'error');
        }
    }
}

function copyTextFallback(value) {
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.setAttribute('readonly', '');
    textarea.className = 'clipboard-fallback';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) throw new Error('浏览器未授予剪贴板权限');
}

async function exportSummaryImage() {
    if (typeof html2canvas === 'undefined') {
        showToast('图片导出组件未加载，请刷新页面后重试', 'error');
        return;
    }
    setDownloading(true);
    const stage = document.createElement('div');
    stage.className = 'image-export-stage';
    const sheet = document.createElement('article');
    sheet.className = 'image-export-sheet';
    sheet.innerHTML = renderMarkdown(currentHtml || currentMarkdown);
    stage.append(sheet);
    document.body.append(stage);
    try {
        if (document.fonts?.ready) await document.fonts.ready;
        await waitForExportImages(sheet);
        const width = sheet.scrollWidth;
        const height = sheet.scrollHeight;
        const areaScale = Math.sqrt(40_000_000 / Math.max(1, width * height));
        const heightScale = 30_000 / Math.max(1, height);
        const scale = Math.min(2, areaScale, heightScale);
        if (scale < 0.75) {
            throw new Error('笔记内容过长，暂时无法稳定导出图片，请改用 HTML 或 Markdown');
        }
        const canvas = await html2canvas(sheet, {
            backgroundColor: '#ffffff',
            scale,
            useCORS: true,
            logging: false,
            width,
            height,
            windowWidth: width
        });
        const requestedLayout = byId('imageLayoutSelect').value;
        const paginate = requestedLayout === 'portrait' || canvas.height > 16_000;
        if (paginate) {
            if (requestedLayout === 'long') {
                showToast('笔记较长，已自动改为 3:4 分页图片', 'info');
            }
            await downloadCanvasPages(canvas);
        } else {
            triggerBlobDownload(await canvasToBlob(canvas), '.png');
        }
        showToast('图片导出已完成', 'success');
    } catch (error) {
        showToast(`图片导出失败：${error.message}`, 'error');
    } finally {
        stage.remove();
        setDownloading(false);
    }
}

async function waitForExportImages(root) {
    const images = Array.from(root.querySelectorAll('img'));
    await Promise.all(images.map((img) => {
        if (img.complete) return Promise.resolve();
        return new Promise((resolve) => {
            img.addEventListener('load', resolve, { once: true });
            img.addEventListener('error', resolve, { once: true });
            window.setTimeout(resolve, 5000);
        });
    }));
}

async function downloadCanvasPages(source) {
    const pageHeight = Math.round(source.width * 4 / 3);
    const pagePadding = Math.max(32, Math.round(source.width * 0.055));
    const contentHeight = pageHeight - pagePadding * 2;
    const overlap = Math.max(24, Math.round(source.width * 0.025));
    const pageAdvance = contentHeight - overlap;
    const pages = source.height <= contentHeight
        ? 1
        : 1 + Math.ceil((source.height - contentHeight) / pageAdvance);
    for (let index = 0; index < pages; index += 1) {
        const page = document.createElement('canvas');
        page.width = source.width;
        page.height = pageHeight;
        const context = page.getContext('2d');
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, page.width, page.height);
        const sourceY = index * pageAdvance;
        const sliceHeight = Math.min(contentHeight, source.height - sourceY);
        context.drawImage(
            source,
            0,
            sourceY,
            source.width,
            sliceHeight,
            0,
            pagePadding,
            source.width,
            sliceHeight
        );
        const suffix = `.page-${String(index + 1).padStart(3, '0')}.png`;
        triggerBlobDownload(await canvasToBlob(page), suffix);
    }
}

function canvasToBlob(canvas) {
    return new Promise((resolve, reject) => {
        canvas.toBlob(
            (blob) => blob ? resolve(blob) : reject(new Error('无法生成 PNG 文件')),
            'image/png'
        );
    });
}

async function downloadMarkdownFile() {
    if (!currentTaskId) return validationError('没有可下载的任务');
    setDownloading(true);
    try {
        const response = await fetch(`${API_BASE}/download/${encodeURIComponent(currentTaskId)}`);
        if (!response.ok) throw new Error(await extractErrorMessage(response, '下载失败'));
        triggerBlobDownload(await response.blob(), '.md');
        showToast('下载已开始', 'success');
    } catch (error) {
        showToast(`下载失败：${error.message}`, 'error');
    } finally {
        setDownloading(false);
    }
}

function setDownloading(active) {
    isDownloading = active;
    const button = byId('downloadMdBtn');
    button.disabled = active;
    button.classList.toggle('is-loading', active);
    button.querySelector('.btn-text').textContent = active ? '准备下载' : '下载';
}

function triggerBlobDownload(blob, extension) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `video_summary_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}${extension}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function convertToHtml(markdown) {
    const body = renderMarkdown(markdown);
    return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>视频笔记</title></head><body><main>${body}</main></body></html>`;
}

function convertToJson(markdown) {
    return JSON.stringify({ summary: markdown, generated_at: new Date().toISOString(), format: 'markdown' }, null, 2);
}

function stripMarkdown(markdown) {
    return markdown.replace(/!\[[^\]]*\]\([^)]+\)/g, '').replace(/[#*`>|]/g, '').replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/\n{3,}/g, '\n\n');
}

function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = value;
    return element.innerHTML;
}

async function readResponse(response, fallbackMessage) {
    if (!response.ok) throw new Error(await extractErrorMessage(response, fallbackMessage));
    try {
        return await response.json();
    } catch {
        throw new Error('后端返回了无法解析的数据');
    }
}

async function fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...options, signal: controller.signal });
    } catch (error) {
        if (error.name === 'AbortError') throw new Error('请求超时');
        throw error;
    } finally {
        window.clearTimeout(timeout);
    }
}

async function extractErrorMessage(response, fallbackMessage) {
    try {
        const data = await response.json();
        return data.detail || data.error || data.message || `${fallbackMessage}（HTTP ${response.status}）`;
    } catch {
        return `${fallbackMessage}（HTTP ${response.status}）`;
    }
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    byId('toastRegion').appendChild(toast);
    window.setTimeout(() => toast.remove(), 4200);
}
