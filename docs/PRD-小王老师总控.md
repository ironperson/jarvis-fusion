# PRD —「小王老师」总控台（贾维斯私人 AI 助理 · 对话总控）

> **文档定位**：本文件是「总控台」需求 conveyed 的唯一依据。先出方案再写码，架构与实现不得以任何理由推翻 §0。
> **默认答复供应链**：凡涉及本机事实，一律以 §0.1/§0.4 的读码与实测为准；凡涉及 DeepSeek 云端事实，一律以 §0.2 为准。
> 编写时间：2026-10-30。作者：许清楚（产品经理）。

---

## §0 不可动摇基线（后续任何环节不得推翻）

### 0.1 需求定案（经 9 轮一问一答确认）

| 维度 | 定案 |
|---|---|
| **定位** | 总控 = 用户与「小王老师」对话的窗口。用户提需求，小王老师**自己挑工具、排步骤、干完回报**。不是纯后台编排，也不是转发路由器 |
| **形态** | 桌面原生窗口，**PySide6**（避开 PyQt 的 GPL 许可风险） |
| **大脑** | `deepseek-flash` 云端主力 + 本地 Ollama `qwen3-14b` 断网兜底（双引擎自动降级） |
| **工具·四件套** | ① 采集入库（复用 pipeline）② 语义检索（MyKnowledge + bge-m3）③ 本机操控（system.py）④ 文件/报告读写（生成本地 md/doc） |
| **取数路线** | B站公开搜索接口 + 网页搜索为**主力**；抖音保持本地视频文件模式，**站内搜索预留不启用**（规避反爬与封号） |
| **语音** | 本版：文字输入框 + edge-tts 语音播报（只需扬声器）；ASR 收音仅做接口预留。**用户尚未购买麦克风** |
| **记忆** | 双层：当次会话上下文 + 跨会话长期记忆（本地 SQLite，存偏好/常用路径/历史任务/重要结论）；有价值的产出**自动沉淀进 MyKnowledge**，可被后续语义检索命中 |
| **安全** | 读写放行；删除/移动/重命名现有文件、结束进程、关机等破坏性操作**必须弹窗确认**并列出具体路径；提供「信任模式」开关；涉及 Desktop/Documents 等个人目录时额外提示 |
| **API Key** | 用户自备，**不会贴进对话**。必须支持 `~/.jarvis/.env` 或环境变量读取，**代码零改动即可生效**，Key 绝不写死、绝不入库 |

**首个验收用例（用户亲口举的）**：「帮我搜集一下抖音播放量最高的 AI 有关的视频」
→ 本版实际路径：**B站/网页搜索 AI 相关高播放视频 → 采集 → 整理 → 入库 MyKnowledge → 在窗口里回报清单**。抖音本地视频转写作为已入库资产的补充路径保留。

### 0.2 已联网核实的技术事实（2026-10-29 查证，引用以此为准）

| 项 | 事实 |
|---|---|
| 模型名 | **`deepseek-flash`**（由 DeepSeek-V4.1-Flash-1017 提供服务）；`deepseek-chat` / `deepseek-reasoner` 已退役 |
| base_url | `https://api.deepseek.com`（OpenAI 兼容）；另有 Anthropic 格式 `https://api.deepseek.com/anthropic` |
| 能力 | **明确支持 Tool Calls（Function Calling）**、JSON Output、Responses API、Vision |
| 上下文 / 输出 | 1M 上下文，最大输出 384K |
| **思考模式** | **默认开启**，响应带 `reasoning_content` 字段 —— **多轮工具调用时若处理不当会报错**（关键约束，见 §5.3） |
| 价格（RMB / 百万 token） | 输入缓存命中 闲 0.02 / 峰 0.04；输入缓存未命中 闲 1 / 峰 2；输出 闲 4 / 峰 8 |
| 高峰时段（北京时间） | **9:00–13:00 与 14:00–18:00，工作日** |
| 并发 | flash 2500 |

### 0.3 已知硬约束（历史踩坑，逐条须有对策，见 §8）

1. **尚无物理麦克风**（麦克风通道 peak 恒为 0，仅立体声混音有信号）→ 语音输入只做接口预留。
2. **沙箱网络受限**：出口有 MITM 代理，curl 能通但 urllib/requests 默认 `CERTIFICATE_VERIFY_FAILED`；GitHub Releases 下不动；playwright chromium 二进制下载被拦 → **联网实跑只能在用户本机进行**。
3. **pip 需 `--timeout 15 --no-cache-dir`**，默认源常缺包，用阿里云镜像 `https://mirrors.aliyun.com/pypi/simple`。
4. **中文路径会让 sherpa-onnx C++ 层直接崩溃（exit 127）** → 加载模型前 `os.chdir(模型目录)`，只传文件名。
5. **PySide6 尚未安装**；sherpa-onnx 锁 **1.12.40**，**不得升级 1.13.x**（有唤醒缺陷）。

