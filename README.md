<div align="center">

# 🌰 LZAgent

### 栗子Agent — 你的 AI 私人助理 + 桌面宠物

<br>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-22+-47848F?style=for-the-badge&logo=electron&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-000000?style=for-the-badge)

<br>

[快速开始](#-快速开始) · [功能](#-功能) · [桌面宠物](#-桌面宠物) · [架构](#-架构) · [TODO](#-todo)

</div>

---

## 项目简介

**LZAgent** 是一个全栈 AI 助理系统，包含：

1. **🧠 AI 后端** — 基于 LLM 的智能对话引擎，支持长期记忆、知识图谱、技能系统
2. **🌰 桌面宠物** — Electron 桌面宠物，可拖拽、可对话、可互动

在微信/飞书里聊天，AI 帮你搜论文、查天气、做规划、记事情——**它会记住你说过的每一件事**。

---


## 功能

### 🤖 AI 助理能力

<table>
<tr>
<td width="50%">

**📚 学术研究**
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

**🧠 长期记忆**
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

**🗺️ 旅行规划**
```
你: 下周五去成都三天，预算 3000
AI: [查 12306 余票]
    Day1: 春熙路→太古里
    Day2: 都江堰→青城山
    Day3: 宽窄巷子→返程
```

</td>
<td>

**⏰ 定时任务**
```
你: 每天 8 点推送 AI 论文
AI: 已创建定时任务。
    （每天自动执行）
    📰 今日 arxiv 精选：
    1. GPT-5 Technical Report
```

</td>
</tr>
</table>

### 🌰 桌面宠物

- **可爱形象**：CSS 绘制的栗子宠物，支持自定义精灵图
- **拖拽移动**：左键拖拽宠物到任意位置
- **点击对话**：左键点击打开聊天面板
- **右键菜单**：对话、隐藏、退出
- **系统托盘**：最小化到托盘，双击恢复
- **状态动画**：闲置、思考、拖拽、对话四种状态

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/LZYAIYQ/LIZIAgent.git
cd LIZIAgent
```

### 2. 启动 AI 后端

```bash
# 配置
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY

# Docker 启动
docker compose up -d --build

# 验证
curl http://localhost:8020/api/health
```

### 3. 启动桌面宠物

```bash
cd desktop-pet2
npm install
npm start
```

或直接双击 `start.bat`。

### 4. 接入 IM

**微信**：
```bash
docker compose run --rm weixin-login
# 扫码 → 微信里直接聊天
```

**飞书**：
```env
# .env 中添加
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
```

```bash
docker compose restart lzagent
```

---

## 桌面宠物

### 界面

```
┌─────────────────────┐
│      💭 气泡        │  ← 思考时显示
│   "嗯，在想..."     │
├─────────────────────┤
│                     │
│    🌰 栗子宠物      │  ← CSS 绘制 / 自定义精灵
│                     │
├─────────────────────┤
│   👀 眼睛会动       │
│   😊 有表情         │
└─────────────────────┘
```

### 交互方式

| 操作 | 效果 |
|:---|:---|
| 鼠标悬停 | 宠物进入思考状态 |
| 左键点击 | 打开聊天面板 |
| 左键拖拽 | 移动宠物位置 |
| 右键菜单 | 对话 / 隐藏 / 退出 |
| 双击托盘图标 | 恢复显示 |

### 自定义精灵

编辑 `renderer/assets/sprites/config.json`：

```json
{
  "useSprite": true,
  "states": {
    "idle": { "image": "idle.png" },
    "thinking": { "image": "thinking.png" },
    "dragging": { "image": "dragging.png" },
    "chatting": { "image": "chatting.png" }
  }
}
```

将精灵图放入 `renderer/assets/sprites/` 目录。

---

## 架构

```
┌─────────────────────────────────────────────────────┐
│                     用户层                           │
│    微信 / 飞书 / 桌面宠物 / Webhook / 任意 IM        │
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
# AI 后端
pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port 8020

# 桌面宠物
cd desktop-pet2
npm install
npm start
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
LIZIAgent/
├── backend/                # AI 后端
│   ├── agent/              # Agent 核心
│   ├── api/                # REST API
│   ├── gateways/           # IM 网关
│   ├── memory/             # 记忆系统
│   ├── skills/             # 技能系统
│   ├── tools/              # 工具系统
│   ├── mcp/                # MCP 扩展
│   ├── graph/              # 知识图谱
│   └── core/               # 配置
├── desktop-pet2/           # 桌面宠物
│   ├── main.js             # Electron 主进程
│   ├── preload.js          # 预加载脚本
│   ├── renderer/           # 渲染进程
│   │   ├── index.html      # 界面
│   │   ├── app.js          # 交互逻辑
│   │   ├── style.css       # 样式
│   │   └── assets/         # 资源文件
│   └── package.json
├── workspace/
│   ├── skills/             # 技能文件
│   └── knowledge/          # 知识库
├── docker-compose.yml
└── Dockerfile
```

---

## 技术栈

| 组件 | 技术 |
|:---|:---|
| AI 后端 | FastAPI + PostgreSQL + Neo4j + Redis |
| LLM | OpenAI 兼容（DeepSeek / Qwen / GPT） |
| 桌面宠物 | Electron + HTML/CSS/JS |
| 工具协议 | MCP |
| 微信接入 | iLink Bot 协议 |
| 飞书接入 | lark-oapi（WebSocket） |

---

## TODO

- [ ] 🎨 桌面宠物支持更多精灵动画
- [ ] 🎤 语音输入支持（Whisper 集成）
- [ ] 📱 移动端 App（React Native）
- [ ] 🔌 更多 MCP 工具集成
- [ ] 🌍 多语言支持（英文、日文）
- [ ] 📊 数据可视化仪表盘
- [ ] 🔐 OAuth 登录支持
- [ ] 📦 插件市场
- [ ] 🤖 多 Agent 协作
- [ ] 📝 文档自动生成
- [ ] 🧪 单元测试覆盖率提升
- [ ] 🚀 CI/CD 自动化部署

---

## 适用场景

**适合**：
- 个人 AI 助理
- 学术研究（论文搜索、知识管理）
- 日常任务（天气、新闻、提醒）
- 项目管理（记忆截止日、跟进进度）
- 旅行规划
- 桌面互动娱乐

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
- [Electron](https://www.electronjs.org/) — 桌面应用框架

---

<div align="center">

**🌰 LZAgent** — 让 AI 成为你的私人助理

[![GitHub stars](https://img.shields.io/github/stars/LZYAIYQ/LIZIAgent?style=social)](https://github.com/LZYAIYQ/LIZIAgent/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/LZYAIYQ/LIZIAgent?style=social)](https://github.com/LZYAIYQ/LIZIAgent/network/members)

</div>

## Star 历史

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=LZYAIYQ/LIZIAgent&type=Date)](https://star-history.com/#LZYAIYQ/LIZIAgent&Date)

</div>

---
