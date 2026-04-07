// API配置
const API_BASE = 'http://localhost:8000/api';

// DOM元素
let currentTaskId = null;
let pollInterval = null;
let currentMarkdown = '';
let currentHtml = '';
let currentJson = null;

// 模型配置映射
const MODEL_CONFIG = {
    deepseek: {
        name: 'DeepSeek',
        base_url: 'https://api.deepseek.com',
        model: 'deepseek-chat',
        need_api_key: true
    },
    openai: {
        name: 'OpenAI GPT-4o-mini',
        base_url: 'https://api.openai.com/v1',
        model: 'gpt-4o-mini',
        need_api_key: true
    },
    openai_gpt4: {
        name: 'OpenAI GPT-4o',
        base_url: 'https://api.openai.com/v1',
        model: 'gpt-4o',
        need_api_key: true
    },
    openai_gpt35: {
        name: 'OpenAI GPT-3.5-Turbo',
        base_url: 'https://api.openai.com/v1',
        model: 'gpt-3.5-turbo',
        need_api_key: true
    },
    qwen: {
        name: '通义千问',
        base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: 'qwen-plus',
        need_api_key: true
    },
    glm: {
        name: '智谱清言',
        base_url: 'https://open.bigmodel.cn/api/paas/v4',
        model: 'glm-4-flash',
        need_api_key: true
    },
    moonshot: {
        name: '月之暗面 Kimi',
        base_url: 'https://api.moonshot.cn/v1',
        model: 'moonshot-v1-8k',
        need_api_key: true
    },
    custom: {
        name: '自定义',
        base_url: '',
        model: '',
        need_api_key: true
    }
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    bindEvents();
    document.getElementById('llmModel').addEventListener('change', toggleCustomConfig);
    document.getElementById('sourceType').addEventListener('change', toggleSourceType);
});

function bindEvents() {
    document.getElementById('saveConfigBtn').addEventListener('click', saveConfig);
    document.getElementById('resetConfigBtn').addEventListener('click', resetConfig);
    document.getElementById('submitBtn').addEventListener('click', startSummary);
    document.getElementById('downloadMdBtn').addEventListener('click', downloadSummary);
}

function toggleCustomConfig() {
    const llmModel = document.getElementById('llmModel').value;
    const customConfig = document.getElementById('customApiConfig');
    customConfig.style.display = llmModel === 'custom' ? 'block' : 'none';
}

function toggleSourceType() {
    const sourceType = document.getElementById('sourceType').value;
    const urlInput = document.getElementById('videoUrl');
    const fileInput = document.getElementById('localFile');
    const fileInfo = document.getElementById('fileInfo');

    if (sourceType === 'local') {
        urlInput.style.display = 'none';
        fileInput.style.display = 'block';
        fileInfo.style.display = 'block';
        fileInfo.innerHTML = '📁 支持的格式：MP4、MOV、AVI、MKV、FLV、WEBM（最大500MB）';
    } else {
        urlInput.style.display = 'block';
        fileInput.style.display = 'none';
        fileInfo.style.display = 'none';
    }
}

// 配置管理
function loadConfig() {
    document.getElementById('llmModel').value = localStorage.getItem('llm_model') || 'deepseek';
    document.getElementById('customBaseUrl').value = localStorage.getItem('custom_base_url') || '';
    document.getElementById('customModelName').value = localStorage.getItem('custom_model_name') || '';
    document.getElementById('customApiKey').value = localStorage.getItem('custom_api_key') || '';
    document.getElementById('sessdata').value = localStorage.getItem('bili_sessdata') || '';
    document.getElementById('biliJct').value = localStorage.getItem('bili_jct') || '';
    document.getElementById('buvid3').value = localStorage.getItem('bili_buvid3') || '';
    document.getElementById('whisperModel').value = localStorage.getItem('whisper_model') || 'base';
    document.getElementById('screenshotInterval').value = localStorage.getItem('screenshot_interval') || '10';
    document.getElementById('useGpu').checked = localStorage.getItem('use_gpu') === 'true';

    toggleCustomConfig();
    toggleSourceType();
}