### 0.4 本次编写 PRD 时实测到的新增事实（读码 + 跑命令所得，非推测）

> 这四条是本次新增核对结果，直接影响开工顺序，实现前请再看一眼。

| # | 实测结论 | 证据 |
|---|---|---|
| A | **`.venv` 当前装不下 pipeline**：实测 `pip list` 仅 32 个包，**PyYAML / trafilatura / chromadb / fastapi / uvicorn / openai / playwright / faster-whisper / cv2 / PySide6 全部缺失**；`core/pipeline.py` 第一行就 `import yaml`，**今天直接跑会 ImportError** | `.venv/Scripts/python.exe -m pip list` + 逐模块 import 探测（2026-10-30） |
| B | **`jarvis/voice/` 目录是空的**（已测得 SuccessVoice 模型权重在 `models/asr/sensevoice/`，代码不在此工作区）。本版不用它，但"语音已实现"不能当作既有资产继承 | `ls -la jarvis/voice/` 返回空目录 |
| C | **Ollama 在线且模型齐**：`http://localhost:11434` HTTP 200，已载 `bge-m3:latest`、`qwen3-14b:latest`、`qwen3.8-27b-ud-q3_k_xl:latest` | `/api/tags` 实测 |
| D | **MyKnowledge 现状**：`D:\Desktop\MyKnowledge\pipeline.db` 现有 **notes 6 条 / tasks 3 条**；目录内有 `.obsidian/` 与 `Clippings/` —— **它已被当作 Obsidian 库打开**，入库的 Markdown+YAML frontmatter 可在 Obsidian 直接看 | `ls` + `sqlite3` 计数 |

**结论**：开工第 0 步必须是「依赖重装修复」（见 §3 P0-0），否则连 `import core.pipeline` 都过不去。

---

## §1 项目信息与产品定义

| 项 | 内容 |
|---|---|
| Language | 中文（简体） |
| Project Name | `jarvis_console`（包/模块一律 snake_case） |
| Programming Language / UI | **Python 3.12 + PySide6**（用户已指定，**覆盖默认的 Vite+React+MUI 前端栈**）；依赖基座为 `requests`（已装），如需 OpenAI SDK 须新增 |
| 运行环境 | Win11 22631 / Ryzen5 7500F / RTX 5060Ti 16GB / 32GB RAM，`.venv` Python 3.12.10 |
| 关联资产 | `pipeline/`（采集入库）、`D:\Desktop\MyKnowledge`（知识库）、`jarvis/skills/system.py`（本机技能）、Ollama 本地模型 |

### 1.1 一句话定位
> **「小王老师」是你用大白话下令、它自己挑工具动手干完并当面汇报的桌面工作台。**

### 1.2 产品目标（3 条，正交）
1. **一句话到一件事办成**：从一句自然语言到"工具链执行完毕 + 结构化回报"，中间不需要用户指定任何函数名、参数或步骤。
2. **过程全透明**：用户随时看得到小王老师正在调哪个工具、传了什么参数、耗时多久、卡在哪一步；不是黑盒 spinner。
3. **干完能沉淀**：有价值的产出自动写进 `MyKnowledge`，下次能被语义检索命中；跨会话记住偏好与常用路径。

### 1.3 明确不在本版范围（不许诺）
- ❌ **语音输入**（无物理麦克风）— 只留 ASR 接口位。
- ❌ **抖音站内搜索 / 分享链接抓取**（反爬 + 封号风险）— 保持本地视频文件模式。
- ❌ 微信相关任何能力（wxauto 已归档，微信 4.x 自绘后 UIAutomation 失效）。
- ❌ 手机端 / 跨设备 / 公网暴露。
- ❌ 自主 7×24 后台编排（本版是**人在窗口前下令**的交互模式，不接管定时自唤醒；定时任务仍归 `pipeline/run.py`）。

---

## §2 用户故事（四条主线 + 验收用例）

> 每条格式：**触发语句 → 小王老师的执行链 → 可见结果**

