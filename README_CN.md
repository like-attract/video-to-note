# 🎬 VideoToNotes - 多平台视频智能总结助手

<div align="center">

**将本地/YouTube/B站等视频一键转换为结构化笔记**

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

支持 B站 · YouTube · 抖音 · 快手 · 腾讯视频 · 爱奇艺 · 优酷 · 本地视频

</div>

---

## ✨ 功能特性

- 🌐 **多平台支持**: 支持 B站、YouTube、抖音、快手、腾讯视频、爱奇艺、优酷等主流视频平台
- 📁 **本地上传**: 支持直接上传本地视频文件进行处理
- 🎙️ **语音识别**: 基于 OpenAI Whisper 模型，支持多种精度级别和 GPU 加速
- 🤖 **AI 总结**: 集成 DeepSeek、OpenAI GPT、通义千问、智谱清言、Kimi 等多个大语言模型
- 📸 **关键帧提取**: 自动提取视频截图并嵌入到笔记中
- 📝 **结构化输出**: 生成包含摘要、核心观点、时间节点的 Markdown 格式笔记
- 🎨 **美观界面**: 现代化的 Web 前端界面，实时显示处理进度
- 💾 **一键下载**: 支持下载包含笔记和截图的完整 ZIP 包

---

## 🏗️ 项目结构

```
VideoToNotes/
├── backend/                    # 后端服务
│   ├── main.py                # FastAPI 主入口
│   ├── video_processor.py     # 视频处理器（下载、信息获取、关键帧提取）
│   ├── whisper_asr.py         # Whisper 语音识别模块
│   ├── llm_summarizer.py      # LLM 总结生成器
│   ├── requirements.txt       # Python 依赖
│   ├── .env                   # 环境配置文件
│   └── workspace/             # 临时工作目录（自动生成）
├── frontend/                  # 前端页面
│   ├── index.html             # 主页面
│   ├── style.css              # 样式文件
│   └── script.js              # 交互逻辑
└── readme.md                  # 项目说明文档
```

---

## 🚀 快速开始

### 前置要求

- **Python**: 3.9 或更高版本
- **FFmpeg**: 用于音频提取和视频处理
- **GPU (可选)**: NVIDIA 显卡 + CUDA 驱动（用于 Whisper 加速）

### 步骤 1: 安装 FFmpeg

**Windows:**
```bash
# 使用 Chocolatey 安装
choco install ffmpeg

# 或手动下载安装
# 访问 https://ffmpeg.org/download.html 下载 Windows 版本
# 解压后将 bin 目录添加到系统 PATH
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt update && sudo apt install ffmpeg
```

### 步骤 2: 克隆项目

```bash
git clone https://github.com/your-username/VideoToNotes.git
cd VideoToNotes
```

### 步骤 3: 创建虚拟环境

```bash
# 进入后端目录
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### 步骤 4: 安装依赖

```bash
pip install -r requirements.txt
```

**如需 GPU 加速（NVIDIA 显卡用户）：**
```bash
# 卸载 CPU 版本的 torch
pip uninstall torch torchaudio

# 安装 CUDA 版本的 torch（根据你的 CUDA 版本选择）
# CUDA 11.8:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118

# CUDA 12.1:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### 步骤 5: 配置环境变量

复制 `.env` 文件并编辑：

```bash
# Windows
copy .env .env.local

# macOS/Linux
cp .env .env.local
```

编辑 `.env` 文件，填入你的 API Key：

```env
# ========== AI 大模型配置（至少配置一个） ==========

# DeepSeek（推荐，性价比高）
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
DEEPSEEK_MODEL=deepseek-chat

# OpenAI GPT（可选）
# OPENAI_API_KEY=sk-your-openai-api-key

# 通义千问（可选）
# QWEN_API_KEY=sk-your-qwen-api-key

# 智谱清言（可选）
# ZHIPU_API_KEY=your-zhipu-api-key

# 月之暗面 Kimi（可选）
# MOONSHOT_API_KEY=sk-your-moonshot-api-key

# ========== B站 Cookie 配置（仅高清视频需要） ==========
BILIBILI_SESSDATA=your-sessdata
BILIBILI_BILI_JCT=your-bili-jct
BILIBILI_BUVID3=your-buvid3

# ========== 服务配置 ==========
HOST=0.0.0.0
PORT=8000
```

#### 🔑 获取 API Key

