"""
=== 文件路径：core/graph_builder.py ===
作用说明：知识图谱构建器。
负责将代码文件解析为结构化信息，构建 Neo4j PropertyGraph 和 Chroma 向量索引。
实现分层摘要（函数→文件→模块→社区→全仓）策略。
"""

import hashlib
import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from storage.neo4j_graph import Neo4jGraph
from storage.chroma_db import ChromaDB
from utils.prompt_templates import CODE_SUMMARY_PROMPT, PRIORITY_INSTRUCTIONS

logger = logging.getLogger(__name__)


class GraphBuilder:
    """知识图谱构建器"""

    def __init__(
        self,
        neo4j_store: Neo4jGraph,
        chroma_store: ChromaDB,
        llm: BaseChatModel,
        config: dict[str, Any],
    ):
        self._neo4j = neo4j_store
        self._chroma = chroma_store
        self._llm = llm
        self._config = config
        self._priority_config = config.get("priority", {})

    # ----------------------------------------------------------------
    # 全量构建入口
    # ----------------------------------------------------------------

    async def build_full_graph(
        self,
        repo_name: str,
        repo_url: str,
        version: str,
        files: list[dict[str, str]],
        modules: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        全量构建知识图谱。

        Args:
            repo_name: 仓库名称
            repo_url: 仓库 URL
            version: 版本号
            files: 文件列表 [{path, language, content}]
            modules: 模块列表 [{path, name, priority}]

        Returns:
            构建统计信息
        """
        stats = {"nodes": 0, "relationships": 0, "vectors": 0, "errors": 0}

        # 创建索引
        self._neo4j.create_indexes()

        # 1. 创建仓库根节点
        self._neo4j.merge_node("Repository", {"name": repo_name}, {
            "url": repo_url,
            "version": version,
        })
        stats["nodes"] += 1

        # 2. 创建模块节点
        module_map = {}
        for mod in modules:
            self._neo4j.merge_node("Module", {"path": mod["path"]}, {
                "name": mod["name"],
                "priority": mod["priority"],
            })
            self._neo4j.create_relationship(
                "Repository", "name", repo_name,
                "Module", "path", mod["path"],
                "CONTAINS",
            )
            module_map[mod["path"]] = mod["priority"]
            stats["nodes"] += 1
            stats["relationships"] += 1

        # 3. 逐文件处理
        for file_info in files:
            try:
                file_stats = await self._process_file(
                    file_info=file_info,
                    repo_name=repo_name,
                    module_map=module_map,
                )
                stats["nodes"] += file_stats.get("nodes", 0)
                stats["relationships"] += file_stats.get("relationships", 0)
                stats["vectors"] += file_stats.get("vectors", 0)
            except Exception as e:
                logger.error("处理文件失败 %s: %s", file_info["path"], e)
                stats["errors"] += 1

        # 4. 生成模块级摘要
        for mod in modules:
            try:
                await self._generate_module_summary(mod["path"], mod["name"], mod["priority"])
            except Exception as e:
                logger.error("生成模块摘要失败 %s: %s", mod["path"], e)

        logger.info("知识图谱构建完成: %s", stats)
        return stats

    # ----------------------------------------------------------------
    # 增量更新入口
    # ----------------------------------------------------------------

    async def update_incremental(
        self,
        repo_name: str,
        changed_files: list[dict[str, Any]],
        all_files: list[dict[str, str]],
        module_map: dict[str, str],
    ) -> dict[str, Any]:
        """
        增量更新知识图谱（PR 合并后调用）。

        Args:
            repo_name: 仓库名称
            changed_files: 变更文件列表 [{path, change_type, diff_content}]
            all_files: 仅含变更文件的最新内容 [{path, language, content}]
            module_map: 模块路径→优先级映射
        """
        stats = {"updated": 0, "deleted": 0, "added": 0, "errors": 0}
        affected_modules: set[str] = set()

        for change in changed_files:
            path = change["path"]
            change_type = change["change_type"]

            # 确定受影响的模块
            for mod_path in module_map:
                if path.startswith(mod_path):
                    affected_modules.add(mod_path)
                    break

            try:
                if change_type == "D":
                    self._delete_file_data(path)
                    stats["deleted"] += 1
                elif change_type in ("A", "M"):
                    file_info = next((f for f in all_files if f["path"] == path), None)
                    if file_info:
                        self._delete_file_data(path)
                        await self._process_file(file_info, repo_name, module_map)
                        stats["updated" if change_type == "M" else "added"] += 1
            except Exception as e:
                logger.error("增量更新文件失败 %s: %s", path, e)
                stats["errors"] += 1

        # 重新生成受影响模块的摘要
        for mod_path in affected_modules:
            try:
                mod_name = mod_path.split("/")[-1]
                priority = module_map.get(mod_path, "medium")
                await self._generate_module_summary(mod_path, mod_name, priority)
            except Exception as e:
                logger.error("更新模块摘要失败 %s: %s", mod_path, e)

        logger.info("增量更新完成: %s", stats)
        return stats

    # ----------------------------------------------------------------
    # 文件级处理
    # ----------------------------------------------------------------

    async def _process_file(
        self,
        file_info: dict[str, str],
        repo_name: str,
        module_map: dict[str, str],
    ) -> dict[str, int]:
        """处理单个文件：解析 AST → 创建图节点 → 写入向量"""
        stats = {"nodes": 0, "relationships": 0, "vectors": 0}

        path = file_info["path"]
        language = file_info["language"]
        content = file_info["content"]

        # 确定文件所属模块和优先级
        file_priority = self._priority_config.get("default", "medium")
        parent_module = None
        for mod_path, priority in module_map.items():
            if path.startswith(mod_path):
                file_priority = priority
                parent_module = mod_path
                break

        # 使用 tree-sitter 解析 AST
        ast_info = self._parse_file_ast(content, language)

        # 生成文件摘要
        summary = await self._generate_file_summary(path, language, content, file_priority)

        # 创建文件节点
        self._neo4j.merge_node("File", {"path": path}, {
            "name": path.split("/")[-1],
            "language": language,
            "summary": summary,
            "priority": file_priority,
        })
        stats["nodes"] += 1

        # 关联到模块
        if parent_module:
            self._neo4j.create_relationship(
                "Module", "path", parent_module,
                "File", "path", path,
                "CONTAINS",
            )
            stats["relationships"] += 1

        # 根据优先级决定解析粒度
        if file_priority == "high":
            # high: 函数级节点
            for cls in ast_info.get("classes", []):
                self._neo4j.merge_node("Class", {"name": cls["name"]}, {
                    "file_path": path,
                    "summary": "",
                })
                self._neo4j.create_relationship(
                    "File", "path", path,
                    "Class", "name", cls["name"],
                    "CONTAINS",
                )
                stats["nodes"] += 1
                stats["relationships"] += 1

                for method in cls.get("methods", []):
                    self._neo4j.merge_node("Function", {"name": method}, {
                        "file_path": path,
                        "class_name": cls["name"],
                    })
                    self._neo4j.create_relationship(
                        "Class", "name", cls["name"],
                        "Function", "name", method,
                        "CONTAINS",
                    )
                    stats["nodes"] += 1
                    stats["relationships"] += 1

            for func in ast_info.get("functions", []):
                self._neo4j.merge_node("Function", {"name": func["name"]}, {
                    "file_path": path,
                    "signature": func.get("signature", ""),
                })
                self._neo4j.create_relationship(
                    "File", "path", path,
                    "Function", "name", func["name"],
                    "CONTAINS",
                )
                stats["nodes"] += 1
                stats["relationships"] += 1

        elif file_priority == "medium":
            # medium: 仅关键函数
            for func in ast_info.get("functions", [])[:10]:
                self._neo4j.merge_node("Function", {"name": func["name"]}, {
                    "file_path": path,
                })
                self._neo4j.create_relationship(
                    "File", "path", path,
                    "Function", "name", func["name"],
                    "CONTAINS",
                )
                stats["nodes"] += 1
                stats["relationships"] += 1

        # 处理导入关系
        for imp in ast_info.get("imports", []):
            imported_module = self._extract_import_target(imp)
            if imported_module:
                self._neo4j.create_relationship(
                    "File", "path", path,
                    "File", "path", imported_module,
                    "IMPORTS",
                )
                stats["relationships"] += 1

        # 写入向量数据库
        chunk_size = self._priority_config.get("chunk_sizes", {}).get(file_priority, 300)
        chunks = self._chunk_content(content, chunk_size)
        for i, chunk in enumerate(chunks):
            doc_id = self._make_id(f"{path}:chunk:{i}")
            self._chroma.upsert_documents(
                ids=[doc_id],
                documents=[chunk],
                metadatas=[{
                    "file_path": path,
                    "language": language,
                    "module": parent_module or "",
                    "priority": file_priority,
                    "chunk_index": i,
                    "type": "code",
                }],
            )
            stats["vectors"] += 1

        # 摘要也写入向量数据库
        if summary:
            summary_id = self._make_id(f"{path}:summary")
            self._chroma.upsert_documents(
                ids=[summary_id],
                documents=[summary],
                metadatas=[{
                    "file_path": path,
                    "module": parent_module or "",
                    "priority": file_priority,
                    "type": "summary",
                }],
            )
            stats["vectors"] += 1

        return stats

    def _delete_file_data(self, file_path: str) -> None:
        """删除文件相关的所有图节点和向量"""
        self._neo4j.delete_node_by_property("File", "path", file_path)
        self._chroma.delete_by_metadata({"file_path": file_path})
        logger.info("已删除文件数据: %s", file_path)

    # ----------------------------------------------------------------
    # 摘要生成
    # ----------------------------------------------------------------

    async def _generate_file_summary(
        self, file_path: str, language: str, content: str, priority: str
    ) -> str:
        """使用 LLM 为文件生成摘要"""
        try:
            # 截断过长的内容
            max_chars = 8000
            truncated = content[:max_chars] + ("\n... (已截断)" if len(content) > max_chars else "")

            prompt = CODE_SUMMARY_PROMPT.format(
                file_path=file_path,
                language=language,
                priority=priority,
                code=truncated,
                priority_instruction=PRIORITY_INSTRUCTIONS.get(priority, ""),
            )
            response = self._llm.invoke(prompt)
            return response.content
        except Exception as e:
            logger.error("生成文件摘要失败 %s: %s", file_path, e)
            return ""

    async def _generate_module_summary(self, mod_path: str, mod_name: str, priority: str) -> None:
        """聚合模块下所有文件摘要，生成模块级摘要"""
        try:
            results = self._neo4j.execute_cypher(
                "MATCH (m:Module {path: $path})-[:CONTAINS]->(f:File) "
                "RETURN f.summary AS summary, f.path AS path",
                {"path": mod_path},
            )

            file_summaries = [
                f"- {r['path']}: {r['summary']}"
                for r in results if r.get("summary")
            ]
            if not file_summaries:
                return

            prompt = (
                f"请为模块 '{mod_name}'（优先级：{priority}）生成模块级摘要。\n\n"
                f"该模块包含以下文件的摘要：\n{''.join(file_summaries[:20])}\n\n"
                f"请概括该模块的整体功能、核心职责以及与其他模块的潜在交互。"
            )
            response = self._llm.invoke(prompt)
            module_summary = response.content

            self._neo4j.merge_node("Module", {"path": mod_path}, {"summary": module_summary})

            # 模块摘要也写入向量库
            self._chroma.upsert_documents(
                ids=[self._make_id(f"{mod_path}:module_summary")],
                documents=[module_summary],
                metadatas=[{
                    "module": mod_path,
                    "priority": priority,
                    "type": "module_summary",
                }],
            )
            logger.info("模块摘要已生成: %s", mod_path)

        except Exception as e:
            logger.error("生成模块摘要失败 %s: %s", mod_path, e)

    # ----------------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------------

    def _parse_file_ast(self, content: str, language: str) -> dict[str, Any]:
        """解析文件 AST，提取结构信息"""
        try:
            from tree_sitter_languages import get_parser
            parser = get_parser(language)
            tree = parser.parse(content.encode("utf-8"))
            root = tree.root_node

            classes = []
            functions = []
            imports = []

            def walk(node, depth=0):
                if node.type in ("class_definition", "class_declaration"):
                    name = self._get_node_name(node, content)
                    methods = []
                    for child in node.children:
                        if child.type in ("function_definition", "method_definition"):
                            methods.append(self._get_node_name(child, content))
                    classes.append({"name": name, "methods": methods})

                elif node.type in ("function_definition", "function_declaration") and depth == 0:
                    name = self._get_node_name(node, content)
                    sig_line = content.split("\n")[node.start_point[0]].strip()
                    functions.append({"name": name, "signature": sig_line})

                elif node.type in ("import_statement", "import_from_statement"):
                    imports.append(content[node.start_byte:node.end_byte].strip())

                for child in node.children:
                    walk(child, depth + 1)

            walk(root)
            return {"classes": classes, "functions": functions, "imports": imports}

        except Exception as e:
            logger.debug("tree-sitter 解析失败，使用降级方案: %s", e)
            return self._fallback_ast_parse(content, language)

    def _fallback_ast_parse(self, content: str, language: str) -> dict[str, Any]:
        """降级 AST 解析（正则表达式）"""
        import re
        classes = []
        functions = []
        imports = []

        lines = content.split("\n")

        if language == "python":
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("class "):
                    name = stripped.split("(")[0].split(":")[0].replace("class ", "").strip()
                    classes.append({"name": name, "methods": []})
                elif stripped.startswith("def "):
                    name = stripped.split("(")[0].replace("def ", "").strip()
                    functions.append({"name": name, "signature": stripped.rstrip(":")})
                elif stripped.startswith(("import ", "from ")):
                    imports.append(stripped)

        return {"classes": classes, "functions": functions, "imports": imports}

    @staticmethod
    def _get_node_name(node, source: str) -> str:
        """从 AST 节点提取名称"""
        name_node = node.child_by_field_name("name")
        if name_node:
            return source[name_node.start_byte:name_node.end_byte]
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte:child.end_byte]
        return "<unknown>"

    @staticmethod
    def _extract_import_target(import_stmt: str) -> str:
        """从 import 语句中提取目标模块路径"""
        if import_stmt.startswith("from "):
            parts = import_stmt.split()
            if len(parts) >= 2:
                module = parts[1].replace(".", "/")
                return module + ".py"
        return ""

    @staticmethod
    def _chunk_content(content: str, chunk_size: int) -> list[str]:
        """将内容按行分块"""
        lines = content.split("\n")
        chunks = []
        for i in range(0, len(lines), chunk_size):
            chunk = "\n".join(lines[i:i + chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    @staticmethod
    def _make_id(key: str) -> str:
        """生成确定性文档 ID"""
        return hashlib.sha256(key.encode()).hexdigest()[:16]