### US-1 ★首个验收用例★ 找 AI 视频并入库
- **触发**：「帮我搜集一下抖音播放量最高的 AI 有关的视频」
- **执行链**：
  `bilibili_search(keyword="AI", order="播放量", limit=10)` → 取前 N 条详情 `fetch_page(url)` → 用 `deepseek-flash` 整理成清单（标题/UP主/播放量/链接/一句话看点）→ `write_report(~/Desktop/AI短视频清单.md)` → `remember(任务摘要)` → `speak(播报结论)`
- **可见结果**：对话区给出**表格清单**；工具过程面板逐步骤打勾；同时落一份 md 到桌面；知识库条目数 +N；语音播报一句总结。
- **诚实口径**：小王老师须先说明「抖音站内搜索本版没开，我用 B站 + 网页搜」——**不得假装是从抖音抓的**。

### US-2 语义检索问答
- **触发**：「我之前存过的关于知识管理的内容，帮我理一下」
- **执行链**：`knowledge_search("知识管理 组织方法", top_k=5)` → 读取命中的 `raw_path` 原文 → LLM 综合成回答并**标注引用来源 note_id** → 可选 `write_report` 存为笔记
- **可见结果**：回答末尾列出引用条目（标题 + 相似度 + 原文路径，可直接点开）。

### US-3 本机操控
- **触发**：「帮我看下电脑现在卡不卡，看看都开着啥」/「帮我把飞书关了」
- **执行链（只读）**：`system_status()` + `list_processes()` → 用中文讲人话（不 dump 原始数据）
- **执行链（写）**：`close_app("飞书")` → **命中破坏性门禁 → 弹确认框**（列出将被终止的进程名/PID）→ 用户确认后执行
- **可见结果**：只读类直接给结论；破坏性类必须先看到带具体目标的确认弹窗。

### US-4 文件 / 报告读写
- **触发**：「把刚才那份清单整理成一篇文章，存到桌面」
- **执行链**：`write_file(path, content)` → 成功回报路径 + 行数 + 「用 Obsidian 打开」按钮
- **触发（删除）**：「把我桌面那堆临时文件删了」→ `list_dir` 先列候选 → **弹窗逐条列出路径** → 确认后 `delete_file`
- **可见结果**：所有写/删操作的**完整绝对路径**必须出现在对话或弹窗里，不允许只说"已删除"。

### US-5 跨会话记忆（贯穿以上四条）
- **触发**：隔天重开窗口：「老规矩，再看一遍昨天那批 AI 视频」
- **执行链**：`recall("AI视频")` 命中昨天任务摘要 → 复用上次关键词重跑 → 回报时说"这是接着上次那批"
- **可见结果**：右上角「记忆」指示灯点亮，鼠标悬停显示本次用了哪几条长期记忆。

---

## §3 需求池（P0 必须 / P1 应该有 / P2 未来）

> 【复用】= 已有代码可直接调；【薄封装】= 已有函数，只需包一层工具 schema；【新写】= 从零实现。

### P0-0 环境与工程骨架（阻塞项，最先做）
| ID | 需求 | 来源 |
|---|---|---|
| P0-0.1 | 修复 `.venv` 依赖：`pip install -i https://mirrors.aliyun.com/pypi/simple --timeout 15 --no-cache-dir PyYAML requests trafilatura PySide6`；chromadb 可选（已验证不必装，LocalStore 回退可用） | 【新写】§0.4-A |
| P0-0.2 | 建立 `jarvis_console/` 工程目录，`sys.path` 同时挂 `pipeline/` 与 `jarvis/`，`python -m jarvis_console` 可起窗口 | 【新写】 |
| P0-0.3 | 「不跑管线也能起窗口」：导入失败须降级为"该工具不可用"并提示缺哪个包，禁止整个窗口起不来 | 【新写】 |

### P0-1 对话主链路
| ID | 需求 | 来源 |
|---|---|---|
| P0-1.1 | PySide6 主窗口（§4 线框图） | 【新写】 |
| P0-1.2 | 接 `deepseek-flash`，**多轮 Function Calling 循环**（最多 N=8 轮，可配） | 【新写】 |
| P0-1.3 | 双引擎降级：云端连续失败/超时 → 自动切本地 `qwen3-14b`，UI 顶部徽标实时显示当前引擎 | 【薄封装】现有 `core/llm.py::summarize` 已演示降级写法 |
| P0-1.4 | Key 读取链：`os.environ` → `~/.jarvis/.env` → `<工作区>/.env`（沿用 `jarvis/config.py::get_secret` 的做法并扩展到用户主目录）；**缺 Key 时给明确指引，绝不回显 Key** | 【薄封装】`jarvis/config.py` |
| P0-1.5 | 工具在工作线程执行，**绝不阻塞 Qt 主线程**；支持「■停止」中断 | 【新写】 |

