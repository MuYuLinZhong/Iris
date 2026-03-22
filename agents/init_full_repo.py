"""
=== 文件路径：agents/init_full_repo.py ===
作用说明：全仓初始化解读 Agent。
负责完整的仓库克隆→代码解析→知识图谱构建→分层摘要生成流程。
由 /init-repo API 触发，支持后台异步执行和进度追踪。
"""

import asyncio
import logging
import time
from typing import Any

from core.repo_manager import RepoManager
from core.graph_builder import GraphBuilder

logger = logging.getLogger(__name__)

# 任务状态存储（生产环境应使用 Redis 等持久化方案）
_task_store: dict[str, dict[str, Any]] = {}


def get_task_status(task_id: str) -> dict[str, Any] | None:
    """获取任务状态"""
    return _task_store.get(task_id)


async def init_full_repo(
    task_id: str,
    repo_url: str,
    version: str,
    platform: str,
    modules: dict[str, str],
    repo_manager: RepoManager,
    graph_builder: GraphBuilder,
    parser_config: dict[str, Any],
) -> None:
    """
    执行全仓初始化解读。

    这是一个长时间运行的异步任务，通过 task_id 追踪进度。

    Args:
        task_id: 任务唯一标识
        repo_url: 仓库 URL
        version: 目标版本（tag 或 commit SHA）
        platform: Git 平台（gitcode / github）
        modules: 模块路径→优先级映射
        repo_manager: Git 仓库管理器实例
        graph_builder: 知识图谱构建器实例
        parser_config: 解析器配置
    """
    start_time = time.time()

    _task_store[task_id] = {
        "status": "processing",
        "progress": 0,
        "stage": "正在克隆仓库...",
        "started_at": start_time,
        "repo_url": repo_url,
        "version": version,
    }

    try:
        # ---- 阶段 1：克隆仓库 ----
        _update_task(task_id, 5, "正在克隆仓库...")
        logger.info("[InitRepo] 开始克隆: %s @ %s", repo_url, version)

        repo_path = repo_manager.clone_repo(repo_url, version)
        _update_task(task_id, 15, "仓库克隆完成，正在扫描文件...")

        # ---- 阶段 2：扫描文件 ----
        logger.info("[InitRepo] 扫描代码文件...")
        files = repo_manager.get_all_files(
            supported_languages=parser_config.get("supported_languages"),
            ignore_patterns=parser_config.get("ignore_patterns"),
            max_file_size_kb=parser_config.get("max_file_size_kb", 500),
        )
        _update_task(task_id, 25, f"扫描完成，共 {len(files)} 个文件，正在检测模块...")

        # ---- 阶段 3：检测模块 ----
        detected_modules = repo_manager.detect_modules(modules)
        _update_task(task_id, 30, f"检测到 {len(detected_modules)} 个模块，开始构建知识图谱...")

        # ---- 阶段 4：构建知识图谱 ----
        logger.info("[InitRepo] 开始构建知识图谱（%d 个文件，%d 个模块）",
                     len(files), len(detected_modules))

        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

        # 分批处理，更新进度
        total_files = len(files)
        batch_size = max(1, total_files // 10)

        for i in range(0, total_files, batch_size):
            batch = files[i:i + batch_size]
            progress = 30 + int((i / total_files) * 60)
            _update_task(
                task_id, progress,
                f"正在处理文件... ({min(i + batch_size, total_files)}/{total_files})",
            )

        # 实际构建（全量一次性）
        stats = await graph_builder.build_full_graph(
            repo_name=repo_name,
            repo_url=repo_url,
            version=version,
            files=files,
            modules=detected_modules,
        )

        # ---- 阶段 5：完成 ----
        elapsed = time.time() - start_time
        _task_store[task_id] = {
            "status": "completed",
            "progress": 100,
            "stage": "初始化完成",
            "started_at": start_time,
            "completed_at": time.time(),
            "elapsed_seconds": round(elapsed, 1),
            "repo_url": repo_url,
            "version": version,
            "stats": {
                "total_files": len(files),
                "total_modules": len(detected_modules),
                "graph_nodes": stats.get("nodes", 0),
                "graph_relationships": stats.get("relationships", 0),
                "vector_documents": stats.get("vectors", 0),
                "errors": stats.get("errors", 0),
            },
        }
        logger.info("[InitRepo] 全仓初始化完成，耗时 %.1f 秒, 统计: %s", elapsed, stats)

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error("[InitRepo] 初始化失败: %s", e, exc_info=True)
        _task_store[task_id] = {
            "status": "failed",
            "progress": 0,
            "stage": f"初始化失败: {str(e)}",
            "started_at": start_time,
            "elapsed_seconds": round(elapsed, 1),
            "error": str(e),
        }


def _update_task(task_id: str, progress: int, stage: str) -> None:
    """更新任务进度"""
    if task_id in _task_store:
        _task_store[task_id]["progress"] = progress
        _task_store[task_id]["stage"] = stage
    logger.info("[InitRepo] [%d%%] %s", progress, stage)


async def handle_pr_webhook(
    repo_url: str,
    base_sha: str,
    head_sha: str,
    repo_manager: RepoManager,
    graph_builder: GraphBuilder,
    module_map: dict[str, str],
    parser_config: dict[str, Any],
) -> dict[str, Any]:
    """
    处理 PR 合并后的增量更新。

    Args:
        repo_url: 仓库 URL
        base_sha: 合并前的 commit SHA
        head_sha: 合并后的 commit SHA
        repo_manager: 仓库管理器
        graph_builder: 图谱构建器
        module_map: 模块优先级映射
        parser_config: 解析器配置

    Returns:
        增量更新统计信息
    """
    logger.info("[Webhook] 处理 PR 增量更新: %s..%s", base_sha[:8], head_sha[:8])

    try:
        # 拉取最新代码
        repo_manager.pull_latest()

        # 获取变更文件列表
        changed_files = repo_manager.get_pr_changed_files(base_sha, head_sha)
        logger.info("[Webhook] 变更文件: %d 个", len(changed_files))

        if not changed_files:
            return {"status": "no_changes", "changed_files": 0}

        # 读取变更文件的最新内容
        updated_files = []
        for change in changed_files:
            if change["change_type"] != "D":
                content = repo_manager.get_file_content(change["path"])
                if content:
                    ext = change["path"].rsplit(".", 1)[-1] if "." in change["path"] else ""
                    lang_map = {"py": "python", "js": "javascript", "ts": "typescript",
                                "java": "java", "go": "go", "rs": "rust"}
                    updated_files.append({
                        "path": change["path"],
                        "language": lang_map.get(ext, "unknown"),
                        "content": content,
                    })

        # 执行增量更新
        stats = await graph_builder.update_incremental(
            repo_name=repo_url.rstrip("/").split("/")[-1].replace(".git", ""),
            changed_files=changed_files,
            all_files=updated_files,
            module_map=module_map,
        )

        return {
            "status": "completed",
            "changed_files": len(changed_files),
            "stats": stats,
        }

    except Exception as e:
        logger.error("[Webhook] 增量更新失败: %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}
