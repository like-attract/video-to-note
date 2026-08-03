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
    'moonshot_api_key'
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
    removeLegacySecrets();
    loadPreferences();
    bindEvents();
    toggleSourceType();
});

function bindEvents() {
    byId('saveConfigBtn').addEventListener('click', savePreferences);
    byId('resetConfigBtn').addEventListener('click', resetPreferences);
    byId('submitBtn').addEventListener('click', () => startSummary());
    byId('retryBtn').addEventListener('click', () => startSummary({ resumeCurrent: true }));
    byId('restartBtn').addEventListener('click', () => startSummary({ forceRestart: true }));
    byId('regenerateBtn').addEventListener('click', () => startSummary({ resumeCurrent: true }));
    byId('cancelTaskBtn').addEventListener('click', cancelCurrentTask);
    byId('downloadMdBtn').addEventListener('click', downloadSummary);
    byId('llmProvider').addEventListener('change', handleProviderChange);
    byId('llmModel').addEventListener('change', toggleCustomConfig);
    byId('sourceType').addEventListener('change', toggleSourceType);
    byId('videoUrl').addEventListener('input', () => {
        window.clearTimeout(biliHintDebounce);
        biliHintDebounce = window.setTimeout(() => updateBiliHint(), 300);
    });
    byId('biliHintScanBtn').addEventListener('click', startBiliLogin);
    byId('biliHintManualBtn').addEventListener('click', revealBiliCredentials);
    initBiliLogin();
    byId('includeScreenshots').addEventListener('change', toggleScreenshotSettings);
    byId('localFile').addEventListener('change', updateFileInfo);
    byId('videoUrl').addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.isComposing) startSummary();
    });
    initThemeControl();
    window.addEventListener('beforeunload', () => {
        stopPolling();
        stopElapsedTimer();
    });
}

function byId(id) {
    return document.getElementById(id);
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
    byId('screenshotInterval').value = localStorage.getItem('screenshot_interval') || '10';
    byId('includeScreenshots').checked = localStorage.getItem('include_screenshots') === 'true';
    byId('useGpu').checked = localStorage.getItem('use_gpu') === 'true';
    byId('summaryStyle').value = localStorage.getItem('summary_style') || 'detailed';
    byId('reasoningEffort').value = localStorage.getItem('reasoning_effort') || 'auto';
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

function isBilibiliUrl(value) {
    return /bilibili\.com|b23\.tv|BV[0-9A-Za-z]{10}/.test(value);
}

function hasBiliCredentials() {
    return Boolean(
        byId('sessdata').value.trim()
        || byId('biliJct').value.trim()
        || byId('buvid3').value.trim()
    );
}

function updateBiliHint(forceShow = false) {
    const isBili = byId('sourceType').value !== 'local'
        && isBilibiliUrl(byId('videoUrl').value.trim());
    byId('biliHint').hidden = !(forceShow || (isBili && !hasBiliCredentials()));
}

function revealBiliCredentials() {
    updateBiliHint(true);
    const section = byId('credentialsSection');
    section.open = true;
    section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    byId('sessdata').focus();
}

let biliLoginTimer = null;

function initBiliLogin() {
    byId('biliLoginBtn').addEventListener('click', startBiliLogin);
    byId('biliLoginCancelBtn').addEventListener('click', cancelBiliLogin);
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
        byId('biliLoginStatus').textContent = data.message || '请在弹出的窗口中扫码登录';
        byId('biliLoginCancelBtn').hidden = false;
        byId('biliLoginBtn').disabled = true;
        biliLoginTimer = window.setInterval(pollBiliLogin, 2000);
        pollBiliLogin();
    } catch (error) {
        showToast(`启动失败：${error.message}`, 'error');
    }
}

async function pollBiliLogin() {
    try {
        const response = await fetch(`${API_BASE}/bili-login/status`, { cache: 'no-store' });
        const data = await readResponse(response, '读取登录状态失败');
        byId('biliLoginStatus').textContent = data.message || '';
        if (data.state === 'ready' && data.cookies) {
            stopBiliLoginPolling();
            byId('sessdata').value = data.cookies.sessdata || '';
            byId('biliJct').value = data.cookies.bili_jct || '';
            byId('buvid3').value = data.cookies.buvid3 || '';
            showToast('B 站凭据已导入（仅本次会话）', 'success');
            updateBiliHint();
            cancelBiliLogin();
        } else if (['failed', 'timeout'].includes(data.state)) {
            stopBiliLoginPolling();
            showToast(data.message || '导入失败', 'error');
        }
    } catch (error) {
        stopBiliLoginPolling();
        showToast(`登录状态异常：${error.message}`, 'error');
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

    const videoUrl = byId('videoUrl').value.trim();
    if (!videoUrl) return validationError('请输入视频链接');
    try {
        const parsed = new URL(videoUrl);
        if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error();
    } catch {
        return validationError('请输入有效的 http(s) 视频链接');
    }
    return { sourceType, file: null, videoUrl, modelConfig };
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
        byId('networkState').textContent = '连接正常';
        byId('networkState').classList.remove('network-warning');

        updateProgress(task.progress);
        updateStep(task.step);
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
    currentTaskId = null;
    currentMarkdown = '';
    currentHtml = '';
    byId('progressArea').hidden = false;
    byId('resultArea').hidden = true;
    byId('taskError').hidden = true;
    byId('downloadMdBtn').disabled = true;
    byId('regenerateBtn').disabled = true;
    byId('outputNotice').hidden = true;
    byId('outputPath').textContent = '';
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
    updateStep(6);
    byId('regenerateBtn').disabled = false;
    setTaskState('completed', '已完成');
    byId('networkState').textContent = '任务完成';
    addLog('视频笔记已生成', 'success');
    setSubmitButton(false, '再次生成');
    showToast('视频笔记已生成', 'success');
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
        markTaskCancelled(
            '任务已取消；已生成的中间文件仍保留在工作目录',
            data.elapsed_seconds
        );
    } catch (error) {
        showToast(`取消失败：${error.message}`, 'error');
        button.disabled = false;
    } finally {
        isCancelling = false;
        button.textContent = '取消任务';
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
        step.classList.toggle('completed', number < activeStep || activeStep > 5);
        step.classList.toggle('active', number === activeStep);
    });
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
    if (typeof marked !== 'undefined') {
        content.innerHTML = marked.parse(currentHtml);
    } else {
        const pre = document.createElement('pre');
        pre.textContent = currentHtml;
        content.replaceChildren(pre);
    }
    byId('resultArea').hidden = false;
    byId('downloadMdBtn').disabled = false;
    const outputDirectory = typeof result.output_directory === 'string'
        ? result.output_directory.trim()
        : '';
    byId('outputPath').textContent = outputDirectory;
    byId('outputNotice').hidden = !outputDirectory;
    return true;
}

function replaceImagePaths(markdown) {
    if (!currentTaskId) return markdown;
    return markdown.replace(
        /!\[([^\]]*)\]\(\.\/images\/([^)]+)\)/g,
        `![$1](${API_BASE}/image/${encodeURIComponent(currentTaskId)}/$2)`
    );
}

async function downloadSummary() {
    if (isDownloading || !currentMarkdown) return;
    const format = byId('formatSelect').value;
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
    const body = typeof marked !== 'undefined' ? marked.parse(markdown) : `<pre>${escapeHtml(markdown)}</pre>`;
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