### P0-2 工具层（四件套）
| ID | 工具 | 需求 | 来源 |
|---|---|---|---|
| P0-2.1 | `knowledge_search(query, top_k=5)` | 语义检索 MyKnowledge，返回 标题/摘要/相似度/note_id/raw_path | 【复用】`core/search.py::search` 已实现且实测通过 |
| P0-2.2 | `fetch_page(url)` | 抓网页正文 | 【复用】`WebCollector(cfg)` **接受任意 cfg 字典**（已验证 `WebCollector({"seeds": []})` 可实例化）→ 直接喂临时 seeds，**不必改 sources.yaml** |
| P0-2.3 | `ingest_note(title, content, source, tags, url)` | 写一条原子笔记进 MyKnowledge（原文 md + bge-m3 向量 + SQLite） | 【薄封装】`core/models.py::Note` + `ingest/ingest.py::ingest_note` |
| P0-2.4 | `search_bilibili(keyword, order, limit)` | **B站公开搜索接口**，按播放量排序取 Top N | 【新写】现有代码无任何"搜索"能力，这是四件套之外的最大缺口 |
| P0-2.5 | `system_status()` / `list_processes()` / `find_files(pattern, root)` / `open_app(name)` / `screenshot()` | 本机只读 + 打开类 | 【薄封装】`jarvis/skills/system.py` 七函数全部可用且已实测 |
| P0-2.6 | `read_file(path)` / `write_file(path, content)` / `list_dir(path)` | 读写 md/文本；写操作回报绝对路径 | 【新写】须做路径白名单（工作区 + Desktop/Documents 等，且越界须提示） |
| P0-2.7 | `close_app(name)` / `kill_process(pid)` | 破坏性 → **强制弹窗确认**，列出进程名+PID | 【薄封装】+【新写】门禁 |
| P0-2.8 | `remember(key, value)` / `recall(query)` | 长期记忆 SQLite（偏好/常用路径/历史任务/重要结论） | 【新写】 |
| P0-2.9 | `speak(text)` | edge-tts 播报（`zh-CN-YunjianNeural` 沿用历史选型） | 【薄封装】edge_tts 7.3.1 已装 |

> ⚠️ **绕开 `enabled` 门**：`core/pipeline.py::run_source` 第 62 行会检查 `sources.yaml` 的 `enabled`，未启用直接跳过。总控台的临时需求**不走 `run_source`**，应直接 `collector.collect()` + `ingest_note()`，否则用户临时指定 URL 会静默 Collect 不到。

### P0-3 安全与透明
| ID | 需求 | 来源 |
|---|---|---|
| P0-3.1 | 破坏性操作白名单拦截：delete / move / rename / kill / 关机 / sleep → **QMessageBox 弹窗 + 列出完整路径** | 【新写】 |
| P0-3.2 | 「信任模式」开关（默认**关**），开启后仍对个人目录（Desktop/Documents/Downloads）二次提示 | 【新写】 |
| P0-3.3 | 工具调用过程面板：每一步显示 工具名 / 入参 / 耗时 / 状态；失败展开错误全文 | 【新写】 |
| P0-3.4 | 每个工具调用写本地日志（`jarvis_console/logs/`），含时间戳与参数（**Key 脱敏**） | 【新写】 |

### P1（合理延伸）
| ID | 需求 | 来源 |
|---|---|---|
| P1-1 | 通用网页关键词搜索工具（`web_search(query, limit)`）——若选定的搜索源不稳定则自动降级为"只用已入库知识回答"并如实说明 | 【新写】 |
| P1-2 | 有价值的产出**自动沉淀**：任务结束后由 LLM 判断是否需要入库，或每轮征询一次"存进知识库吗" | 【新写】（触发策略待拍板 Q2） |
| P1-3 | 对话历史持久化到 SQLite，可回溯/继续上一轮会话 | 【新写】 |
| P1-4 | `delete_file` / `move_file` / `rename_file`（仍走确认门禁） | 【新写】 |
| P1-5 | 成本估算显示：状态栏按 §0.2 峰谷价显示本轮估算花费（闲/峰标签 + 金额） | 【新写】 |
| P1-6 | 抖音本地视频入库路径（`collect douyin` 包装成工具，需 `faster-whisper` + 首次下权重 GB 级） | 【薄封装】`collectors/douyin.py` 已完成 |
| P1-7 | 复用 `pipeline/api/server.py` 看板（FastAPI 那一套）嵌入侧栏，看任务流 | 【复用】M5 已沙箱自测通过 |

