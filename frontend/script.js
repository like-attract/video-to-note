const API_BASE = '/api';
const POLL_DELAY_MS = 2000;
const POLL_TIMEOUT_MS = 12000;
const MAX_POLL_ERRORS = 5;
const CUSTOM_MODEL_ID = '__custom__';
const PREFERENCE_KEYS = [
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
let currentMarkdown = '';
let currentHtml = '';
let pollTimer = null;
let pollErrorCount = 0;
let isSubmitting = false;
let isTaskActive = false;
let isDownloading = false;
let isCancelling = false;
let elapsedTimer = null;
let elapsedBaseSeconds = 0;
let elapsedSyncedAt = 0;

document.addEventListener('DOMContentLoaded', () => {
    window.__videoToNoReady = false;
    // 每一步单独容错：即使浏览器里残留旧版缓存的 HTML（元素缺失），
    // 也只影响对应功能，不会让整页按钮全部失效。
    safeStep(removeLegacySecrets, '清理旧数据');
    safeStep(loadPreferences, '读取偏好设置');
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
    } catch {
        // 保留 HTML 中的构建版本，服务短暂未就绪不影响页面使用。
    }
}

function bindEvents() {
    bindListener('saveConfigBtn', 'click', savePreferences);
    bindListener('resetConfigBtn', 'click', resetPreferences);
    bindListener('submitBtn', 'click', () => startSummary());
    bindListener('retryBtn', 'click', () => startSummary({ resumeCurrent: true }));
    bindListener('restartBtn', 'click', () => startSummary({ forceRestart: true }));
    bindListener('regenerateBtn', 'click', () => startSummary({ resumeCurrent: true }));
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
    bindListener('llmProvider', 'change', handleProviderChange);
    bindListener('llmModel', 'change', toggleCustomConfig);
    bindListener('llmTestBtn', 'click', testLlmConnection);
    bindListener('sourceType', 'change', toggleSourceType);
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
        title.textContent = task.title || '未命名任务';
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
        if (['failed', 'cancelled'].includes(task.status)) {
            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'recent-task-delete';
            deleteButton.dataset.deleteTaskId = task.task_id;
            const statusLabel = task.status === 'cancelled' ? '已取消任务' : '失败任务';
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
        renderTaskAdvisory(task.advisory);
        if (Array.isArray(task.logs)) updateLogs(task.logs);

        if (task.status === 'completed') {
            completeTask(task.result, task.elapsed_seconds);
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

function loadPreferences() {
    const legacyChoice = localStorage.getItem('llm_model');
    const savedProvider = localStorage.getItem('llm_provider') || LEGACY_PROVIDER_MAP[legacyChoice] || 'deepseek';
    byId('llmProvider').value = PROVIDER_CONFIG[savedProvider] ? savedProvider : 'deepseek';
    byId('customBaseUrl').value = localStorage.getItem('custom_base_url') || '';
    byId('customModelName').value = localStorage.getItem('custom_model_name') || '';
    populateModelOptions(localStorage.getItem('llm_model_id'));
    byId('whisperModel').value = localStorage.getItem('whisper_model') || 'base';
    snapshotWhisperLabels();
    refreshWhisperModelHints();
    byId('screenshotInterval').value = localStorage.getItem('screenshot_interval') || '10';
    byId('includeScreenshots').checked = localStorage.getItem('include_screenshots') === 'true';
    byId('useGpu').checked = localStorage.getItem('use_gpu') === 'true';
    byId('summaryStyle').value = localStorage.getItem('summary_style') || 'detailed';
    const savedReasoning = localStorage.getItem('reasoning_effort') || 'auto';
    byId('reasoningEffort').value = ['auto', 'off', 'high', 'max'].includes(savedReasoning)
        ? savedReasoning : 'auto';
    const processingMode = localStorage.getItem('processing_mode') || 'restart';
    const processingModeInput = document.querySelector(`input[name="processingMode"][value="${processingMode}"]`);
    if (processingModeInput) processingModeInput.checked = true;
    toggleScreenshotSettings();
    applyTheme(localStorage.getItem('theme') || 'system');
}

function initThemeControl() {
    byId('themeControl').addEventListener('click', (event) => {
        const button = event.target.closest('[data-theme-option]');
        if (!button) return;
        const theme = button.dataset.themeOption;
        localStorage.setItem('theme', theme);
        applyTheme(theme);
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

function savePreferences() {
    const interval = normalizeScreenshotInterval();
    localStorage.setItem('llm_provider', byId('llmProvider').value);
    localStorage.setItem('llm_model_id', byId('llmModel').value);
    localStorage.removeItem('llm_model');
    localStorage.setItem('custom_base_url', byId('customBaseUrl').value.trim());
    localStorage.setItem('custom_model_name', byId('customModelName').value.trim());
    localStorage.setItem('whisper_model', byId('whisperModel').value);
    localStorage.setItem('screenshot_interval', String(interval));
    localStorage.setItem('include_screenshots', String(byId('includeScreenshots').checked));
    localStorage.setItem('use_gpu', String(byId('useGpu').checked));
    localStorage.setItem('summary_style', byId('summaryStyle').value);
    localStorage.setItem('reasoning_effort', byId('reasoningEffort').value);
    localStorage.setItem(
        'processing_mode',
        document.querySelector('input[name="processingMode"]:checked')?.value || 'reuse'
    );
    showToast('非敏感偏好已保存', 'success');
}

function resetPreferences() {
    PREFERENCE_KEYS.forEach((key) => localStorage.removeItem(key));
    loadPreferences();
    showToast('已恢复默认偏好', 'info');
}

function handleProviderChange() {
    populateModelOptions();
}

function populateModelOptions(preferredModel = null) {
    const provider = byId('llmProvider').value;
    const providerConfig = PROVIDER_CONFIG[provider];
    const modelSelect = byId('llmModel');
    modelSelect.replaceChildren();

    providerConfig.models.forEach(([modelId, label]) => {
        const option = new Option(label, modelId);
        option.title = modelId;
        modelSelect.add(option);
    });
    modelSelect.add(new Option('手动输入模型 ID…', CUSTOM_MODEL_ID));

    const requestedModel = preferredModel || providerConfig.defaultModel;
    const hasPreset = Array.from(modelSelect.options).some((option) => option.value === requestedModel);
    modelSelect.value = hasPreset ? requestedModel : CUSTOM_MODEL_ID;
    if (!hasPreset && requestedModel && requestedModel !== CUSTOM_MODEL_ID && !byId('customModelName').value) {
        byId('customModelName').value = requestedModel;
    }
    toggleCustomConfig();
}

function toggleCustomConfig() {
    const customProvider = byId('llmProvider').value === 'custom';
    const customModel = byId('llmModel').value === CUSTOM_MODEL_ID;
    byId('customBaseUrlField').hidden = !customProvider;
    byId('customModelNameField').hidden = !customModel;
    byId('customApiConfig').hidden = !customProvider;
}

async function testLlmConnection() {
    const button = byId('llmTestBtn');
    const status = byId('llmTestStatus');
    const modelConfig = getSelectedModelConfig();
    if (!modelConfig) return;
    const apiKey = byId('apiKey').value.trim();
    if (!apiKey) {
        status.textContent = '请先填写 API Key';
        status.className = 'llm-test-status error';
        return;
    }
    button.disabled = true;
    status.textContent = '测试中…';
    status.className = 'llm-test-status testing';
    try {
        const response = await fetchWithTimeout(
            `${API_BASE}/llm-test`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model_type: modelConfig.provider,
                    api_key: apiKey,
                    base_url: modelConfig.baseUrl || undefined,
                    model: modelConfig.model
                })
            },
            30_000
        );
        const data = await readResponse(response, '测试请求失败');
        status.textContent = data.ok ? data.message : (data.message || data.error || '连接失败');
        status.className = data.ok ? 'llm-test-status ok' : 'llm-test-status error';
    } catch (error) {
        status.textContent = `测试失败：${error.message}`;
        status.className = 'llm-test-status error';
    } finally {
        button.disabled = false;
    }
}

function getSelectedModelConfig() {
    const provider = byId('llmProvider').value;
    const providerConfig = PROVIDER_CONFIG[provider];
    if (!providerConfig) return null;

    const selectedModel = byId('llmModel').value;
    const model = selectedModel === CUSTOM_MODEL_ID
        ? byId('customModelName').value.trim()
        : selectedModel;
    const baseUrl = provider === 'custom'
        ? byId('customBaseUrl').value.trim()
        : providerConfig.baseUrl;
    const modelLabel = selectedModel === CUSTOM_MODEL_ID
        ? model
        : byId('llmModel').selectedOptions[0]?.textContent;
    return {
        provider,
        baseUrl,
        model,
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
    try {
        return localStorage.getItem(`whisper_confirm_${modelId}`) === '1';
    } catch (_error) {
        return false;
    }
}

function rememberWhisperDownloadConfirm(modelId) {
    try {
        localStorage.setItem(`whisper_confirm_${modelId}`, '1');
    } catch (_error) {
        // 隐私模式等无法写入存储时，只影响本次会话的重复提醒
    }
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
        : '支持常见视频格式，单个文件不超过 500 MB';
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
        let videoUrl = request.videoUrl;
        let uploadTaskId = null;

        if (request.sourceType === 'local' && !resumeTaskId) {
            addLog('正在上传本地视频');
            const uploadResult = await uploadLocalFile(request.file);
            videoUrl = uploadResult.file_path;
            uploadTaskId = uploadResult.task_id;
            addLog(`上传完成：${uploadResult.filename || request.file.name}`, 'success');
        }

        const config = buildSummarizeConfig(videoUrl, uploadTaskId, resumeTaskId, forceRestart);
        addLog(`正在提交总结任务 · ${request.modelConfig.name}`);
        const response = await fetch(`${API_BASE}/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        const data = await readResponse(response, '提交任务失败');

        if (!data.task_id) throw new Error('后端未返回任务 ID');
        currentTaskId = data.task_id;
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
    const modelConfig = getSelectedModelConfig();
    const apiKey = byId('apiKey').value.trim();

    if (!modelConfig) return validationError('请选择有效的总结模型');
    if (!apiKey) return validationError(`请输入 ${modelConfig.name} 的 API Key`);
    if (!modelConfig.model) return validationError('请输入模型 ID');
    if (!modelConfig.baseUrl) return validationError('请输入 API Base URL');

    if (sourceType === 'local') {
        const file = byId('localFile').files[0];
        if (resumeTaskId) return { sourceType, file: null, videoUrl: '', modelConfig };
        if (!file) return validationError('请选择本地视频文件');
        if (file.size > 500 * 1024 * 1024) return validationError('文件不能超过 500 MB');
        return { sourceType, file, videoUrl: '', modelConfig };
    }

    const videoUrl = normalizeVideoInput(byId('videoUrl').value);
    if (!videoUrl) return validationError('请输入有效链接，或包含 B 站链接 / BV 号的分享文本');
    byId('videoUrl').value = videoUrl;
    return { sourceType, file: null, videoUrl, modelConfig };
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

function buildSummarizeConfig(videoUrl, uploadTaskId, resumeTaskId = null, forceRestart = false) {
    const modelConfig = getSelectedModelConfig();
    const llmConfig = {
        model_type: modelConfig.provider,
        api_key: byId('apiKey').value.trim(),
        base_url: modelConfig.baseUrl,
        model: modelConfig.model
    };

    const config = {
        video_url: videoUrl,
        screenshot_interval: normalizeScreenshotInterval(),
        prefer_subtitles: true,
        include_screenshots: byId('includeScreenshots').checked,
        whisper_model: byId('whisperModel').value,
        use_gpu: byId('useGpu').checked,
        processing_mode: forceRestart ? 'restart' : 'reuse',
        summary_style: byId('summaryStyle').value,
        reasoning_effort: byId('reasoningEffort').value,
        llm_config: llmConfig
    };

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

async function uploadLocalFile(file) {
    const formData = new FormData();
    formData.append('file', file);
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
            completeTask(task.result, task.elapsed_seconds);
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

function completeTask(result, elapsedSeconds = null) {
    stopPolling();
    stopElapsedTimer(elapsedSeconds ?? result?.processing_seconds ?? null);
    if (!showResult(result)) return;
    setTaskActive(false);
    updateProgress(100);
    updateStep(7);
    byId('regenerateBtn').disabled = false;
    setTaskState('completed', '已完成');
    byId('networkState').textContent = '任务完成';
    addLog('视频笔记已生成', 'success');
    setSubmitButton(false, '再次生成');
    showToast('视频笔记已生成', 'success');
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
            addLog('取消请求已发送；当前下载、转写或模型调用返回后将停止', 'warning');
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
    document.querySelectorAll('input[name="processingMode"]').forEach((input) => {
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

function showResult(result) {
    if (!result || typeof result.markdown !== 'string') {
        failTask('任务完成，但后端未返回可用的 Markdown 内容');
        return false;
    }

    currentMarkdown = result.markdown;
    currentHtml = replaceImagePaths(result.markdown);
    const content = byId('markdownContent');
    content.innerHTML = renderMarkdown(currentHtml);
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
