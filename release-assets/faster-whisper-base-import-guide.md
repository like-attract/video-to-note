# faster-whisper-base 手动导入说明 / Manual Import Guide

> 中文说明（English note at the bottom）



> 适用版本：v1.2.0 及以上（带「手动导入模型」按钮，位于「语音转写」设置区）
> 本说明长期有效：即便群文件或本附件过期，也随时可自行按【方式二】从镜像站下载。

---

## 什么时候需要手动导入？

选了 Whisper 模型（默认 base）但下载慢/反复失败时，可以手动把模型文件放进指定文件夹，程序会自动识别，效果和自动下载完全一样。

## 方式一：从 Release 附件 / 群文件下载（推荐，约 127 MB）

1. 下载 `faster-whisper-base.zip`（SHA256 见文末）
   - **GitHub Release 附件**：进入 VideoToNo 的 Release 页，展开“Assets / 附件”下载（若 Release 页因 GitHub 文件大小限制没有附带 ZIP，请改用方式二从镜像站直链下载，同样简单快速）；
   - 或从**QQ 群文件**下载（如有）。
2. 解压得到文件夹 `faster-whisper-base`，里面有 4 个文件：
   - `config.json`（2,309 字节）
   - `model.bin`（145,217,532 字节 ≈ 141 MB）
   - `tokenizer.json`（2,203,239 字节 ≈ 2.1 MB）
   - `vocabulary.txt`（459,861 字节 ≈ 449 KB）
3. 把 **4 个文件**（不是整个文件夹）复制到程序的手动导入目录（见下文【放到哪里】）；
4. 回到界面，几秒内下拉框会从「未缓存」变成「**已缓存**」，即可正常提交任务。

## 先别急着下大模型：中文视频建议用 belle-turbo-zh / paraformer-zh

如果你的视频是**中文**（B 站课程、口播、直播回放），**不建议**下载 `medium`（约 1.5GB）/ `large-v3`（约 3.1GB）/ `turbo`（约 1.6GB）来"提高精度"——下拉框里这三档已标注「不推荐下载」。它们在中文上都不如下面两个，只有**非中文或多语言**视频才值得装（多语言要高精度选 `large-v3`，要多语言提速选 `turbo`；`turbo` 的中文反而比 large-v3 差）。

| 推荐档 | 体积 | 适合 | 说明 |
|---|---|---|---|
| `paraformer-zh` | 约 0.5GB | **中文首选** | 只支持中文。本机实测比 `small` 快约 9.6 倍（80 分钟视频从约 41 分钟降到约 4 分钟），中文专名识别与标点明显更好 |
| `belle-turbo-zh` | 约 0.8GB | 中文要 whisper 系最高精度 | whisper-turbo 的中文微调版，精度优于 `large-v3` 而体积只有它四分之一；时间戳分段偏粗（约 30 秒一段），逐句对齐场景仍建议 `small` 或 `paraformer-zh` |

**为什么这两个不放在 Release 附件里**：随 Release 分发的 `faster-whisper-base.zip` 已经 127MB，0.8GB / 0.5GB 的模型不适合当附件；请到镜像站自取，方法和下面完全一样。

### belle-turbo-zh 手动导入（5 个文件）

它是社区转换的 CT2 int8 仓库，**不在 Systran 官方仓**，词表文件名是 `vocabulary.json`：

| 文件 | 下载链接 |
|---|---|
| config.json | https://hf-mirror.com/wolfofbackstreet/faster-whisper-belle-whisper-large-v3-turbo-zh-ct2-int8/resolve/main/config.json |
| model.bin | https://hf-mirror.com/wolfofbackstreet/faster-whisper-belle-whisper-large-v3-turbo-zh-ct2-int8/resolve/main/model.bin |
| tokenizer.json | https://hf-mirror.com/wolfofbackstreet/faster-whisper-belle-whisper-large-v3-turbo-zh-ct2-int8/resolve/main/tokenizer.json |
| vocabulary.json | https://hf-mirror.com/wolfofbackstreet/faster-whisper-belle-whisper-large-v3-turbo-zh-ct2-int8/resolve/main/vocabulary.json |
| preprocessor_config.json | https://hf-mirror.com/wolfofbackstreet/faster-whisper-belle-whisper-large-v3-turbo-zh-ct2-int8/resolve/main/preprocessor_config.json |