### P2（未来）
- P2-1 物理麦克风到位后的 SenseVoice ASR 收音（唤醒词「贾维斯」已有且与 1.12.40 配套；注意中文路径 chdir 坑）。
- P2-2 Vision 能力：截图 Feed 给 `deepseek-flash` 做屏幕理解（官方支持图像理解）。
- P2-3 子任务并行（多工具同轮并发），任务看板 / 长任务续跑。
- P2-4 MCP 协议接入第三方工具。

---

## §4 界面设计稿（PySide6 主窗口）

**布局原则**：左侧"对话"，右侧"它在干什么"，底部"状态 + 输入"。透明度优先于酷炫。

```
┌─ 小王老师 · 总控台 ─────────────────── 🧠 DeepSeek ✓ │ 🦙 Ollama 待命 ─┐
│  [ 对话 ]  [ 工具过程 ]  [ 知识库 ]     🔊播报 ●  │ 信任模式 ○  │ 记忆 ● 6条 │
├───────────────────────────────┬────────────────────────────────────────┤
│ 对话区                         │ 工具调用过程面板 (QTreeWidget)           │
│                               │ ▶ 任务 #12 「搜集 AI 高播放视频」 [运行中]│
│ 我：帮我搜集一下抖音播放量最   │ ├─ ✓ web_search("AI 视频")        0.8s  │
│    高的 AI 有关的视频          │ ├─ ✓ search_bilibili(order=播放)  1.4s  │
│                               │ │   入参 {"keyword":"AI","limit":10}     │
│ 小王老师：                     │ │   返回 10 条 · 峰值 1.2e7 播放         │
│  说明一下：抖音站内搜索本版没  │ ├─ ⟳ fetch_page(bv1..)  2/10  抓取中…     │
│  开，我改从「B站 + 网页」找 AI │ ├─ ⏸ ingest_note ×3              等待…   │
│  相关高播放视频，正在跑：      │ ├─ ⏸ write_report(桌面清单.md)    等待…   │
│   ① 已检索到 10 条候选         │ └─ ⏸ speak(播报总结)              等待…   │
│   ② 正在抓详情页…              │                                        │
│                               │ ── 点任意行展开：入参 / 返回 / 耗时 / 错 │
│  [⏵ 查看它在干什么 → 右侧面板] │ ───────────────────────────────────────  │
│                               │ 原始输出预览（截断 2KB，可"复制全文"）   │
│                               │ {"keyword":"AI","order":"click",...}    │
├───────────────────────────────┴────────────────────────────────────────┤
│ 状态栏：⏵ 第 2/5 步 fetch_page │ tokens 12.3k │ 本轮 ≈¥0.04(闲时) │ KB 6→9 │
├────────────────────────────────────────────────────────────────────────┤
│ [ 说出你的需求…（Enter 发送 / Shift+Enter 换行）        ] [发送] [■停止] │
│                                                        [🎙 未插麦克风]  │
└────────────────────────────────────────────────────────────────────────┘
```

**组件约定**

| 区域 | 控件 | 要点 |
|---|---|---|
| 顶部徽标 | QLabel ×2 | 实时反映当前引擎；降级为本地时 Ollama 徽标变亮、DeepSeek 变灰并附 cause（tooltip） |
| 开关三件套 | QPushButton(可选中) / QCheckBox | 🔊播报、信任模式、记忆指示（悬停列出本次命中的记忆条目） |
| 对话区 | QTextEdit（只读）+ 轻量 Markdown 渲染 | 工具代码片段/JSON 折叠；长表格用等宽字体块 |
| 过程面板 | QTreeWidget | 状态图标 ✓/⟳/⏸/✗；子行展开入参与返回；失败行红色可复制堆栈 |
| 输入框 | QTextEdit + QPushButton | 🎙 按钮**置灰 + tooltip「未检测到输入设备，本版仅支持文字」**——诚实，不做假象 |
| 确认弹窗 | QMessageBox | 破坏性操作专用；正文必须逐行列出目标绝对路径/PID，**按钮文案写具体动作**（"删除这 3 个文件"）而非"确定" |

---

## §5 关键技术设计约束（给架构师，不可自由发挥）

