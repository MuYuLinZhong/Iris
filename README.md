# Iris 🔮

> **私有化、智能化的代码讲解与知识库系统**

Iris 是一个基于 **Agentic + GraphRAG** 架构的代码知识库系统，专为忙碌架构师设计。它像一位资深架构师一样，能动态分析代码仓库、理解跨模块关系、自我反思纠错，为你提供深度的代码讲解和架构分析。

## 核心特性

- **全仓深度解读**：一键对指定版本进行结构化解读，构建多层知识图谱
- **PR 增量演进**：Webhook 驱动，PR 合并后自动更新知识图谱
- **Agentic 智能问答**：LangGraph 实现 Router → Tool Executor → Reflection 循环
- **永不爆上下文**：GraphRAG 分层摘要 + Agent 按需拉取子图
- **4 大工具**：向量检索 / 图遍历 / Cypher 查询 / AST 分析
- **模块优先级**：high / medium / low 三级精细控制
- **全私有部署**：一键 Docker 部署，数据完全私有

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | FastAPI + Uvicorn |
| Agent 编排 | LangGraph (StateGraph + Reflection Loop) |
| RAG 引擎 | LlamaIndex (PropertyGraphIndex + GraphRAG) |
| 向量数据库 | Chroma |
| 图数据库 | Neo4j |
| 代码解析 | tree-sitter |
| LLM | Anthropic Claude（支持切换 OpenAI / Grok）|

## 快速开始

### 1. 环境准备

```bash
git clone https://github.com/your-org/iris.git
cd iris
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API Key：

```
ANTHROPIC_API_KEY=sk-ant-xxx
NEO4J_PASSWORD=your-password
GITHUB_TOKEN=ghp_xxx
WEBHOOK_SECRET=your-secret
```

### 2. 一键启动

```bash
docker-compose up -d
```

### 3. 验证服务

```bash
curl http://localhost:8000/health
```

### 4. 初始化仓库

```bash
curl -X POST http://localhost:8000/init-repo \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/your-org/your-project.git",
    "version": "v1.0.0",
    "platform": "github",
    "modules": {
      "src/payment": "high",
      "src/order": "high",
      "src/utils": "medium",
      "scripts": "low"
    }
  }'
```

### 5. 开始提问

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "用中级架构师水平解释 payment 模块的 Redis 限流逻辑",
    "module_priority": "high"
  }'
```

### 6. 查看 Agent 图

浏览器打开：http://localhost:8000/visualize-agent?format=html

## 项目结构

```
iris/
├── docker-compose.yml          # Docker 编排
├── Dockerfile                  # 应用镜像
├── config.yaml                 # 配置文件
├── requirements.txt            # Python 依赖
├── main.py                     # FastAPI 入口 + 所有 API 端点
├── core/
│   ├── repo_manager.py         # Git 仓库管理（GitPython）
│   ├── graph_builder.py        # 知识图谱构建（Neo4j + Chroma）
│   └── explainer_agent.py      # LangGraph Agent 主逻辑
├── agents/
│   └── init_full_repo.py       # 全仓初始化 Agent
├── storage/
│   ├── chroma_db.py            # Chroma 向量数据库封装
│   └── neo4j_graph.py          # Neo4j 图数据库封装
├── utils/
│   ├── prompt_templates.py     # Prompt 模板集合
│   ├── tools.py                # 4 个 Agent 工具实现
│   └── visualize.py            # LangGraph 可视化
├── docs/
│   └── architecture.md         # 架构设计文档
└── data/                       # 运行时数据（.gitignore）
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/init-repo` | POST | 初始化全仓解读 |
| `/init-repo/status/{task_id}` | GET | 查询初始化进度 |
| `/query` | POST | 智能问答 |
| `/webhook/github` | POST | GitHub Webhook |
| `/webhook/gitcode` | POST | GitCode Webhook |
| `/visualize-agent` | GET | Agent 图可视化（支持 mermaid/html/png）|

## Agent 工作流程

```
用户提问 → Router（问题分类）→ Tool Executor（多工具并行）→ Reflection（自我反思）
                                       ↑                          ↓
                                       ←── 结果不完整时重新执行 ←──┘
                                                                   ↓ 结果完整
                                                           Generate（生成回答）
```

## 许可证

MIT License