function saveConfig() {
    localStorage.setItem('llm_model', document.getElementById('llmModel').value);
    localStorage.setItem('custom_base_url', document.getElementById('customBaseUrl').value);
    localStorage.setItem('custom_model_name', document.getElementById('customModelName').value);
    localStorage.setItem('custom_api_key', document.getElementById('customApiKey').value);
    localStorage.setItem('bili_sessdata', document.getElementById('sessdata').value);
    localStorage.setItem('bili_jct', document.getElementById('biliJct').value);
    localStorage.setItem('bili_buvid3', document.getElementById('buvid3').value);
    localStorage.setItem('whisper_model', document.getElementById('whisperModel').value);
    localStorage.setItem('screenshot_interval', document.getElementById('screenshotInterval').value);
    localStorage.setItem('use_gpu', document.getElementById('useGpu').checked);

    showToast('配置已保存', 'success');
}

function resetConfig() {
    localStorage.clear();
    loadConfig();
    showToast('已重置为默认配置', 'info');
}

function getApiKeyForModel(modelType) {
    if (modelType === 'custom') {
        return document.getElementById('customApiKey').value;
    }

    const keyMap = {
        deepseek: 'deepseek_api_key',
        openai: 'openai_api_key',
        openai_gpt4: 'openai_api_key',
        openai_gpt35: 'openai_api_key',
        qwen: 'qwen_api_key',
        glm: 'zhipu_api_key',
        moonshot: 'moonshot_api_key'
    };

    const storageKey = keyMap[modelType];
    let apiKey = localStorage.getItem(storageKey);

    if (!apiKey) {
        const modelName = MODEL_CONFIG[modelType]?.name || modelType;
        apiKey = prompt(`请输入 ${modelName} 的 API Key：`);
        if (apiKey) {
            localStorage.setItem(storageKey, apiKey);
        }
    }

    return apiKey;
}

// 上传本地文件
async function uploadLocalFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE}/upload`, {
        method: 'POST',
        body: formData
    });

    if (!response.ok) {
        throw new Error('上传失败');
    }

    const data = await response.json();
    return { file_path: data.file_path, task_id: data.task_id, filename: data.filename };
}

// 主要功能
async function startSummary() {
    const sourceType = document.getElementById('sourceType').value;
    let videoUrl = '';
    let uploadTaskId = null;

    if (sourceType === 'local') {
        const file = document.getElementById('localFile').files[0];
        if (!file) {
            showToast('请选择本地视频文件', 'error');
            return;
        }

        // 检查文件大小（限制500MB）
        if (file.size > 500 * 1024 * 1024) {
            showToast('文件过大，请选择小于500MB的视频', 'error');
            return;
        }

        showToast('正在上传文件...', 'info');
        try {
            const uploadResult = await uploadLocalFile(file);
            videoUrl = uploadResult.file_path;
            uploadTaskId = uploadResult.task_id;
            showToast(`文件上传成功: ${uploadResult.filename}`, 'success');
        } catch (error) {
            showToast('文件上传失败: ' + error.message, 'error');
            return;
        }
    } else {
        videoUrl = document.getElementById('videoUrl').value.trim();
        if (!videoUrl) {
            showToast('请输入视频链接', 'error');
            return;
        }
    }

    const llmModel = document.getElementById('llmModel').value;
    const modelConfig = MODEL_CONFIG[llmModel];

    if (!modelConfig) {
        showToast('请选择有效的 AI 模型', 'error');
        return;
    }

    let apiKey = '';
    if (modelConfig.need_api_key) {
        apiKey = getApiKeyForModel(llmModel);
        if (!apiKey) {
            showToast(`请先配置 ${modelConfig.name} 的 API Key`, 'error');
            return;
        }
    }

    // 构建 LLM 配置
    let llmConfig = {
        model_type: llmModel,
        api_key: apiKey
    };

    if (llmModel === 'custom') {
        llmConfig.custom_base_url = document.getElementById('customBaseUrl').value;
        llmConfig.custom_model_name = document.getElementById('customModelName').value;
        if (!llmConfig.custom_base_url || !llmConfig.custom_model_name) {
            showToast('自定义模型需要填写 Base URL 和模型名称', 'error');
            return;
        }
    } else {
        llmConfig.base_url = modelConfig.base_url;
        llmConfig.model = modelConfig.model;
    }

    // 构建 Cookie 配置（仅当有值时传递）
    const sessdata = document.getElementById('sessdata').value.trim();
    let bilibiliCookie = null;
    if (sessdata) {
        bilibiliCookie = {
            sessdata: sessdata,
            bili_jct: document.getElementById('biliJct').value.trim(),
            buvid3: document.getElementById('buvid3').value.trim()
        };
    }

    // 收集配置
    const config = {
        video_url: videoUrl,
        screenshot_interval: parseInt(document.getElementById('screenshotInterval').value),
        whisper_model: document.getElementById('whisperModel').value,
        use_gpu: document.getElementById('useGpu').checked,
        llm_config: llmConfig
    };

    if (bilibiliCookie) {
        config.bilibili_cookie = bilibiliCookie;
    }

    // 如果是本地上传的文件，传递upload_task_id
    if (uploadTaskId) {
        config.upload_task_id = uploadTaskId;
    }

    // 显示进度区
    document.getElementById('progressArea').style.display = 'block';
    document.getElementById('resultArea').style.display = 'none';
    clearLogs();
    updateProgress(0);
    updateStep(0);

    addLog(`🚀 正在提交任务... 使用模型: ${modelConfig.name}`);
    addLog(`📌 视频来源: ${sourceType === 'local' ? '本地文件' : '在线视频'}`);

    try {
        const response = await fetch(`${API_BASE}/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '提交失败');
        }

        const data = await response.json();
        currentTaskId = data.task_id;
        addLog(`✅ 任务已创建，ID: ${currentTaskId}`);

        startPolling(currentTaskId);

    } catch (error) {
        addLog(`❌ 提交失败: ${error.message}`);
        showToast('提交失败，请检查后端服务是否启动', 'error');
    }
}