### 5.1 工具调用循环
```
用户输入 → messages[] → POST /chat/completions (tools=[...], tool_choice=auto)
        → finish_reason=="tool_calls" → 逐个执行 → append {role:"tool", tool_call_id, content}
        → 回到模型 → 直至 finish_reason=="stop"
        → 渲染最终回答 → speak() 播报 → 记忆/沉淀
```
- 最大轮数上限（默认 8），超限必须停止并如实说明"这一步我卡住了，已完成的部分是……"。
- 工具执行必须在 `QThread`/线程池；结果通过 Signal 回主线程刷新 UI。

### 5.2 工具清单（给模型的 schema）
每个工具需：`name` / `description`（**写清什么时候该用、什么时候不该用**）/ `parameters`(JSON Schema)。Tool description 是它"会不会正确挑工具"的唯一依据，必须写人话。
建议首批暴露 10–12 个工具（对应 P0-2），**不要一次性塞 30 个**，实验表明过多会显著降低选型准确率。若后续工具变多，走 P2-3 的分组/动态装载。

### 5.3 `reasoning_content` 硬约束（来自 §0.2）
`deepseek-flash` **默认开启思考模式**，响应会携带 `reasoning_content`。实现规则：
1. **回写对话历史时，assistant 消息只保留 `content` 与 `tool_calls`，不得把 `reasoning_content` 放进 messages**（这是多轮工具调用报错的主要来源）。
2. `reasoning_content` 可以在 UI 的"思考"折叠区单独展示给用户看（加分项），但**不参与协议往返**。
3. 单元测试必须覆盖：连续 3 轮 tool call 后 messages 数组中不含任何 `reasoning_content` 字段。

### 5.4 Key 读取链
```
os.environ["DEEPSEEK_API_KEY"]
  → ~/.jarvis/.env            （用户主目录，推荐）
  → <工作区>/.env             （沿用 jarvis/config.py 既有路径，向后兼容）
```
- 加载顺序固定，**已加载即停**；读到的值**禁止写日志、禁止入库、禁止回显到 UI**（UI 只显示"已配置 ✓ / 未配置 ✗"）。
- 未提供 Key 时：窗口可正常启动，**工具层照常可用**（本机操控/检索/文件读写不依赖云端），只在需要 LLM 时提示"没找到 Key，怎么配：……"。

### 5.5 导入与路径规则
- `pipeline/` 模块内部用绝对导入（`from core.db import ...`），因此必须先 `sys.path.insert(0, pipeline根)`；**不要 chdir 到 pipeline**（会破坏相对资源解析）。
- 知识库落点固定 `D:\Desktop\MyKnowledge`（`core/db.py::DEFAULT_DB` 与 `settings.yaml` 已写死绝对路径），**总控台不得另建一套库**。
- 若未来要动 sherpa-onnx，遵守 §0.3-4 的 `os.chdir` 规则。

---

## §6 验收标准（Definition of Done）

### 6.1 通用 DoD
1. **起得来**：`.venv` 下 `python -m jarvis_console` 起窗口，无 ImportError；缺依赖时按 P0-0.3 降级而不是崩溃。
2. **Key 零改动**：把 Key 写进 `~/.jarvis/.env` 后，**不修改任何一行代码**即可生效；grep 全仓找不到 `sk-` 明文。
3. **工具真的被调**：至少 6 个工具（knowledge_search / fetch_page / ingest_note / search_bilibili / system_status 系 / write_file）在真实对话中被模型成功调用且返回正确。
4. **过程透明**：任一工具调用的过程面板显示 工具名 + 入参 + 耗时 + 状态，失败时能看到错误全文。
5. **不卡死**：执行耗时工具（如采集 10 个页面 ≥30s）时 UI 可正常滚动、可点、可「■停止」。
6. **降级可用**：断网手动测试 → 引擎徽标切到 Ollama 且还能回答（质量下降可接受，崩溃不可接受）。
7. **门禁有效**：触发删除/结束进程时 100% 弹窗且列出完整路径；信任模式关闭态下取消 → 什么都没发生。
8. **reasoning 干净**：三轮以上工具调用后，messages 中无 `reasoning_content`（单测 + 实跑双重验证）。
9. **记得住**：`remember` 写入的内容在重开窗口后能被 `recall` 命中。

### 6.2 首个演示用例走查（对着窗户手操作，逐条打勾）
> 前置：已在 `~/.jarvis/.env` 配好 Key；Ollama 已启动（已实测其上载有 bge-m3/qwen3-14b）；知识库基线 `notes = 6`（§0.4-D）。

