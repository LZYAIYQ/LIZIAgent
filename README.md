<div align="center">

# 🌰 LZAgent

### 栗子Agent — 你的 IM 私人助理

<br>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-000000?style=for-the-badge)

<br>

[快速开始](#-30秒启动) · [功能展示](#-我能做什么) · [架构设计](#-架构) · [部署指南](#-部署) · [API文档](#-api)

</div>

---

## 一句话

> 在微信/飞书里聊天，AI 帮你搜论文、查天气、做规划、记事情——**它会记住你说过的每一件事**。

---

## 我能做什么

<table>
<tr>
<td width="50%">

### 📚 学术研究
```
你: 找几篇 transformer 的最新论文
AI: [搜索 Google Scholar / arxiv]
    1. Attention Is All You Need
    2. Vision Transformer (ViT)
    ...
你: 第 3 篇帮我看具体内容
AI: [提取论文] 这篇主要讲...
```

</td>
<td width="50%">

### 🧠 长期记忆
```
你: 我老婆叫小明，生日 6.15
AI: 好，已记下。
    （3天后...）
你: 下周有什么提醒？
AI: 6月15日是小明生日，
    需要准备礼物吗？
```

</td>
</tr>
<tr>
<td>

### 🗺️ 旅行规划
```
你: 下周五去成都三天，预算 3000
AI: [查 12306 余票]
    Day1: 春熙路→太古里
    Day2: 都江堰→青城山
    Day3: 宽窄巷子→返程
```

</td>
<td>

### ⏰ 定时任务
```
你: 每天 8 点推送 AI 论文
AI: 已创建定时任务。
    （每天自动执行）
    📰 今日 arxiv 精选：
    1. GPT-5 Technical Report
```

</td>
</tr>
<tr>
<td>

### 🔧 工具扩展
```
你: 我需要查股票的工具
AI: 找到 stock-mcp，装吗？
你: 好的
AI: 已安装！查贵州茅台：1856 ↑2.3%
```

</td>
<td>

### 📄 文件处理
```
你: [发送 PDF]
    帮我看看这个 PDF
AI: [自动提取文字]
    这篇论文主要讲...
    需要总结要点吗？
```

</td>
</tr>
</table>

---

## 30秒启动

```bash
# 1. 克隆
git clone https://github.com/LZYAIYQ/LIZIAgent.git
cd LIZIAgent

# 2. 配置（只需填 LLM key）
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY

# 3. 启动
docker compose up -d --build

# 4. 验证
curl http://localhost:8020/api/health
# → {"status":"ok","app":"LZAgent"}
```

### 接入微信

```bash
docker compose run --rm weixin-login
# 扫码 → 微信里直接聊天
```

### 接入飞书

```env
# .env 中添加
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
```

```bash
docker compose restart lzagent
# 飞书里搜索机器人 → 发消息
```

> 飞书用 WebSocket 长连接，**无需公网 IP**。

---

## 架构

```
┌─────────────────────────────────────────────────────┐
│                     用户层                           │
│         微信 / 飞书 / Webhook / 任意 IM              │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                    网关层                            │
│     消息解析 · 权限校验 · 速率限制 · 会话管理        │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                   Agent 核心                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐  │
│  │ 意图识别 │→│ 记忆注入 │→│ 技能加载 │→│ LLM 调用 │  │
│  └─────────┘ └─────────┘ └─────────┘ └──────────┘  │
│                       │                             │
│  ┌────────────────────▼────────────────────────┐   │
│  │              工具执行引擎                     │   │
│  │  safe: 直接执行  │  confirm: 用户确认后执行   │   │
│  └─────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                   知识层                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 答案缓存  │  │ 文档 Wiki │  │ 知识图谱 (Neo4j) │  │
│  │ 毫秒命中  │  │ 结构化存储 │  │ 实体关系抽取     │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 内置工具

| 工具 | 说明 | 权限 |
|:---|:---|:---:|
| `web_search` | 网页搜索（DuckDuckGo / Bing） | ✅ |
| `scholar_search` | Google Scholar 学术搜索 | ✅ |
| `read_url` | 读取网页内容 | ✅ |
| `file_extract` | 提取 PDF / 文件内容 | ✅ |
| `memory_manage` | 长期记忆管理 | ✅ |
| `skill_manage` | 技能管理 | ✅ |
| `knowledge_ingest` | 知识沉淀 | 🔒 |
| `cron_manage` | 定时任务 | 🔒 |
| `mcp_manage` | MCP 工具管理 | 🔒 |
| `code_execution` | 代码执行（沙箱） | 🔒 |
| `delegate` | 委派子 agent | ✅ |

> ✅ = 安全，直接执行 · 🔒 = 需用户确认

---

## 记忆系统

LZAgent 会**主动记住**你说过的每一件重要的事：

```
用户: 我叫张三，不叫李四
     ↓
Agent: 好，已更新。（直接修改，不反问）

用户: 以后回复简短一点
     ↓
Agent: 记下了。（写入偏好，自动遵循）

用户: 项目截止日 6 月 30
     ↓
Agent: 已记录。（异步抽取知识图谱）
```

**三层记忆**：

| 层级 | 内容 | 谁写 |
|:---|:---|:---|
| L1 控制论底座 | 目标、状态、偏差、反馈 | 系统（只读） |
| L2 底层逻辑 | 触发条件、判断标准、失败信号 | Agent |
| L3 用户事实 | 偏好、关系、习惯、项目 | Agent + 用户 |

---

## 知识图谱

从对话中自动抽取结构化知识：

```
[用户] --prefers--> [高铁]
[用户] --about--> [项目 X]
[项目 X] --deadline--> [6月30日]
[张三] --负责--> [项目 X]
```

- 异步抽取，不阻塞对话
- 按知识库隔离
- 支持可视化查询

---

## MCP 扩展

一行命令安装新工具：

```
你: 我需要一个查天气的工具
AI: 找到 weather-mcp，安装吗？
你: 好的
AI: 已安装！
```

支持：`npm` · `pip` · `uvx` · `git+`

---

## 部署

### Docker（推荐）

```bash
docker compose up -d --build
```

自动拉起：
- LZAgent（:8020）
- PostgreSQL + pgvector（:5432）
- Neo4j（:7474）
- Redis（:6379）

### 本地开发

```bash
pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port 8020
```

---

## API

| 端点 | 说明 |
|:---|:---|
| `GET /api/health` | 健康检查 |
| `GET /api/harness/dashboard` | 实时仪表盘 |
| `GET /api/memory` | 记忆列表 |
| `GET /api/users` | 用户管理 |
| `GET /api/sessions` | 会话管理 |
| `GET /api/graph-rag` | 知识图谱 |
| `GET /api/tools` | 工具列表 |

完整 API 文档：http://localhost:8020/docs

---

## 配置

```env
# 必填
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# 可选
LZAGENT_PORT=8020
LZAGENT_LOG_LEVEL=INFO
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
SERPAPI_API_KEY=xxx          # Google Scholar
LZAGENT_SCHOLAR_PROXY=...   # 代理地址
```

完整配置见 [`.env.example`](.env.example)

---

## 项目结构

```
LZAgent/
├── backend/
│   ├── agent/          # Agent 核心
│   ├── api/            # REST API
│   ├── gateways/       # IM 网关
│   ├── memory/         # 记忆系统
│   ├── skills/         # 技能系统
│   ├── tools/          # 工具系统
│   ├── mcp/            # MCP 扩展
│   ├── graph/          # 知识图谱
│   └── core/           # 配置
├── workspace/
│   ├── skills/         # 技能文件
│   └── knowledge/      # 知识库
├── docker-compose.yml
└── Dockerfile
```

---

## 技术栈

| 组件 | 技术 |
|:---|:---|
| Web 框架 | FastAPI |
| LLM | OpenAI 兼容（DeepSeek / Qwen / GPT） |
| 数据库 | PostgreSQL + pgvector |
| 图数据库 | Neo4j |
| 缓存 | Redis |
| 工具协议 | MCP |
| 微信接入 | iLink Bot 协议 |
| 飞书接入 | lark-oapi（WebSocket） |

---

## 适用场景

**适合**：
- 个人 AI 助理
- 学术研究（论文搜索、知识管理）
- 日常任务（天气、新闻、提醒）
- 项目管理（记忆截止日、跟进进度）
- 旅行规划

**不适合**：
- 高频交易系统
- 多人协作平台
- 生产环境核心业务

---

## License

[MIT](LICENSE)

---

## 致谢

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — 记忆系统设计
- [OpenClaw](https://github.com/steipete/openclaw) — 工具权限模型
- [llm-wiki](https://github.com/nvk/llm-wiki) — 知识沉淀机制

---

<div align="center">

**🌰 LZAgent** — 让 AI 成为你的私人助理

</div>
