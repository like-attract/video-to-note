# 🎬 VideoToNotes - Multi-Platform Video to Structured Notes

<div align="center">

**Convert videos from local files, YouTube, Bilibili, and more into structured notes with one click**

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Supports Bilibili · YouTube · Douyin · Kuaishou · Tencent Video · iQiyi · Youku · Local Videos

</div>

---

## ✨ Features

- 🌐 **Multi-Platform Support**: Supports major video platforms including Bilibili, YouTube, Douyin, Kuaishou, Tencent Video, iQiyi, Youku, and more
- 📁 **Local Upload**: Directly upload and process local video files
- 🎙️ **Speech Recognition**: Powered by OpenAI Whisper with multiple accuracy levels and GPU acceleration
- 🤖 **AI Summarization**: Integrated with DeepSeek, OpenAI GPT, Qwen, GLM, Kimi, and other LLMs
- 📸 **Key Frame Extraction**: Automatically extracts video screenshots and embeds them in notes
- 📝 **Structured Output**: Generates Markdown notes with summaries, key points, and timestamps
- 🎨 **Modern UI**: Beautiful web interface with real-time progress tracking
- 💾 **One-Click Download**: Download complete ZIP packages containing notes and screenshots

---

## 🏗️ Project Structure

```
VideoToNotes/
├── backend/                    # Backend service
│   ├── main.py                # FastAPI main entry point
│   ├── video_processor.py     # Video processor (download, info extraction, frame extraction)
│   ├── whisper_asr.py         # Whisper speech recognition module
│   ├── llm_summarizer.py      # LLM summarization generator
│   ├── requirements.txt       # Python dependencies
│   ├── .env                   # Environment configuration
│   └── workspace/             # Temporary working directory (auto-generated)
├── frontend/                  # Frontend pages
│   ├── index.html             # Main page
│   ├── style.css              # Stylesheet
│   └── script.js              # Interaction logic
└── README.md                  # Project documentation
```

---

## 🚀 Quick Start

### Prerequisites

- **Python**: 3.9 or higher
- **FFmpeg**: For audio extraction and video processing
- **GPU (Optional)**: NVIDIA GPU + CUDA drivers (for Whisper acceleration)

### Step 1: Install FFmpeg

**Windows:**
```bash
# Install using Chocolatey
choco install ffmpeg

# Or download manually
# Visit https://ffmpeg.org/download.html to download Windows version
# Extract and add the bin directory to system PATH
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt update && sudo apt install ffmpeg
```

### Step 2: Clone the Repository

```bash
git clone https://github.com/your-username/VideoToNotes.git
cd VideoToNotes
```

### Step 3: Create Virtual Environment

```bash
# Navigate to backend directory
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

**For GPU Acceleration (NVIDIA GPU users):**
```bash
# Uninstall CPU version of torch
pip uninstall torch torchaudio

# Install CUDA version of torch (choose based on your CUDA version)
# CUDA 11.8:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118

# CUDA 12.1:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### Step 5: Configure Environment Variables

Copy the `.env` file and edit it:

```bash
# Windows
copy .env .env.local

# macOS/Linux
cp .env .env.local
```

Edit the `.env` file and fill in your API keys:

```env
# ========== AI Model Configuration (configure at least one) ==========

# DeepSeek (Recommended, cost-effective)
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
DEEPSEEK_MODEL=deepseek-chat

# OpenAI GPT (Optional)
# OPENAI_API_KEY=sk-your-openai-api-key

# Qwen (Optional)
# QWEN_API_KEY=sk-your-qwen-api-key

# GLM (Optional)
# ZHIPU_API_KEY=your-zhipu-api-key

# Moonshot Kimi (Optional)
# MOONSHOT_API_KEY=sk-your-moonshot-api-key

# ========== Bilibili Cookie Configuration (required for HD videos) ==========
BILIBILI_SESSDATA=your-sessdata
BILIBILI_BILI_JCT=your-bili-jct
BILIBILI_BUVID3=your-buvid3

# ========== Service Configuration ==========
HOST=0.0.0.0
PORT=8000
```

#### 🔑 Getting API Keys