| 步 | 操作 | 期望 |
|---|---|---|
| 1 | 启动 `python -m jarvis_console` | 窗口出现，顶部 🧠 DeepSeek ✓、🦙 Ollama 待命；🎙 按钮置灰 |
| 2 | 输入「帮我搜集一下抖音播放量最高的 AI 有关的视频」回车 | 对话区出现用户气泡；过程面板出现 任务 #1 且 ≥1 个工具进入 ⟳ |
| 3 | 观察 | 小王老师**先说明**「抖音站内搜索本版没开，改从 B站+网页」；`search_bilibili` 返回 ≥5 条候选 |
| 4 | 观察 | `fetch_page` 逐条抓取，进度可见（2/10…）；单条失败不影响整体，失败行标红 |
| 5 | 等待完成 | 对话区返回**表格清单**（标题/UP主/播放量/链接/一句话看点），按播放量降序 |
| 6 | 检查落盘 | 桌面出现 `AI短视频清单.md`；MyKnowledge 笔记数 **6 → >6**（新笔记可被 `knowledge_search` 命中） |
| 7 | 换一句话验证沉淀：输入「我刚才存过 AI 视频相关的内容吗」 | `knowledge_search` 命中刚入库的条目，回答附 note_id 与原文路径 |
| 8 | 点击 🔊 关掉播报再问一句 | 不再朗读（开关真实生效） |
| 9 | 触发一次破坏性操作：「把刚才生成的清单文件删了」 | **必须弹窗**且列出完整绝对路径；点取消 → 文件仍在；点确认 → 删除并在对话中回报路径 |
| 10 | 断网后重问一句 | 引擎徽标自动切 Ollama，仍能给出回答（哪怕质量下降） |

---

## §7 待拍板清单（6 条，请用户回答）

> 均为 §0 定案未覆盖、我无法自行决定的事项。

**Q1｜长期记忆存哪儿？**
(a) 独立 `~/.jarvis/memory.db`（干净，不被 Obsidian 看到）；
(b) 直接写进 MyKnowledge 的 `notes` 表（能被检索与复用，但会和正经笔记混在一起）。
→ 影响 §3 P0-2.8 的实现方式。

**Q2｜"自动沉淀进知识库"的触发口径？**
(a) 每条有产出的任务都自动入库（省心，但会灌入噪音笔记）；
(b) 小王老师自行判断"这条值得存"才入库；
(c) 每轮结束问一句"存进知识库吗"（最可控，但多一次打断）。

**Q3｜破坏性操作的默认态？**
(a) 默认每次弹窗确认（最安全，符合 §0 定案）；
(b) 默认开信任模式，仅在涉及 Desktop/Documents 等个人目录时才问。
（注：§0 定案写的是"必须弹窗 + 提供信任模式开关"，这里要的是**开关初始是开还是关**。）

**Q4｜通用网页搜索要不要现在做？**
B站搜索能满足首个用例。通用网页搜索若要做，需要定一个来源：① 无 Key 的搜索站 HTML 解析（免费但不稳）② 申请一个搜索 API Key（稳定但要额外花钱/注册）。要不要本版做，还是先只做 B站 + 直接给 URL 的采集？

**Q5｜抖音本地视频入库（P1-6）现在启用吗？**
需要先 `pip install faster-whisper opencv-python-headless` 且**首次转写会下载 GB 级模型权重**。本版是否现在就接，还是等窗口主链路跑通再说？

**Q6｜语音播报的范围？**
(a) 每条回复都读（长文本会吵）；
(b) 只读结论，超过 N 字（默认 200）自动不读改用"摘要版"；
(c) 只在我点了 🔊 时才读本条。

---

## §8 风险与回退

