# Iris 部署与使用手册

> **版本**：v1.0.0  
> **更新**：2026-03-22  

---

## 目录

1. [系统要求](#1-系统要求)
2. [获取项目](#2-获取项目)
3. [环境配置](#3-环境配置)
4. [Docker 部署（推荐）](#4-docker-部署推荐)
5. [本地开发部署](#5-本地开发部署)
6. [初始化仓库](#6-初始化仓库)
7. [Webhook 配置](#7-webhook-配置)
8. [API 使用指南](#8-api-使用指南)
9. [Agent 可视化](#9-agent-可视化)
10. [常见问题排查](#10-常见问题排查)
11. [升级与维护](#11-升级与维护)

---

## 1. 系统要求

### 1.1 硬件要求

| 规格 | 最低 | 推荐 |
|------|------|------|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 10 GB | 50 GB+（取决于仓库大小） |

### 1.2 软件要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Docker | 24.0+ | 容器运行时 |
| Docker Compose | 2.20+ | 编排工具 |
| Python | 3.11+ | 本地开发时需要 |
| Git | 2.30+ | 代码拉取 |

### 1.3 外部服务

- **LLM API Key**：Anthropic Claude（推荐）/ OpenAI / Grok，任选其一
- **网络访问**：能访问目标 Git 平台（GitHub / GitCode）

---

## 2. 获取项目

```bash
git clone https://github.com/your-org/iris.git
cd iris
```

目录结构确认：

```
iris/
├── docker-compose.yml
├── Dockerfile
├── config.yaml
├── requirements.txt
├── main.py
├── .env.example
├── core/
├── agents/
├── storage/
├── utils/
└── docs/
```

---

## 3. 环境配置

### 3.1 复制环境变量模板

```bash
cp .env.example .env
```

### 3.2 填写 `.env` 文件

用任意编辑器打开 `.env`，填入实际值：

```dotenv
# ── LLM（选填其一）──
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxx

# 如果使用 OpenAI
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# ── 数据库 ──
NEO4J_PASSWORD=iris_secure_2026          # 自定义，记住即可

# ── Git 平台 Token ──
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx    # GitHub → Settings → Developer settings → Personal access tokens
GITCODE_TOKEN=your-gitcode-token         # GitCode → 账号设置 → 访问令牌

# ── Webhook 签名密钥 ──
WEBHOOK_SECRET=iris-webhook-secret-2026  # 自定义，需与 Git 平台配置一致
```

> **安全提示**：`.env` 已在 `.gitignore` 中，绝对不要提交到 Git 仓库。

### 3.3 修改 `config.yaml`（可选）

默认配置开箱即用，以下是常见的自定义项：

```yaml
# 切换 LLM（默认 Anthropic，可改为 openai）
llm:
  provider: "anthropic"           # 改为 "openai" 切换到 GPT
  model: "claude-sonnet-4-20250514"

# 调整 Agent 反思次数
agent:
  max_reflections: 3              # 增大则更准确，但响应更慢

# 调整代码解析忽略规则
parser:
  ignore_patterns:
    - "node_modules/"
    - ".git/"
    - "vendor/"                   # 可添加项目特定的忽略目录
```

---

## 4. Docker 部署（推荐）

### 4.1 启动所有服务

```bash
# 后台启动 Iris + Neo4j + Chroma
docker-compose up -d
```

启动后包含 3 个容器：

| 容器名 | 服务 | 端口 |
|--------|------|------|
| `iris-app` | Iris FastAPI 服务 | `8000` |
| `iris-neo4j` | Neo4j 图数据库 | `7474`（Web UI）`7687`（Bolt） |
| `iris-chroma` | Chroma 向量数据库 | `8001` |

### 4.2 验证启动状态

```bash
# 检查容器是否都在运行
docker-compose ps

# 健康检查（等 30 秒左右让服务完全就绪）
curl http://localhost:8000/health
```

预期返回：

```json
{
  "status": "healthy",
  "neo4j": "connected",
  "chroma": "connected"
}
```

### 4.3 查看日志

```bash
# 查看所有服务日志
docker-compose logs -f

# 只看 Iris 应用日志
docker-compose logs -f iris

# 只看 Neo4j 日志
docker-compose logs -f neo4j
```

### 4.4 停止服务

```bash
# 停止但保留数据
docker-compose stop

# 停止并删除容器（数据卷保留）
docker-compose down

# 停止并删除所有数据（完全重置）
docker-compose down -v
```

### 4.5 访问 Neo4j Web UI

浏览器打开 [http://localhost:7474](http://localhost:7474)

- 用户名：`neo4j`
- 密码：你在 `.env` 中设置的 `NEO4J_PASSWORD`

可在此直接执行 Cypher 查询，查看知识图谱节点和关系。

---

## 5. 本地开发部署

适合调试和二次开发时使用。

### 5.1 创建虚拟环境

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 5.2 安装依赖

```bash
pip install -r requirements.txt
```

### 5.3 启动数据库（仍用 Docker）

```bash
# 只启动数据库，不启动 Iris 应用
docker-compose up -d neo4j chroma
```

### 5.4 修改 `config.yaml` 中的连接地址

```yaml
neo4j:
  uri: "bolt://localhost:7687"    # 本地连接，将 neo4j 改为 localhost

chroma:
  host: "localhost"               # 本地连接，将 chroma 改为 localhost
  port: 8001                      # 对应 docker-compose 映射的宿主机端口
```

### 5.5 启动 Iris

```bash
python main.py
# 或使用 uvicorn 热重载
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 6. 初始化仓库

这是使用 Iris 的**第一步**，也是最重要的一步。

### 6.1 发起初始化请求

```bash
curl -X POST http://localhost:8000/init-repo \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/your-org/your-project.git",
    "version": "v2.1.0",
    "platform": "github",
    "modules": {
      "src/payment": "high",
      "src/order": "high",
      "src/user": "medium",
      "src/notification": "medium",
      "scripts": "low",
      "migrations": "low"
    }
  }'
```

**请求参数说明：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `repo_url` | string | ✅ | 仓库 HTTPS 克隆地址 |
| `version` | string | ✅ | tag（如 `v2.1.0`）或 commit SHA（如 `a1b2c3d`） |
| `platform` | string | ✅ | `github` 或 `gitcode` |
| `modules` | object | ✅ | 模块路径→优先级映射（`high`/`medium`/`low`） |

**模块优先级说明：**

| 优先级 | 解读深度 | 适用场景 |
|--------|----------|----------|
| `high` | 函数级 + 架构权衡 + 风险分析 | 核心业务模块（支付、订单等） |
| `medium` | 文件级 + 核心逻辑 + 调用链 | 重要支撑模块 |
| `low` | 模块级简要概述 | 工具脚本、配置文件等 |

**返回示例：**

```json
{
  "task_id": "init-a3f8c2e1",
  "status": "processing",
  "message": "全仓初始化已开始，请通过 /init-repo/status/{task_id} 查询进度"
}
```

### 6.2 查询初始化进度

```bash
curl http://localhost:8000/init-repo/status/init-a3f8c2e1
```

**进行中：**

```json
{
  "status": "processing",
  "progress": 45,
  "stage": "正在处理文件... (450/1000)"
}
```

**完成：**

```json
{
  "status": "completed",
  "progress": 100,
  "stage": "初始化完成",
  "elapsed_seconds": 847.3,
  "stats": {
    "total_files": 1000,
    "total_modules": 6,
    "graph_nodes": 3420,
    "graph_relationships": 8760,
    "vector_documents": 2150,
    "errors": 0
  }
}
```

**失败：**

```json
{
  "status": "failed",
  "stage": "初始化失败: Authentication failed for 'https://...'",
  "error": "Authentication failed"
}
```

> **预计耗时**：中等规模仓库（1000 个文件）约需 15-45 分钟，主要取决于 LLM API 响应速度。

---

## 7. Webhook 配置

配置 Webhook 后，每次 PR 合并会**自动**触发知识图谱增量更新，无需人工干预。

### 7.1 GitHub Webhook 配置

1. 进入 GitHub 仓库页面
2. **Settings** → **Webhooks** → **Add webhook**
3. 填写以下信息：

| 字段 | 值 |
|------|----|
| Payload URL | `https://your-domain:8000/webhook/github` |
| Content type | `application/json` |
| Secret | 与 `.env` 中 `WEBHOOK_SECRET` 完全一致 |
| Which events | 选择 **Let me select individual events** → 勾选 **Pull requests** |
| Active | ✅ 勾选 |

4. 点击 **Add webhook**
5. 在 Webhooks 列表中查看最近的 Delivery 确认是否成功（绿色 ✓）

### 7.2 GitCode Webhook 配置

1. 进入 GitCode 仓库页面
2. **设置** → **Webhooks** → **添加 Webhook**
3. 填写：

| 字段 | 值 |
|------|----|
| URL | `https://your-domain:8000/webhook/gitcode` |
| 密钥 Token | 与 `.env` 中 `WEBHOOK_SECRET` 完全一致 |
| 触发事件 | 勾选 **合并请求事件**（Merge Request Events） |

4. 点击保存，发送测试请求验证连通性

### 7.3 内网穿透（本地开发测试 Webhook）

如果 Iris 运行在本地，Git 平台无法直接访问，可使用 [ngrok](https://ngrok.com)：

```bash
# 安装 ngrok 后
ngrok http 8000

# 将 ngrok 生成的 https 地址填入 Webhook URL
# 例如: https://abc123.ngrok.io/webhook/github
```

---

## 8. API 使用指南

### 8.1 智能问答 `/query`

这是 Iris 最核心的端点。

**基础请求：**

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "解释 auth.py 中的 JWT 验证流程",
    "module_priority": "medium"
  }'
```

**复杂跨模块查询（充分发挥 Agentic 能力）：**

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "用中级架构师水平解释 payment 模块的 Redis 限流逻辑，为什么不用数据库？这个改动对 order 模块有什么影响？如果 Redis 宕机会怎样？",
    "module_priority": "high"
  }'
```

**返回格式：**

```json
{
  "answer": "## Payment 模块 Redis 限流逻辑解析\n\n...",
  "trace": {
    "nodes_visited": ["router", "tool_executor", "reflection", "tool_executor", "reflection", "generate"],
    "tools_used": ["vector_retrieve", "graph_traverse", "cypher_query"],
    "query_type": "cross_module",
    "reflection_count": 2,
    "mermaid_trace": "graph TD\n  N0{Router Agent}..."
  },
  "sources": [
    {"file": "src/payment/rate_limiter.py", "type": "vector_search"},
    {"file": "src/order/checkout.py", "type": "vector_search"}
  ]
}
```

**字段说明：**

| 字段 | 说明 |
|------|------|
| `answer` | Markdown 格式的完整回答 |
| `trace.nodes_visited` | Agent 实际经过的节点序列 |
| `trace.tools_used` | 实际调用的工具列表 |
| `trace.query_type` | Router 判断的问题类型 |
| `trace.reflection_count` | 经历了几轮反思循环 |
| `trace.mermaid_trace` | 本次推理路径的 Mermaid 图 |
| `sources` | 引用的源文件列表 |

### 8.2 问题类型与对应效果

| 问题类型 | 示例问题 | Router 分类 | 使用工具 |
|----------|----------|-------------|----------|
| 局部细节 | "这个函数的参数是什么意思？" | `local_detail` | vector_retrieve + code_ast_analyze |
| 跨模块关系 | "A 模块改动影响 B 模块吗？" | `cross_module` | graph_traverse + cypher_query |
| 全局风险 | "架构有什么单点故障？" | `global_risk` | 全部 4 个工具 |
| 演进历史 | "这个设计是什么时候改的？" | `evolution` | vector_retrieve + graph_traverse |

### 8.3 提问技巧

**好的提问方式（具体 + 有上下文）：**

```
用高级架构师视角，解释 payment/rate_limiter.py 中的 Redis 滑动窗口限流实现，
对比令牌桶算法的优劣，以及当前实现的潜在风险。
```

```
order 模块最近有哪些变更？这些变更对 inventory 模块的库存扣减有什么影响？
```

**一般的提问方式：**

```
解释限流逻辑    ← 缺少模块上下文
支付怎么实现的  ← 过于笼统
```

---

## 9. Agent 可视化

### 9.1 查看 Agent 流程图

在浏览器中打开：

```
http://localhost:8000/visualize-agent?format=html
```

页面展示当前 LangGraph StateGraph 的完整结构，包含所有节点（Router、Tool Executor、Reflection、Generate）和条件边。

### 9.2 获取 Mermaid 源码

```bash
curl http://localhost:8000/visualize-agent?format=mermaid
```

可将输出粘贴到 [Mermaid Live Editor](https://mermaid.live) 或任何支持 Mermaid 的 Markdown 文档中。

### 9.3 下载 PNG 图片

```bash
curl -o iris-agent-graph.png \
  "http://localhost:8000/visualize-agent?format=png"
```

> **注意**：PNG 格式需要服务端已安装 `pyppeteer`（`pip install pyppeteer`），首次使用会自动下载 Chromium 浏览器内核（约 300 MB）。

---

## 10. 常见问题排查

### 10.1 健康检查返回 `degraded`

**症状：**

```json
{"status": "degraded", "neo4j": "disconnected", "chroma": "connected"}
```

**解决：**

```bash
# 检查 Neo4j 是否正常启动
docker-compose logs neo4j | tail -20

# 检查密码是否配置正确
# docker-compose.yml 中 NEO4J_AUTH=neo4j/${NEO4J_PASSWORD}
# .env 中 NEO4J_PASSWORD 必须一致

# 重启 Neo4j
docker-compose restart neo4j
```

### 10.2 初始化任务一直卡在某个进度

**症状：** `status: "processing"` 长时间不变

**解决：**

```bash
# 查看 Iris 应用日志
docker-compose logs -f iris | grep ERROR

# 常见原因：
# 1. LLM API Key 无效或额度耗尽
# 2. Git Token 权限不足（需要 repo:read 权限）
# 3. 目标仓库地址错误或无法访问
```

### 10.3 克隆仓库失败

```
GitCommandError: Authentication failed
```

**解决：**

```bash
# 检查 .env 中的 Token 是否正确
# GitHub Token 需要有 repo 读取权限
# GitCode Token 需要有代码读取权限

# 验证 Token 是否有效
curl -H "Authorization: token YOUR_GITHUB_TOKEN" \
  https://api.github.com/user
```

### 10.4 查询返回 "未检索到相关信息"

**原因：** 仓库尚未初始化，或初始化失败

**解决：**

```bash
# 查看 Chroma 中的文档数量
curl -X POST http://localhost:8000/query \
  -d '{"question": "列出所有已知的模块", "module_priority": "low"}'

# 如果确认初始化失败，重新执行初始化
```

### 10.5 内存不足（OOM）

**症状：** Docker 容器频繁重启，日志显示 OOM

**解决：**

```yaml
# 在 docker-compose.yml 中限制 Neo4j 内存
services:
  neo4j:
    environment:
      - NEO4J_server_memory_heap_initial__size=512m
      - NEO4J_server_memory_heap_max__size=1G
      - NEO4J_server_memory_pagecache_size=512m
```

### 10.6 切换 LLM 提供商

**切换到 OpenAI GPT-4：**

编辑 `config.yaml`：

```yaml
llm:
  provider: "openai"
  model: "gpt-4o"
  api_key: "${OPENAI_API_KEY}"
```

编辑 `.env`：

```
OPENAI_API_KEY=sk-xxxxxxxx
```

重启 Iris：

```bash
docker-compose restart iris
```

---

## 11. 升级与维护

### 11.1 更新 Iris

```bash
git pull origin main
docker-compose build iris
docker-compose up -d iris
```

### 11.2 备份数据

```bash
# 备份 Neo4j 数据
docker exec iris-neo4j neo4j-admin database dump neo4j \
  --to-path=/backups/neo4j-$(date +%Y%m%d).dump

# 备份 Chroma 数据（直接备份卷目录）
docker run --rm \
  -v iris_chroma_data:/data \
  -v $(pwd)/backups:/backups \
  alpine tar czf /backups/chroma-$(date +%Y%m%d).tar.gz /data
```

### 11.3 重置知识图谱（针对特定仓库）

如需对同一仓库重新初始化（例如切换了版本），直接重新调用 `/init-repo` 即可——构建器会先删除旧数据再重建。

### 11.4 监控建议

| 监控项 | 建议工具 | 告警阈值 |
|--------|----------|---------|
| API 响应时间 | Prometheus + Grafana | P99 > 30s |
| Neo4j 节点数增长 | Neo4j Metrics | 单次增量 > 10万 节点 |
| Chroma 文档数 | 自定义脚本 | 无 |
| LLM API 费用 | 各平台控制台 | 按预算设置 |

---

## 附录：完整 API 端点参考

| 端点 | 方法 | 请求体 | 返回 |
|------|------|--------|------|
| `/health` | GET | — | `{status, neo4j, chroma}` |
| `/init-repo` | POST | `{repo_url, version, platform, modules}` | `{task_id, status}` |
| `/init-repo/status/{task_id}` | GET | — | `{status, progress, stage, stats?}` |
| `/query` | POST | `{question, module_priority}` | `{answer, trace, sources}` |
| `/webhook/github` | POST | GitHub Webhook Payload | `{status}` |
| `/webhook/gitcode` | POST | GitCode Webhook Payload | `{status}` |
| `/visualize-agent?format=html` | GET | — | HTML 页面 |
| `/visualize-agent?format=mermaid` | GET | — | Mermaid 文本 |
| `/visualize-agent?format=png` | GET | — | PNG 图片 |

---

> **问题反馈**：如遇到本文档未覆盖的问题，请查看 [架构设计文档](./architecture.md) 或提交 Issue。