下拉框选中 `belle-turbo-zh` → 点「手动导入模型」→ 把 5 个文件放进打开的 `manual\belle-turbo-zh\`。

> `preprocessor_config.json` **必须带**：程序靠它决定用 128 维特征，缺了会直接报 shape 不匹配。
> `turbo` 这一档同理不在 Systran 仓（官方从未发布 turbo 的 CT2 版），要手动下就用 `https://hf-mirror.com/deepdml/faster-whisper-large-v3-turbo-ct2/resolve/main/<文件名>`，词表也是 `vocabulary.json`。

### paraformer-zh 要手动下怎么办

`paraformer-zh` 是另一套引擎（sherpa-onnx），**「手动导入模型」按钮只管 faster-whisper 系**，选它时请按正常流程让程序自动下载（约 0.5GB，走 hf-mirror，通常几分钟）。下载失败先重试或换网络。

高级用户确实要手工铺文件，程序读的是 `<程序目录>\workspace\_model_cache\sherpa\` 下这三个子目录（标点缺失会自动降级为无标点转写，不影响出稿）：

| 子目录 | 需要的文件 | 来源仓库 |
|---|---|---|
| `sherpa\asr\` | `model.int8.onnx`、`tokens.txt` | https://hf-mirror.com/csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14/tree/main |
| `sherpa\punc\` | `model.onnx` | https://hf-mirror.com/csukuangfj/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12/tree/main |
| `sherpa\vad\` | `silero_vad.onnx` | https://hf-mirror.com/csukuangfj/vad/tree/main |

## 方式二：自行从镜像站下载（附件/群文件过期时用）

用浏览器（或 IDM、迅雷等下载工具）逐个下载这 4 个直链（hf-mirror 国内镜像，无需科学上网）：

| 文件 | 下载链接 |
|---|---|
| config.json | https://hf-mirror.com/Systran/faster-whisper-base/resolve/main/config.json |
| model.bin | https://hf-mirror.com/Systran/faster-whisper-base/resolve/main/model.bin |
| tokenizer.json | https://hf-mirror.com/Systran/faster-whisper-base/resolve/main/tokenizer.json |
| vocabulary.txt | https://hf-mirror.com/Systran/faster-whisper-base/resolve/main/vocabulary.txt |

也可以打开模型主页 https://hf-mirror.com/Systran/faster-whisper-base/tree/main ，逐个点进文件后点右上角的下载图标。

> 下载工具建议：model.bin 有 141 MB，浏览器直下容易断；用下载工具更稳。
> 如果 hf-mirror 也不可用，可把链接里的 `hf-mirror.com` 换成 `huggingface.co`（可能需要科学上网）。

## 放到哪里？

**方法 A（最简单，v1.2.0+）**：界面「语音转写」设置中，先在下拉框选中 `base`，点「**手动导入模型**」按钮 → 确认 → 程序自动打开导入文件夹 → 把 4 个文件放进去即可。

**方法 B（手动找目录）**：把 4 个文件放到（没有就新建）：

```
<程序目录>\workspace\_model_cache\manual\base\
```

便携版exe用户：就是 exe 所在目录下的 `workspace\_model_cache\manual\base\`。

高级用户也可以把解压出的整个 `faster-whisper-base` 文件夹直接放进 `manual\` 目录（`manual\faster-whisper-base\` 同样能识别）。

## 常见问题

- **放进去后还是显示「未缓存」？**
  - 检查是否多套了一层文件夹（正确：`manual\base\model.bin`；错误：`manual\base\faster-whisper-base\model.bin` 或 `manual\base\手动导入-faster-whisper-base\model.bin`）；
  - 检查文件名是否被浏览器改了名（如 `model (1).bin`）；
  - 刷新页面或重新打开程序再看。
- **model.bin 大小对不上？** 对比上面列出的精确字节数；传输/解压不完整会导致文件损坏，重新下载即可。程序 v1.2.0+ 会自动校验并提示。
- **其他模型（small/medium 等）怎么手动导入？** 同样方法，把上面链接中的 `faster-whisper-base` 换成 `faster-whisper-small`、`faster-whisper-medium` 等，放入 `manual\<模型名>\` 文件夹。

## 校验信息

- `faster-whisper-base.zip`
  - 大小：133,125,869 字节（约 127 MB）
  - SHA256：`18691126EF4C22F0AF6298276BB9FF22AD54F7E006B96C6820EEEF0DA49F03AF`
- 解压后 4 个文件均与 Hugging Face `Systran/faster-whisper-base` 仓库完全一致（已逐文件校验哈希，并实测加载转写正常）。