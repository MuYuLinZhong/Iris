"""
=== 文件路径：main.py ===
作用说明：Iris 应用主入口。
FastAPI 服务器，包含所有 API 端点：
- /health          健康检查
- /init-repo       全仓初始化
- /query           智能问答
- /webhook/*       Git 平台 Webhook
- /visualize-agent Agent 图可视化
"""

import asyncio
import hashlib
import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel

from core.repo_manager import RepoManager
from core.graph_builder import GraphBuilder
from core.explainer_agent import ExplainerAgent
from storage.neo4j_graph import Neo4jGraph
from storage.chroma_db import ChromaDB
from agents.init_full_repo import init_full_repo, get_task_status, handle_pr_webhook
from utils.visualize import visualize_graph
from utils import tools as tools_module

# ----------------------------------------------------------------
# 日志配置
# ----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("iris")

# ----------------------------------------------------------------
# 加载配置
# ----------------------------------------------------------------
load_dotenv()


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    """加载并解析配置文件，支持 ${ENV_VAR} 语法"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    for key, value in os.environ.items():
        raw = raw.replace(f"${{{key}}}", value)

    return yaml.safe_load(raw)


config = load_config()

# ----------------------------------------------------------------
# 全局组件（在 lifespan 中初始化）
# ----------------------------------------------------------------
neo4j_store: Neo4jGraph | None = None
chroma_store: ChromaDB | None = None
repo_manager: RepoManager | None = None
graph_builder: GraphBuilder | None = None
explainer_agent: ExplainerAgent | None = None


def _create_llm():
    """根据配置创建 LLM 实例"""
    llm_config = config.get("llm", {})
    provider = llm_config.get("provider", "anthropic")
    model = llm_config.get("model", "claude-sonnet-4-20250514")
    temperature = llm_config.get("temperature", 0.1)
    max_tokens = llm_config.get("max_tokens", 4096)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )


# ----------------------------------------------------------------
# Lifespan：启动时连接数据库，关闭时断开
# ----------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global neo4j_store, chroma_store, repo_manager, graph_builder, explainer_agent

    logger.info("Iris 正在启动...")

    # 连接 Neo4j
    neo4j_cfg = config.get("neo4j", {})
    neo4j_store = Neo4jGraph(
        uri=neo4j_cfg.get("uri", "bolt://localhost:7687"),
        username=neo4j_cfg.get("username", "neo4j"),
        password=neo4j_cfg.get("password", ""),
        database=neo4j_cfg.get("database", "neo4j"),
    )
    neo4j_store.connect()

    # 连接 Chroma
    chroma_cfg = config.get("chroma", {})
    chroma_store = ChromaDB(
        host=chroma_cfg.get("host", "localhost"),
        port=chroma_cfg.get("port", 8000),
        collection_name=chroma_cfg.get("collection_name", "iris_code"),
        persist_directory=chroma_cfg.get("persist_directory", "./data/chroma"),
    )
    chroma_store.connect()

    # 初始化 LLM
    llm = _create_llm()

    # 初始化 RepoManager
    git_cfg = config.get("git", {})
    repo_manager = RepoManager(
        clone_directory=git_cfg.get("clone_directory", "./data/repos"),
        platforms=git_cfg.get("platforms", {}),
    )

    # 初始化 GraphBuilder
    graph_builder = GraphBuilder(
        neo4j_store=neo4j_store,
        chroma_store=chroma_store,
        llm=llm,
        config=config,
    )

    # 初始化 Agent 工具
    tools_module.init_tools(
        neo4j_store=neo4j_store,
        chroma_store=chroma_store,
        llm=llm,
        config=config,
        repo_base_path=git_cfg.get("clone_directory", "./data/repos"),
    )

    # 初始化 ExplainerAgent
    explainer_agent = ExplainerAgent(llm=llm, config=config)

    logger.info("Iris 启动完成！所有组件已就绪。")

    yield

    # 关闭连接
    if neo4j_store:
        neo4j_store.close()
    logger.info("Iris 已关闭")


# ----------------------------------------------------------------
# FastAPI App
# ----------------------------------------------------------------
app = FastAPI(
    title="Iris",
    description="私有化智能代码讲解与知识库系统",
    version="1.0.0",
    lifespan=lifespan,
)


# ================================================================
# Request / Response 模型
# ================================================================

class InitRepoRequest(BaseModel):
    repo_url: str
    version: str
    platform: str = "github"
    modules: dict[str, str] = {}


class QueryRequest(BaseModel):
    question: str
    module_priority: str = "medium"


# ================================================================
# API 端点
# ================================================================

@app.get("/health")
async def health_check():
    """健康检查：验证所有组件连接状态"""
    neo4j_ok = neo4j_store.health_check() if neo4j_store else False
    chroma_ok = chroma_store.health_check() if chroma_store else False

    status = "healthy" if (neo4j_ok and chroma_ok) else "degraded"
    return {
        "status": status,
        "neo4j": "connected" if neo4j_ok else "disconnected",
        "chroma": "connected" if chroma_ok else "disconnected",
    }


@app.post("/init-repo")
async def init_repo_endpoint(req: InitRepoRequest, background_tasks: BackgroundTasks):
    """
    初始化全仓解读（异步任务）。
    返回 task_id，通过 /init-repo/status/{task_id} 查询进度。
    """
    task_id = f"init-{uuid.uuid4().hex[:8]}"

    if not repo_manager or not graph_builder:
        raise HTTPException(status_code=503, detail="服务尚未就绪")

    parser_cfg = config.get("parser", {})

    background_tasks.add_task(
        init_full_repo,
        task_id=task_id,
        repo_url=req.repo_url,
        version=req.version,
        platform=req.platform,
        modules=req.modules,
        repo_manager=repo_manager,
        graph_builder=graph_builder,
        parser_config=parser_cfg,
    )

    return {
        "task_id": task_id,
        "status": "processing",
        "message": "全仓初始化已开始，请通过 /init-repo/status/{task_id} 查询进度",
    }


@app.get("/init-repo/status/{task_id}")
async def init_repo_status(task_id: str):
    """查询初始化任务进度"""
    status = get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return status


@app.post("/query")
async def query_endpoint(req: QueryRequest):
    """
    智能问答端点。
    使用 LangGraph Agent 处理复杂代码相关问题。
    """
    if not explainer_agent:
        raise HTTPException(status_code=503, detail="Agent 尚未就绪")

    try:
        result = await explainer_agent.query(
            question=req.question,
            module_priority=req.module_priority,
        )
        return result
    except Exception as e:
        logger.error("查询失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询处理失败: {str(e)}")


# ----------------------------------------------------------------
# Webhook 端点
# ----------------------------------------------------------------

@app.post("/webhook/github")
async def webhook_github(request: Request, background_tasks: BackgroundTasks):
    """GitHub Webhook：处理 PR 合并事件"""
    body = await request.body()

    # 验证签名
    webhook_secret = config.get("webhook", {}).get("secret", "")
    if webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_github_signature(body, webhook_secret, signature):
            raise HTTPException(status_code=401, detail="签名验证失败")

    payload = await request.json()
    action = payload.get("action", "")

    if action != "closed" or not payload.get("pull_request", {}).get("merged"):
        return {"status": "ignored", "reason": "非 PR 合并事件"}

    pr = payload["pull_request"]
    repo_url = payload.get("repository", {}).get("clone_url", "")
    base_sha = pr.get("base", {}).get("sha", "")
    head_sha = pr.get("merge_commit_sha", "")

    if not all([repo_url, base_sha, head_sha]):
        raise HTTPException(status_code=400, detail="缺少必要的 PR 信息")

    # 从配置中获取模块映射（实际应从数据库读取）
    module_map = config.get("_runtime_modules", {})
    parser_cfg = config.get("parser", {})

    background_tasks.add_task(
        handle_pr_webhook,
        repo_url=repo_url,
        base_sha=base_sha,
        head_sha=head_sha,
        repo_manager=repo_manager,
        graph_builder=graph_builder,
        module_map=module_map,
        parser_config=parser_cfg,
    )

    return {"status": "processing", "pr_number": pr.get("number")}


@app.post("/webhook/gitcode")
async def webhook_gitcode(request: Request, background_tasks: BackgroundTasks):
    """GitCode Webhook：处理 Merge Request 合并事件"""
    body = await request.body()
    payload = await request.json()

    # GitCode Webhook 事件类型
    event_type = payload.get("object_kind", "")
    if event_type != "merge_request":
        return {"status": "ignored", "reason": f"非 merge_request 事件: {event_type}"}

    attrs = payload.get("object_attributes", {})
    if attrs.get("state") != "merged":
        return {"status": "ignored", "reason": "MR 未合并"}

    repo_url = payload.get("project", {}).get("git_http_url", "")
    target_branch = attrs.get("target_branch", "")

    # 使用 last_commit 获取 SHA
    base_sha = attrs.get("merge_commit_sha", attrs.get("last_commit", {}).get("id", ""))
    head_sha = base_sha

    if not repo_url:
        raise HTTPException(status_code=400, detail="缺少仓库 URL")

    module_map = config.get("_runtime_modules", {})
    parser_cfg = config.get("parser", {})

    background_tasks.add_task(
        handle_pr_webhook,
        repo_url=repo_url,
        base_sha=base_sha,
        head_sha=head_sha,
        repo_manager=repo_manager,
        graph_builder=graph_builder,
        module_map=module_map,
        parser_config=parser_cfg,
    )

    return {"status": "processing", "merge_request_id": attrs.get("iid")}


# ----------------------------------------------------------------
# 可视化端点
# ----------------------------------------------------------------

@app.get("/visualize-agent")
async def visualize_agent_endpoint(format: str = "html"):
    """
    LangGraph Agent 图可视化。

    Query Params:
        format: mermaid / html / png
    """
    if not explainer_agent:
        raise HTTPException(status_code=503, detail="Agent 尚未就绪")

    try:
        compiled = explainer_agent.compiled_graph

        if format == "mermaid":
            mermaid_text = visualize_graph(compiled, output_format="mermaid")
            return Response(content=mermaid_text, media_type="text/plain")

        elif format == "html":
            html_content = visualize_graph(compiled, output_format="html")
            return HTMLResponse(content=html_content)

        elif format == "png":
            png_bytes = visualize_graph(compiled, output_format="mermaid_png")
            return Response(content=png_bytes, media_type="image/png")

        else:
            raise HTTPException(status_code=400, detail=f"不支持的格式: {format}，可选: mermaid/html/png")

    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------

def _verify_github_signature(payload: bytes, secret: str, signature: str) -> bool:
    """验证 GitHub Webhook 签名"""
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ----------------------------------------------------------------
# 直接运行入口
# ----------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    server_cfg = config.get("server", {})
    uvicorn.run(
        "main:app",
        host=server_cfg.get("host", "0.0.0.0"),
        port=server_cfg.get("port", 8000),
        workers=server_cfg.get("workers", 1),
        log_level=server_cfg.get("log_level", "info"),
        reload=False,
    )