function startPolling(taskId) {
    if (pollInterval) clearInterval(pollInterval);

    pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE}/task/${taskId}`);
            if (!response.ok) throw new Error('获取状态失败');

            const task = await response.json();

            updateProgress(task.progress);
            updateStep(task.step);

            if (task.logs && task.logs.length > 0) {
                updateLogs(task.logs);
            }

            if (task.status === 'completed') {
                clearInterval(pollInterval);
                pollInterval = null;
                showResult(task.result);
                addLog('🎉 视频总结完成！');
                showToast('总结完成！', 'success');
            } else if (task.status === 'failed') {
                clearInterval(pollInterval);
                pollInterval = null;
                addLog(`❌ 任务失败: ${task.error}`);
                showToast('任务失败，请查看日志', 'error');
            }

        } catch (error) {
            console.error('轮询错误:', error);
        }
    }, 2000);
}

function updateProgress(progress) {
    const fill = document.getElementById('progressFill');
    if (fill) fill.style.width = `${progress}%`;
}

function updateStep(step) {
    const steps = document.querySelectorAll('.step');
    steps.forEach((stepEl, index) => {
        stepEl.classList.remove('active', 'completed');
        if (index + 1 < step) stepEl.classList.add('completed');
        else if (index + 1 === step) stepEl.classList.add('active');
    });
}

function addLog(message) {
    const logArea = document.getElementById('logArea');
    if (logArea) {
        const logEntry = document.createElement('div');
        logEntry.className = 'log-entry';
        logEntry.innerHTML = `[${new Date().toLocaleTimeString()}] ${message}`;
        logArea.appendChild(logEntry);
        logArea.scrollTop = logArea.scrollHeight;
    }
}

function updateLogs(logs) {
    const logArea = document.getElementById('logArea');
    if (logArea && logs && logs.length > 0) {
        logArea.innerHTML = '';
        logs.forEach(log => {
            const logEntry = document.createElement('div');
            logEntry.className = 'log-entry';
            logEntry.innerHTML = `[${new Date().toLocaleTimeString()}] ${log}`;
            logArea.appendChild(logEntry);
        });
        logArea.scrollTop = logArea.scrollHeight;
    }
}

function clearLogs() {
    const logArea = document.getElementById('logArea');
    if (logArea) logArea.innerHTML = '<div class="log-entry">等待任务开始...</div>';
}

function showResult(result) {
    const resultArea = document.getElementById('resultArea');
    const markdownContent = document.getElementById('markdownContent');

    if (resultArea && markdownContent && result && result.markdown) {
        currentMarkdown = result.markdown;
        // currentTaskId 已经在 startSummary 中设置

        // 处理markdown中的图片路径，替换为完整的API URL
        // 将 ./images/filename.jpg 替换为 http://localhost:8000/api/image/{task_id}/filename.jpg
        let processedMarkdown = result.markdown;
        if (currentTaskId) {
            processedMarkdown = processedMarkdown.replace(
                /!\[([^\]]*)\]\(\.\/images\/([^)]+)\)/g,
                `![$1](${API_BASE}/image/${currentTaskId}/$2)`
            );
        }

        if (typeof marked !== 'undefined') {
            markdownContent.innerHTML = marked.parse(processedMarkdown);
        } else {
            markdownContent.innerHTML = `<pre>${processedMarkdown}</pre>`;
        }

        resultArea.style.display = 'block';
        
        // 保存处理后的markdown用于HTML下载（使用完整URL）
        currentHtml = processedMarkdown;
    }
}

async function downloadSummary() {
    const format = document.getElementById('formatSelect').value;
    
    // 如果是markdown格式，下载zip包含图片
    if (format === 'markdown') {
        await downloadMarkdownWithImages();
        return;
    }
    
    let content = currentMarkdown;
    let extension = '.md';
    let mimeType = 'text/markdown';

    switch(format) {
        case 'html':
            // 使用已经处理过的markdown（带完整URL）
            content = convertToHtml(currentHtml || currentMarkdown);
            extension = '.html';
            mimeType = 'text/html';
            break;
        case 'json':
            content = convertToJson(currentMarkdown);
            extension = '.json';
            mimeType = 'application/json';
            break;
        case 'txt':
            content = stripMarkdown(currentMarkdown);
            extension = '.txt';
            mimeType = 'text/plain';
            break;
        default:
            extension = '.md';
            mimeType = 'text/markdown';
    }

    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `video_summary_${new Date().toISOString().slice(0, 19)}${extension}`;
    a.click();
    URL.revokeObjectURL(url);

    showToast('下载成功', 'success');
}

async function downloadMarkdownWithImages() {
    if (!currentTaskId) {
        showToast('没有可下载的任务', 'error');
        return;
    }
    
    showToast('正在打包下载...', 'info');
    
    try {
        const response = await fetch(`${API_BASE}/download/${currentTaskId}`);
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || '下载失败');
        }
        
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `video_summary_${new Date().toISOString().slice(0, 19)}.zip`;
        a.click();
        URL.revokeObjectURL(url);
        
        showToast('下载成功', 'success');
    } catch (error) {
        showToast('下载失败: ' + error.message, 'error');
    }
}

function convertToHtml(markdown) {
    // 先将相对路径替换为完整URL
    let processedMarkdown = markdown;
    if (currentTaskId) {
        processedMarkdown = markdown.replace(
            /!\[([^\]]*)\]\(\.\/images\/([^)]+)\)/g,
            `![$1](${API_BASE}/image/${currentTaskId}/$2)`
        );
    }
    
    if (typeof marked !== 'undefined') {
        return marked.parse(processedMarkdown);
    }
    return `<pre>${processedMarkdown}</pre>`;
}

function convertToJson(markdown) {
    const jsonObj = {
        summary: markdown,
        generated_at: new Date().toISOString(),
        format: 'markdown'
    };
    return JSON.stringify(jsonObj, null, 2);
}

function stripMarkdown(markdown) {
    return markdown
        .replace(/[#*`>\[\]()|]/g, '')
        .replace(/\n{3,}/g, '\n\n');
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        padding: 12px 24px;
        background: ${type === 'error' ? '#ef4444' : type === 'success' ? '#10b981' : '#3b82f6'};
        color: white;
        border-radius: 8px;
        z-index: 1000;
        animation: slideIn 0.3s ease;
    `;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { transform: translateX(100%); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOut {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);