**DeepSeek (Recommended):**
1. Visit [DeepSeek Platform](https://platform.deepseek.com/)
2. Register an account and complete verification
3. Create a new API key in the "API Keys" section
4. New users receive free credits, highly cost-effective

**OpenAI:**
1. Visit [OpenAI Platform](https://platform.openai.com/)
2. Register an account and add payment method
3. Create an API key in the "API Keys" section

**Other Models:**
- Qwen: [Alibaba Cloud DashScope](https://dashscope.aliyun.com/)
- GLM: [Zhipu AI Open Platform](https://open.bigmodel.cn/)
- Moonshot: [Kimi Open Platform](https://platform.moonshot.cn/)

#### 🍪 Getting Bilibili Cookies

1. Log in to [Bilibili](https://www.bilibili.com/) in your browser
2. Press `F12` to open Developer Tools
3. Switch to the **Application** tab
4. Select **Cookies** → `https://www.bilibili.com` in the left panel
5. Find and copy these three values:
   - `SESSDATA`: Login credential
   - `bili_jct`: CSRF Token
   - `buvid3`: Device identifier

---

## 🎯 Usage

### Method 1: Web Interface (Recommended)

#### 1. Start the Backend Service

```bash
cd backend
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux

python main.py
```

The service will start at `http://localhost:8000`.

#### 2. Open the Frontend Page

Open `frontend/index.html` directly in your browser, or use a simple HTTP server:

```bash
# Execute from project root
cd frontend
python -m http.server 3000
```

Then visit `http://localhost:3000`

#### 3. Usage Flow

1. **Configure AI Model**: Select an AI model in the left sidebar and enter the API Key
2. **Configure Bilibili Cookie** (Optional): Fill in cookie information for HD Bilibili videos
3. **Adjust Parameters**: Choose Whisper model, screenshot interval, etc. as needed
4. **Enter Video URL or Upload Local File**:
   - Select "Video Link" mode and paste the video URL
   - Or select "Local File" mode and upload a video file
5. **Click "Start Summarization"**: Wait for processing to complete
6. **View and Download**: Preview the generated notes online or download the complete ZIP package

### Method 2: API Calls

#### After starting the service, you can make API calls:

**1. Upload Local Video:**
```bash
curl -X POST "http://localhost:8000/api/upload" \
  -F "file=@/path/to/video.mp4"
```

Response example:
```json
{
  "task_id": "abc123-def456-ghi789",
  "file_path": "D:\\workspace\\abc123\\video.mp4",
  "filename": "video.mp4"
}
```

**2. Start Summarization (Local File):**
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

**3. Start Summarization (Online Video):**
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

**4. Check Task Status:**
```bash
curl "http://localhost:8000/api/task/{task_id}"
```

**5. Download Summary Report:**
```bash
curl -O "http://localhost:8000/api/download/{task_id}"
```

---

## ⚙️ Configuration Guide

### Whisper Model Selection

| Model | Speed | Accuracy | VRAM Usage | Use Case |
|-------|-------|----------|------------|----------|
| tiny | ⚡⚡⚡⚡⚡ | ⭐⭐ | ~1GB | Short videos (<10 min), quick testing |
| base | ⚡⚡⚡⚡ | ⭐⭐⭐ | ~1.5GB | **Recommended**, balanced speed and accuracy |
| small | ⚡⚡⚡ | ⭐⭐⭐⭐ | ~2.5GB | Medium-length videos, higher accuracy |
| medium | ⚡⚡ | ⭐⭐⭐⭐⭐ | ~5GB | Long videos (>30 min), high accuracy |
| large | ⚡ | ⭐⭐⭐⭐⭐⭐ | ~10GB | Professional use, highest accuracy |

### Screenshot Interval Settings

- **5 seconds**: Suitable for tutorials and tech demos, rich in details
- **10 seconds**: **Recommended**, suitable for most videos
- **15-30 seconds**: Suitable for lectures and interviews, fewer images

### GPU Acceleration

If you have an NVIDIA GPU, enabling GPU acceleration is recommended:

1. Ensure CUDA drivers are installed (version >= 11.8)
2. Install CUDA version of PyTorch (see installation steps)
3. Check "Enable GPU Acceleration" in the frontend

Whisper runs approximately **5-10x faster** on GPU.

---

## 📋 Output Example

The generated Markdown note includes:

```markdown
# Video Summary: "Python Beginner Tutorial"

## 📌 One-Sentence Summary
This video provides a detailed introduction to Python programming basics, covering core concepts such as variables, data types, control flow, and functions.

## 🎯 Key Points
- Python is a concise and readable high-level programming language, ideal for beginners
- Master basic syntax to quickly develop various applications
- Good coding standards and commenting habits are essential

## ⏱️ Key Timestamps
| Time | Content Summary |
|------|----------------|
| 00:00 | Course introduction and environment setup |
| 05:30 | Detailed explanation of variables and data types |
| 15:20 | Conditional statements and loop structures |
| 28:45 | Function definition and usage |

## 💡 Learning Points
1. Understand Python's dynamic typing characteristics
2. Master list comprehension techniques
3. Learn to use exception handling mechanisms

## 🎬 Overall Evaluation
This is an excellent Python introductory course for beginners, with clear explanations and abundant examples. Highly recommended for programming novices.

## 📸 Video Screenshots

![Screenshot 1](./images/frame_0000_0s.jpg)

![Screenshot 2](./images/frame_0001_10s.jpg)

...
```

Downloaded ZIP package structure:
```
Video_Title_summary.zip
├── Video_Title.md          # Markdown notes
└── images/                 # Video screenshots folder
    ├── frame_0000_0s.jpg
    ├── frame_0001_10s.jpg
    └── ...
```

---

## 🔧 Troubleshooting

### 1. Error: "ffmpeg is not recognized as an internal or external command"

**Solution:**
- Verify FFmpeg is correctly installed
- Add FFmpeg's `bin` directory to system PATH environment variable
- Restart the command line window

### 2. Whisper Model Download Failed

**Solution:**
- The model downloads automatically on first run; you may need a VPN
- Manually download models and place them in the cache directory:
  ```bash
  # Windows cache directory
  C:\Users\YourUsername\.cache\whisper
  
  # macOS/Linux cache directory
  ~/.cache/whisper
  ```

### 3. Bilibili Video Download Failed or Low Quality

**Solution:**
- Ensure Bilibili cookies are correctly configured (SESSDATA, bili_jct, buvid3)
- Cookies expire; re-obtain if invalid
- Premium member-exclusive videos require premium account cookies

### 4. API Call Failed or Returns Errors

**Solution:**
- Verify API Key is correct
- Confirm sufficient account balance
- Check backend console logs for detailed error messages
- Try switching to a different AI model

### 5. Insufficient Memory or Slow Processing

**Solution:**
- Use smaller Whisper models (tiny or base)
- Increase screenshot interval to reduce image count
- Enable GPU acceleration
- Close other memory-intensive programs

### 6. Character Encoding Issues

**Solution:**
- Ensure system encoding is UTF-8
- Windows users can add to environment variables:
  ```
  PYTHONIOENCODING=utf-8
  ```

---

## 🛠️ Tech Stack

### Backend
- **FastAPI**: High-performance async web framework
- **yt-dlp**: Powerful video downloader
- **OpenAI Whisper**: Advanced speech recognition model
- **OpenCV**: Video frame extraction and processing
- **OpenAI Python SDK**: Calling various LLM APIs

### Frontend
- **Native HTML/CSS/JavaScript**: Lightweight, no dependencies
- **Marked.js**: Markdown rendering
- **Fetch API**: Async requests

---

## 📝 Roadmap

- [ ] Support more video platforms
- [ ] Add batch processing functionality
- [ ] Support custom summary templates
- [ ] Add video clip editing features
- [ ] Support export to PDF, Word, and other formats
- [ ] Add user system and history records
- [ ] Optimize long video processing performance
- [ ] Support multi-language translation

---

## 🤝 Contributing

Issues and Pull Requests are welcome!

1. Fork this repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details

---

## 🙏 Acknowledgments

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Powerful video downloader
- [OpenAI Whisper](https://github.com/openai/whisper) - Advanced speech recognition model
- [FastAPI](https://fastapi.tiangolo.com/) - Modern web framework
- Thanks to all open-source project contributors

---

## 📮 Contact

For questions or suggestions, feel free to reach out:

- Submit an [Issue](https://github.com/your-username/VideoToNotes/issues)
- Email: your-email@example.com

---

<div align="center">

**If this project helps you, please give it a ⭐ Star!**

Made with ❤️ by Your Name

</div>
