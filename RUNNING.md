# 运行指南

如何在 Windows / Linux 上把 LZAgent 跑起来，以及如何同时观察各路日志。

---

## 0. 前置依赖

| 依赖 | 版本 | 必需 | 说明 |
|---|---|---|---|
| Python | 3.11+ | ✅ | 本地直跑必需 |
| Docker Desktop | 最新 | 推荐 | 一键拉起 zlagent + postgres + neo4j + redis |
| Git | 任意 | ✅ | 克隆代码 |
| Node.js / npm | 18+ | 可选 | 装 npm 类 MCP server 时需要 |
| `uv` / `uvx` | 可选 | 可选 | 装 Python 类 MCP server 用 |

LLM API key（DeepSeek / Qwen / OpenAI 任一兼容服务）— 不配也能起，但 agent 只能 echo。

---

## 1. 首次建立

### 1.1 克隆代码

```powershell
git clone https://github.com/Kkkirito-123/LZAgent.git
cd LZAgent
```

### 1.2 配置环境变量

```powershell
copy .env.example .env
notepad .env
```

最少改这三项即可正常用：

```env
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

完整字段见 `.env.example` 注释。

---

## 2. 启动

```powershell
docker compose up -d --build
```

会拉起四个容器：

| 服务 | 端口 | 用途 |
|---|---|---|
| `zlagent` | 8020 | 主应用 FastAPI |
| `zlagent-postgres` | 5432 | 业务数据 + pgvector |
| `zlagent-neo4j` | 7474 (web) / 7687 (bolt) | GraphRAG 知识图谱 |
| `zlagent-redis` | 6379 | 会话 / 缓存 LRU |

打开 `http://localhost:8020/api/health`，应返回 `{"status":"ok","version":"1.0.0"}`。

绑定个人微信扫码（任何时候要换号都跑这条）：

```powershell
docker compose run --rm weixin-login
```

---

## 3. 八组件自检

```powershell
curl http://localhost:8020/api/doctor
```

返回 LLM / DB / 工作区 / 技能 / Cron / 网关 / 记忆 / MCP 八项状态。任何一项 `error` 都会被列出。

---

## 4. 同时查看日志

LZAgent 日志分为三路：

| 日志 | 内容 | 位置 |
|---|---|---|
| **应用主日志** | agent loop / IM 网关 / 路由 / 请求 / 异常 | 容器 stdout（`docker compose logs`） |
| **MCP 安装日志** | npm / pip / uvx 装包过程的 stdout + stderr | 容器内 `workspace/logs/mcp-install.log` |
| **MCP 运行日志** | 已连接 MCP server 子进程的 stderr | 容器内 `workspace/logs/mcp-stderr.log` |

### 4.1 单终端跟主日志（够用）

```powershell
docker compose logs -f zlagent
```

### 4.2 三窗口同时观察（排查时推荐）

开三个 PowerShell 窗口各跑一条：

```powershell
# 窗口 1：应用主日志（容器 stdout）
docker compose logs -f --tail=100 zlagent

# 窗口 2：MCP 安装管线
docker compose exec zlagent sh -c "tail -F workspace/logs/mcp-install.log"

# 窗口 3：MCP 子进程 stderr
docker compose exec zlagent sh -c "tail -F workspace/logs/mcp-stderr.log"
```

### 4.3 单窗口三路合一（PowerShell 后台 job）

```powershell
$j1 = Start-Job { docker compose logs -f --no-color zlagent }
$j2 = Start-Job { docker compose exec -T zlagent sh -c "tail -F workspace/logs/mcp-install.log" }
$j3 = Start-Job { docker compose exec -T zlagent sh -c "tail -F workspace/logs/mcp-stderr.log" }

# 实时拉取三个 job 的输出
while ($true) {
    Receive-Job $j1, $j2, $j3
    Start-Sleep -Milliseconds 500
}

# 退出时清理
Stop-Job $j1, $j2, $j3 ; Remove-Job $j1, $j2, $j3
```

### 4.4 按级别过滤

启动前在 `.env` 改：

```env
LZAGENT_LOG_LEVEL=DEBUG     # 默认 INFO；问题排查时调到 DEBUG
```

DEBUG 会包含每个 turn 的 prompt 长度、router LLM 决策、guardrail 签名等细节。生产环境建议保持 INFO。

### 4.5 实时跟某条会话

会话上下文落在容器内 `workspace/memory/session_context.jsonl`，可同步 tail：

```powershell
docker compose exec zlagent sh -c "tail -F workspace/memory/session_context.jsonl"
```

每行一个 JSON turn，含 `platform / user_id / role / content / timestamp`。

---

## 5. 常见问题排查

| 现象 | 检查 |
|---|---|
| `/api/health` 502 | `docker compose ps`，`zlagent` 容器是否 healthy |
| `/api/doctor` 显示 LLM down | `.env` 里 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` 三项是否都填 |
| GraphRAG 节点为空 | Neo4j 容器是否启动（`docker compose logs neo4j`）；`LZAGENT_GRAPH_LLM_ENABLED=true` |
| MCP 工具装不上 | `workspace/logs/mcp-install.log` 看 npm / pip 真实报错；网络 / 包名 / `--ignore-scripts` |
| MCP 工具装上但调用 fail | `workspace/logs/mcp-stderr.log` 看子进程异常 |
| 微信收不到回复 | `docker compose logs zlagent | findstr weixin`；检查 `WEIXIN_BASE_URL` / Webhook 签名 |
| Postgres 启动慢 | 首次 init schema 约 10–20s，等 healthcheck 转绿 |

---

## 6. 停止 / 重启 / 清理

### 6.1 软停（保留数据）

```powershell
docker compose stop
```

### 6.2 重启某个服务

```powershell
docker compose restart zlagent
```

### 6.3 完全停（保留 named volume）

```powershell
docker compose down
```

### 6.4 完全清空（连数据卷一起删）

```powershell
docker compose down -v
```

⚠️ **不可逆**：会清空所有长期记忆、cron 任务、MCP server 配置、知识库、知识图谱。

---

## 7. 验证脚本

在 zlagent 容器内执行（脚本依赖 backend 包，宿主机没装 Python 也能跑）：

```powershell
docker compose exec zlagent python -m compileall -q backend scripts   # 编译检查
docker compose exec zlagent python scripts/smoke_llm_graph.py          # GraphRAG LLM 抽取闭环
docker compose exec zlagent python scripts/mcp_e2e_check.py            # MCP stdio 端到端
docker compose exec zlagent python scripts/mcp_e2e_http_check.py       # MCP HTTP 端到端
docker compose exec zlagent python scripts/demo_e2e.py                 # 离线 demo（无需 LLM key）
```

每个脚本都自带 assert，exit 0 即通过。
