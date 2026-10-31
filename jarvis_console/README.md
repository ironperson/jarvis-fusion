# 小王老师 · 总控台

Windows 桌面 AI Agent。用户用自然语言下单，总控台把 LLM、12 个工具、长期记忆、知识库沉淀、破坏性确认、过程追踪和语音播报串成完整闭环。

## 能力边界

- 支持文字输入与 `edge-tts` 语音播报；本版**没有语音输入**。
- 支持 B站公开视频搜索和通用网页搜索；本版**没有抖音站内搜索**。用户提抖音时会明确改用“B站 + 网页搜索”。
- `close_app`、`delete_file` 必须经过确认门；信任模式不会绕过写入路径白名单。
- 长期记忆存 `~/.jarvis/memory.db`，与 `D:\Desktop\MyKnowledge\pipeline.db` 物理隔离。

## 安装

在仓库根 `D:/workbuddy worktool/贾维斯` 使用项目

`.venv`，按顺序执行：

```bash
.venv/Scripts/python.exe -m pip install -i https://mirrors.aliyun.com/pypi/simple --timeout 15 --no-cache-dir PyYAML trafilatura pytest
.venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'pipeline');import core.pipeline,core.search,ingest.ingest,collectors.web;print('pipeline OK')"
.venv/Scripts/python.exe -m pip install -i https://mirrors.aliyun.com/pypi/simple --timeout 15 --no-cache-dir PySide6
.venv/Scripts/python.exe -c "import PySide6, sherpa_onnx; print('Qt', PySide6.__version__, '| sherpa', sherpa_onnx.__version__)"
```

`sherpa-onnx` 考虑，必须保持 `1.12.40`，禁止升级到 1.13.x。本版不安装 `faster-whisper`，不下载 ASR 权重。`edge-tts 7.3.1` 已是既有依赖，不需要重装。

## 配置 DeepSeek Key

不要把 Key 发到对话里，也不要写进仓库。新建：

```text
C:\Users\<你的用户名>\.jarvis\.env
```

内容：

```dotenv
DEEPSEEK_API_KEY=你的Key
```

读取顺序固定为：系统环境变量 → `~/.jarvis/.env` → 仓库根 `.env`。UI 和日志只显示“是否配置”，不会回显值。当前模型为 `deepseek-flash`，Base URL 为 `https://api.deepseek.com`；云端不可用时自动尝试本地 Ollama `qwen3-14b`。

## 启动

```bash
.venv/Scripts/python.exe -m jarvis_console
```

无 Key 时窗口仍可启动，本机工具和 Ollama 兜底仍可用。缺 PySide6 时入口会打印明确安装命令而不会崩出长 traceback。

## 完全离线 Mock 自测

Mock 模式不会访问网络，也不会写真实 MyKnowledge；数据落到 `~/.jarvis/mock_workspace`。Git Bash：

```bash
JARVIS_MOCK=1 .venv/Scripts/python.exe -m pytest jarvis_console/tests -q --basetemp=$(mktemp -d)
JARVIS_MOCK=1 .venv/Scripts/python.exe -m jarvis_console
```

`--basetemp` 请给**每次全新**的系统临时目录；不要复用仓库内的旧目录（pytest 9 会清理历史 basetemp，在受限环境下可能被拦截而卡住收尾）。

Windows PowerShell：

```powershell
$env:JARVIS_MOCK='1'
.venv\Scripts\python.exe -m pytest jarvis_console\tests -q
.venv\Scripts\python.exe -m jarvis_console
```

Mock 脚本覆盖 `search_bilibili → web_search → fetch_page×2 → write_file → ingest_note → stop`，可验证多轮 Tool Calls 和 `reasoning_content` 不污染下一轮消息。

## 写入安全

写入/覆盖/删除只允许：

1. `D:\Desktop\MyKnowledge`；
2. Windows Shell API 动态探测到的真实系统桌面；
3. `jarvis_console` 自身目录；
4. 用户点击“授权目录”并手动选中的路径。

白名单外请求一律返回明确错误，信任模式也不放行。`read_file` 会拒绝读取凭据/私钥类文件，`find_files` 列举与模糊搜索时也会隐藏它们——避免 Key 被读进上下文后随请求发往云端；是否配置 Key 只看 UI 的“已配置/未配置”指示。