**DeepSeek（推荐）：**
1. 访问 [DeepSeek 开放平台](https://platform.deepseek.com/)
2. 注册账号并完成实名认证
3. 在"API Keys"页面创建新密钥
4. 新用户赠送免费额度，性价比极高

**OpenAI：**
1. 访问 [OpenAI Platform](https://platform.openai.com/)
2. 注册账号并绑定支付方式
3. 在"API Keys"页面创建密钥

**其他模型：**
- 通义千问：[阿里云灵积平台](https://dashscope.aliyun.com/)
- 智谱清言：[智谱 AI 开放平台](https://open.bigmodel.cn/)
- 月之暗面：[Kimi 开放平台](https://platform.moonshot.cn/)

#### 🍪 获取 B站 Cookie

1. 浏览器登录 [B站](https://www.bilibili.com/)
2. 按 `F12` 打开开发者工具
3. 切换到 **Application**（或"存储"）标签
4. 左侧选择 **Cookies** → `https://www.bilibili.com`
5. 找到以下三个值并复制：
   - `SESSDATA`: 登录凭证
   - `bili_jct`: CSRF Token
   - `buvid3`: 设备标识

---

## 🎯 使用方法

### 方式一：Web 界面（推荐）

#### 1. 启动后端服务

```bash
cd backend
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux

python main.py
```

服务将在 `http://localhost:8000` 启动。

#### 2. 打开前端页面

直接在浏览器中打开 `frontend/index.html` 文件，或者使用简单的 HTTP 服务器：

```bash
# 在项目根目录执行
cd frontend
python -m http.server 3000
```

然后访问 `http://localhost:3000`

#### 3. 使用流程

1. **配置 AI 模型**：在左侧配置中心选择 AI 模型并输入 API Key
2. **配置 B站 Cookie**（可选）：如需下载 B站高清视频，填写 Cookie 信息
3. **调整参数**：根据需要选择 Whisper 模型、截图间隔等
4. **输入视频链接或上传本地文件**：
   - 选择"视频链接"模式，粘贴视频 URL
   - 或选择"本地文件"模式，上传视频文件
5. **点击"开始总结"**：等待处理完成
6. **查看和下载**：在线预览生成的笔记，或下载完整的 ZIP 包

### 方式二：API 调用

#### 启动服务后，可以通过 API 进行调用：

**1. 上传本地视频：**
```bash
curl -X POST "http://localhost:8000/api/upload" \
  -F "file=@/path/to/video.mp4"
```

响应示例：
```json
{
  "task_id": "abc123-def456-ghi789",
  "file_path": "D:\\workspace\\abc123\\video.mp4",
  "filename": "video.mp4"
}
```

**2. 开始总结（本地文件）：**
```bash
curl -X POST "http://localhost:8000/api/summarize" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "",
    "upload_task_id": "abc123-def456-ghi789",
    "screenshot_interval": 10,
    "whisper_model": "base",
    "use_gpu": false,
    "llm_config": {
      "model_type": "deepseek",
      "api_key": "sk-your-api-key"
    }
  }'
```

**3. 开始总结（在线视频）：**
```bash
curl -X POST "http://localhost:8000/api/summarize" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://www.bilibili.com/video/BV1xx411c7mD",
    "screenshot_interval": 10,
    "whisper_model": "base",
    "use_gpu": false,
    "bilibili_cookie": {
      "sessdata": "your-sessdata",
      "bili_jct": "your-bili-jct",
      "buvid3": "your-buvid3"
    },
    "llm_config": {
      "model_type": "deepseek",
      "api_key": "sk-your-api-key"
    }
  }'
```

**4. 查询任务状态：**
```bash
curl "http://localhost:8000/api/task/{task_id}"
```

**5. 下载总结报告：**
```bash
curl -O "http://localhost:8000/api/download/{task_id}"
```

---

## ⚙️ 配置说明

### Whisper 模型选择

| 模型 | 速度 | 准确度 | 显存占用 | 适用场景 |
|------|------|--------|----------|----------|
| tiny | ⚡⚡⚡⚡⚡ | ⭐⭐ | ~1GB | 短视频 (<10分钟)，快速测试 |
| base | ⚡⚡⚡⚡ | ⭐⭐⭐ | ~1.5GB | **推荐**，平衡速度与准确度 |
| small | ⚡⚡⚡ | ⭐⭐⭐⭐ | ~2.5GB | 中等长度视频，追求准确 |
| medium | ⚡⚡ | ⭐⭐⭐⭐⭐ | ~5GB | 长视频 (>30分钟)，高准确度 |
| large | ⚡ | ⭐⭐⭐⭐⭐⭐ | ~10GB | 专业场景，最高准确度 |

### 截图间隔设置

- **5秒**：适合教程类、技术演示类视频，细节丰富
- **10秒**：**推荐**，适合大多数视频
- **15-30秒**：适合讲座、访谈类视频，减少图片数量

### GPU 加速

如果你的电脑有 NVIDIA 显卡，建议启用 GPU 加速：

1. 确保已安装 CUDA 驱动（版本 >= 11.8）
2. 安装 CUDA 版本的 PyTorch（见安装步骤）
3. 在前端勾选"启用 GPU 加速"选项

Whisper 在 GPU 上的速度提升约为 **5-10倍**。

---

## 📋 输出示例

生成的 Markdown 笔记包含以下内容：

```markdown
# 视频总结：《Python 入门教程》

## 📌 一句话摘要
本视频详细介绍了 Python 编程语言的基础知识，包括变量、数据类型、控制流和函数等核心概念。

## 🎯 核心观点
- Python 是一门简洁易读的高级编程语言，适合初学者
- 掌握基础语法后，可以快速开发各种应用
- 良好的代码规范和注释习惯至关重要

## ⏱️ 关键时间节点
| 时间点 | 内容摘要 |
|--------|----------|
| 00:00 | 课程介绍和环境搭建 |
| 05:30 | 变量和数据类型详解 |
| 15:20 | 条件语句和循环结构 |
| 28:45 | 函数的定义和使用 |

## 💡 学习要点
1. 理解 Python 的动态类型特性
2. 掌握列表推导式的使用技巧
3. 学会使用异常处理机制

## 🎬 总结评价
这是一门非常适合初学者的 Python 入门课程，讲解清晰，示例丰富。强烈推荐编程新手观看。

## 📸 视频截图预览

![截图1](./images/frame_0000_0s.jpg)

![截图2](./images/frame_0001_10s.jpg)

...
```

下载的 ZIP 包结构：
```
视频标题_summary.zip
├── 视频标题.md          # Markdown 笔记
└── images/              # 视频截图文件夹
    ├── frame_0000_0s.jpg
    ├── frame_0001_10s.jpg
    └── ...
```

---

## 🔧 常见问题

### 1. 提示 "ffmpeg 不是内部或外部命令"

**解决方案：**
- 确认已正确安装 FFmpeg
- 将 FFmpeg 的 `bin` 目录添加到系统 PATH 环境变量
- 重启命令行窗口

### 2. Whisper 模型下载失败

**解决方案：**
- 首次运行会自动下载模型，可能需要科学上网
- 可以手动下载模型并放置到缓存目录：
  ```bash
  # Windows 缓存目录
  C:\Users\你的用户名\.cache\whisper
  
  # macOS/Linux 缓存目录
  ~/.cache/whisper
  ```

### 3. B站视频下载失败或画质低

**解决方案：**
- 确保已正确配置 B站 Cookie（SESSDATA、bili_jct、buvid3）
- Cookie 会过期，如失效请重新获取
- 大会员专属视频需要大会员账号的 Cookie

### 4. API 调用失败或返回错误

**解决方案：**
- 检查 API Key 是否正确
- 确认账户余额充足
- 查看后端控制台日志获取详细错误信息
- 尝试更换其他 AI 模型

### 5. 内存不足或处理速度慢

**解决方案：**
- 使用较小的 Whisper 模型（tiny 或 base）
- 增加截图间隔，减少截图数量
- 启用 GPU 加速
- 关闭其他占用内存的程序

### 6. 中文乱码问题

**解决方案：**
- 确保系统编码为 UTF-8
- Windows 用户可以在环境变量中添加：
  ```
  PYTHONIOENCODING=utf-8
  ```

---

## 🛠️ 技术栈

### 后端
- **FastAPI**: 高性能异步 Web 框架
- **yt-dlp**: 强大的视频下载工具
- **OpenAI Whisper**: 先进的语音识别模型
- **OpenCV**: 视频帧提取和处理
- **OpenAI Python SDK**: 调用各大语言模型 API

### 前端
- **原生 HTML/CSS/JavaScript**: 轻量级无依赖
- **Marked.js**: Markdown 渲染
- **Fetch API**: 异步请求

---

## 📝 开发计划

- [ ] 支持更多视频平台
- [ ] 添加批量处理功能
- [ ] 支持自定义总结模板
- [ ] 添加视频片段剪辑功能
- [ ] 支持导出为 PDF、Word 等格式
- [ ] 添加用户系统和历史记录
- [ ] 优化长视频处理性能
- [ ] 支持多语言翻译

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 强大的视频下载工具
- [OpenAI Whisper](https://github.com/openai/whisper) - 先进的语音识别模型
- [FastAPI](https://fastapi.tiangolo.com/) - 现代化的 Web 框架
- 感谢所有开源项目的贡献者

---

## 📮 联系方式

如有问题或建议，欢迎通过以下方式联系：

- 提交 [Issue](https://github.com/your-username/VideoToNotes/issues)
- 发送邮件至：your-email@example.com

---

<div align="center">

**如果这个项目对你有帮助，请给个 ⭐ Star 支持一下！**

Made with ❤️ by Your Name

</div>