| # | 风险 | 触发信号 | 对策 / 回退 |
|---|---|---|---|
| R1 | **依赖环境名存实亡** | `import core.pipeline` 就 ImportError（§0.4-A 已实测成立） | P0-0 第一步就用阿里云镜像补齐 PyYAML/requests/trafilatura/PySide6；pip 必须带 `--timeout 15 --no-cache-dir` |
| R2 | **沙箱无法验证联网** | MITM 代理 → `CERTIFICATE_VERIFY_FAILED`；GitHub/Chromium 下不动 | 联网类工具（B站搜索/抓页）的验证**全部安排在用户本机进行**；`WebCollector` 已有 `verify_ssl` 与 `mirror_map` 兜底开关；沙箱只做离线 mock 与 UI 逻辑验证 |
| R3 | **证书/反爬** | 目标站点 403 / ProxyError / 验证码 | 每个抓取工具有超时 + 重试 + 失败入日志；失败项如实告知用户"这个源今天抓不动，我先用其余 N 条给你一份"，**绝不静默跳过** |
| R4 | **模型降级** | 云端 5xx / 超时 / 余额不足 / Key 缺失 | 双引擎自动切 Ollama `qwen3-14b`（已实测在线、已载模型）；UI 徽标实时提示；本地 qwen3-14b 支持 tools，工具链在本地仍可跑（质量下降可接受） |
| R5 | **Key 缺失或泄漏** | 用户还没配 Key / 不慎贴进对话 | Key 只从 env 读取（§5.4），缺 Key 时不阻断其它工具；文档明确"不要把 Key 发给我"；日志与 UI 一律显示"已配置/未配置"，不回显 |
| R6 | **reasoning_content 炸多轮调用** | 第 2 轮起报 400 | 按 §5.3 严格剥离，单测覆盖；保留 but 不回传 |
| R7 | **PySide6 与 sherpa-onnx 1.12.40 共存** | 装包后版本冲突 | PySide6 单独最后装、锁 `sherpa-onnx==1.12.40`（**严禁升 1.13.x**）；装完立刻跑 `import PySide6; import sherpa_onnx` 双验证 |
| R8 | **UI 主线程卡死** | 采集/ OCR 长任务期间窗口转圈 | 工具一律 QThread 执行 + Signal 回主线程；提供「■停止」 |
| R9 | **虚假能力承诺** | 用户误以为能搜抖音/能语音输入 | US-1 要求小王老师在回答开头就说明口径；输入框 🎙 置灰并 tooltip 说明。**任何未完成能力不得伪装成已完成** |
| R10 | **知识库污染** | 临时测试写进 `pipeline.db` | 沿用历史教训（`init_db` 默认参数曾绑定真实库导致污染）：所有回归测试必须用临时 DB；MyKnowledge 改动前先备份 `pipeline.db` |

---

## 附录 A：可复用资产的真实接口清单（读码所得）

| 能力 | 位置 | 签名 / 要点 |
|---|---|---|
| 语义检索 | `pipeline/core/search.py` | `search(query, top_k=5, settings=None) -> list[dict]`，每项含 `score/title/content/note_id/raw_path` |
| 入库 | `pipeline/ingest/ingest.py` | `ingest_note(note: Note, settings=None) -> Note`；原文落在 `D:/Desktop/MyKnowledge/raw/<note_id>.md`（YAML frontmatter，Obsidian 可直接看） |
| 笔记模型 | `pipeline/core/models.py` | `Note(note_id,title,content,source,url,note_type,para,tags,links,summary,attachments)`；`NoteType` / `ParaCategory` 枚举 |
| 网页采集 | `pipeline/collectors/web.py` | `WebCollector(cfg)`，`cfg` 支持 `seeds/timeout/concurrency/mirror_map/verify_ssl`；`.collect() -> list[RawItem]`（接受任意 dict，**无需改 sources.yaml**） |
| 抖音本地视频 | `pipeline/collectors/douyin.py` | `DouyinCollector(cfg)`，扫 `local_dir`；需 faster-whisper + opencv |
| 嵌入 | `pipeline/core/embedder.py` | `Embedder(base_url, model="bge-m3")`，走 Ollama `/api/embed`，实测 1024 维 |
| 看板 | `pipeline/api/server.py` | FastAPI：`/api/stats`、`/api/tasks`、`/api/items?q=`、`/api/sources`、`POST /api/trigger`、`GET /` |
| 本机技能 | `jarvis/skills/system.py` | `open_app/close_app/list_apps/find_files/system_status/screenshot/set_volume` —— 全返回中文字符串，可直接当工具返回体 |
| 密钥读取 | `jarvis/config.py` | `get_secret(key)` / `has_secret(key)`，当前读 `<工作区>/.env` → 需扩展到 `~/.jarvis/.env` |
| 命令行 | `pipeline/cli.py` | `status / search / seed / collect <src> / tasks` —— 排查问题时可直接对照 |

## 附录 B：本文档禁止的表述（防吹牛清单）
- ❌「支持语音输入」「可以直接说话」→ 本版只有文字输入 + 语音播报。
- ❌「能搜抖音」「能抓抖音站内」→ 本版只有抖音**本地视频文件**模式。
- ❌「全自动跑」→ 本版是人在窗口前下令。
- ❌ 任何未经实测的性能/准确率数字。
- ❌ 把 `jarvis/voice/` 当作已有资产继承 —— 该目录当前为空（§0.4-B）。

---

*本 PRD 是后续架构设计与编码的唯一依据。所有本机事实均来自 §0.4 的读码与实测，所有云端事实均来自 §0.2 的联网查证。*