拒绝清单在 `config/console.yaml` 的 `safety.read_denylist` 中配置（置空即放开，但不建议）。默认覆盖：`.env` / `.env.*`、`*.key` / `*.pem` / `*.p12` / `*.pfx` / `*.keystore` / `*.jks`、`id_rsa` / `id_ed25519` / `id_dsa` / `id_ecdsa`（含 `.pub`）、`.npmrc` / `.pypirc` / `.netrc` / `.htpasswd` / `.git-credentials`、`credentials` / `secrets.json` / `token.json` / `*serviceaccount*.json`，以及目录规则 `~/.ssh/`（全目录拒绝）与 `~/.jarvis/`（仅拒绝其中的凭据类文件名，不误伤 `memory.db`）。  
**注意**：`~/.ssh` 是不可关闭的安全底线——即使把 `read_denylist` 置空，`~/.ssh` 下所有文件照样拒绝；清单置空只能放开其余条目。知识库 md、桌面文档、CSV/YAML 等正常文件照常可读。知识库每日首写前自动备份到 `_backup/pipeline-YYYYMMDD-HHMMSS.db`，保留最近 7 份。

## 用户本机网络功能走查清单

沙箱有 MITM 代理，以下项目必须在用户自己的 Windows 环境勾选并记录真实结果。

### 网络专项

- [ ] 输入“搜索 B站 AI Agent 视频，按播放量排序”，确认 `search_bilibili` 返回标题、UP主、数值播放量、BV 号和链接。
- [ ] 给出一个普通文章 URL，确认 `fetch_page` 返回标题与正文；过程面板显示入参、耗时、结果。
- [ ] 输入通用网页问题，确认 DuckDuckGo 成功；若其失败，确认自动尝试 Bing 且来源标注正确。
- [ ] 断开网络再提问，确认徽标切到 Ollama，回答不崩溃；网络搜索失败必须如实说明。
- [ ] 临时让 B站接口不可达，确认工具返回 `ok:false` 并提示改走 `web_search`，不伪造 B站数据。

### DoD 十步演示

- [ ] 启动 `python -m jarvis_console`：窗口出现，DeepSeek/Ollama 徽标可见，语音输入按钮置灰。
- [ ] 输入“帮我搜集一下抖音播放量最高的 AI 有关的视频”：过程面板出现运行中工具。
- [ ] 首段口径明确写“抖音站内搜索本版没开，我用 B站 + 网页搜索”。
- [ ] `search_bilibili` 至少返回 5 条候选；`fetch_page` 单条失败不影响其余项。
- [ ] 最终回答给出标题、UP主、播放量、链接、看点，并按播放量降序。
- [ ] 桌面出现完整 Markdown 清单；知识库新增条目，UI 显示“已沉淀”与“撤回”。
- [ ] 追问“我刚才存过 AI 视频相关内容吗”，`knowledge_search` 命中新条目并列 note_id/raw_path。
- [ ] 关闭“播报”再提问，确认不再调用 TTS；开启后长文只播不超过 80 字摘要，全文仍显示。
- [ ] 请求删除刚生成清单：弹窗列完整绝对路径；先取消确认文件仍在，再确认删除。
- [ ] 断网重问：自动切 Ollama；恢复网络后重新启动一轮验证 DeepSeek。

## 排错

- `pipeline` 导入失败：先安装 PyYAML，再运行安装章节的 pipeline import 验证。
- DeepSeek 不可用：确认 `.env` 文件位置和变量名，不要在日志中粘贴 Key；检查后发送下一条消息。
- Ollama 不可用：运行 `ollama list`，确认有 `qwen3-14b` 与 `bge-m3`。
- B站 412/网页验证码：这是远端反爬；工具会返回失败并降级，不能把降级数据说成 B站结果。
- TTS 失败：确认 `edge-tts==7.3.1`、Windows 可联网且 PowerShell 可播放音频。
- 未来接 ASR 模型时，必须先 `chdir` 到模型目录，只传纯文件名给 C++ 层，用完恢复工作目录，避免中文路径导致 exit 127。
