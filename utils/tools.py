"""
=== 文件路径：utils/tools.py ===
作用说明：4 个 LangGraph Agent 工具的完整实现。
包括 vector_retrieve、graph_traverse、cypher_query、code_ast_analyze。
这些工具通过 @tool 装饰器注册，供 Agent 动态调用。
"""

import json
import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# 全局存储引用（在 main.py 启动时注入）
_neo4j_store = None
_chroma_store = None
_llm = None
_config: dict[str, Any] = {}
_repo_base_path: str = ""


def init_tools(neo4j_store, chroma_store, llm, config: dict[str, Any], repo_base_path: str = "") -> None:
    """初始化工具所需的外部依赖（由 main.py 启动时调用）"""
    global _neo4j_store, _chroma_store, _llm, _config, _repo_base_path
    _neo4j_store = neo4j_store
    _chroma_store = chroma_store
    _llm = llm
    _config = config
    _repo_base_path = repo_base_path
    logger.info("Agent 工具初始化完成")


# ----------------------------------------------------------------
# 工具 1：向量语义检索
# ----------------------------------------------------------------
@tool
def vector_retrieve(query: str, top_k: int = 10, module_filter: str | None = None) -> str:
    """基于语义相似度从 Chroma 向量数据库检索代码片段和文档摘要。
    适用于局部细节查询、模糊搜索、获取文件/模块级摘要。

    Args:
        query: 检索查询文本
        top_k: 返回的最大结果数
        module_filter: 可选的模块路径过滤器
    """
    if not _chroma_store:
        return json.dumps({"error": "Chroma 未连接"}, ensure_ascii=False)

    try:
        where = None
        if module_filter:
            where = {"module": module_filter}

        results = _chroma_store.query(
            query_texts=[query],
            n_results=top_k,
            where=where,
        )

        formatted = []
        if results and results.get("documents"):
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
            distances = results["distances"][0] if results.get("distances") else [0.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, distances):
                formatted.append({
                    "content": doc[:500],
                    "metadata": meta,
                    "relevance_score": round(1 - dist, 4),
                })

        logger.info("vector_retrieve: query='%s', 返回 %d 条结果", query, len(formatted))
        return json.dumps(formatted, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("vector_retrieve 执行失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ----------------------------------------------------------------
# 工具 2：图遍历
# ----------------------------------------------------------------
@tool
def graph_traverse(
    start_node: str,
    relationship_types: str | None = None,
    max_depth: int = 3,
    direction: str = "both",
) -> str:
    """从指定节点出发，沿关系类型遍历知识图谱，获取调用链和依赖关系。

    Args:
        start_node: 起始节点名称（函数名、类名或模块路径）
        relationship_types: 逗号分隔的关系类型，如 "CALLS,DEPENDS_ON"。
                            可选值：CALLS, DEPENDS_ON, INHERITS, IMPORTS, DATA_FLOW, CONTAINS
        max_depth: 最大遍历深度（默认3）
        direction: 遍历方向 "out"/"in"/"both"
    """
    if not _neo4j_store:
        return json.dumps({"error": "Neo4j 未连接"}, ensure_ascii=False)

    try:
        rel_list = None
        if relationship_types:
            rel_list = [r.strip() for r in relationship_types.split(",")]

        # 尝试多种节点标签匹配
        results = []
        for label in ["Function", "Class", "File", "Module"]:
            key = "name" if label in ("Function", "Class") else "path"
            paths = _neo4j_store.traverse(
                start_label=label,
                start_key=key,
                start_value=start_node,
                relationship_types=rel_list,
                max_depth=max_depth,
                direction=direction,
            )
            if paths:
                results = paths
                break

        logger.info("graph_traverse: start='%s', 返回 %d 条路径", start_node, len(results))
        return json.dumps(results, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error("graph_traverse 执行失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ----------------------------------------------------------------
# 工具 3：自然语言转 Cypher 查询
# ----------------------------------------------------------------
@tool
def cypher_query(natural_language_query: str) -> str:
    """将自然语言转换为 Cypher 查询并在 Neo4j 上执行。
    适用于复杂图模式匹配、聚合统计、路径查询等。

    Args:
        natural_language_query: 自然语言描述的查询需求
    """
    if not _neo4j_store or not _llm:
        return json.dumps({"error": "Neo4j 或 LLM 未连接"}, ensure_ascii=False)

    from utils.prompt_templates import NL_TO_CYPHER_PROMPT

    try:
        # 使用 LLM 将自然语言转为 Cypher
        prompt = NL_TO_CYPHER_PROMPT.format(query=natural_language_query)
        response = _llm.invoke(prompt)
        cypher_statement = response.content.strip()

        # 清理可能的 markdown 代码块标记
        if cypher_statement.startswith("```"):
            lines = cypher_statement.split("\n")
            cypher_statement = "\n".join(lines[1:-1])

        # 安全检查：只允许只读查询
        forbidden = ["CREATE", "DELETE", "SET", "REMOVE", "MERGE", "DROP"]
        upper_cypher = cypher_statement.upper()
        for keyword in forbidden:
            if keyword in upper_cypher:
                return json.dumps({
                    "error": f"安全限制：查询包含禁止的写操作关键字 '{keyword}'",
                    "generated_cypher": cypher_statement,
                }, ensure_ascii=False)

        results = _neo4j_store.execute_cypher(cypher_statement)

        logger.info("cypher_query: nl='%s', cypher='%s', 返回 %d 条",
                     natural_language_query, cypher_statement, len(results))

        return json.dumps({
            "cypher": cypher_statement,
            "results": results,
            "count": len(results),
        }, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error("cypher_query 执行失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ----------------------------------------------------------------
# 工具 4：AST 代码结构分析
# ----------------------------------------------------------------
@tool
def code_ast_analyze(file_path: str, analysis_type: str = "structure") -> str:
    """使用 tree-sitter 实时解析代码文件的 AST，提取结构化信息。

    Args:
        file_path: 代码文件的相对路径
        analysis_type: 分析类型，可选 "structure"/"imports"/"signatures"/"complexity"
    """
    import os

    try:
        full_path = os.path.join(_repo_base_path, file_path) if _repo_base_path else file_path

        if not os.path.isfile(full_path):
            return json.dumps({"error": f"文件不存在: {full_path}"}, ensure_ascii=False)

        # 检查文件大小限制
        max_size_kb = _config.get("parser", {}).get("max_file_size_kb", 500)
        file_size_kb = os.path.getsize(full_path) / 1024
        if file_size_kb > max_size_kb:
            return json.dumps({
                "error": f"文件过大 ({file_size_kb:.1f}KB)，超出限制 ({max_size_kb}KB)"
            }, ensure_ascii=False)

        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            source_code = f.read()

        # 根据文件扩展名判断语言
        ext_lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".java": "java", ".go": "go", ".rs": "rust",
            ".jsx": "javascript", ".tsx": "typescript",
        }
        ext = os.path.splitext(file_path)[1].lower()
        language = ext_lang_map.get(ext)

        if not language:
            return json.dumps({
                "error": f"不支持的文件类型: {ext}",
                "supported": list(ext_lang_map.values()),
            }, ensure_ascii=False)

        result = _parse_with_tree_sitter(source_code, language, analysis_type)
        result["file_path"] = file_path
        result["language"] = language
        result["analysis_type"] = analysis_type

        logger.info("code_ast_analyze: file='%s', type='%s'", file_path, analysis_type)
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("code_ast_analyze 执行失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _parse_with_tree_sitter(source: str, language: str, analysis_type: str) -> dict[str, Any]:
    """使用 tree-sitter 解析代码并提取信息"""
    try:
        from tree_sitter_languages import get_parser
        parser = get_parser(language)
    except ImportError:
        return _fallback_parse(source, language, analysis_type)
    except Exception:
        return _fallback_parse(source, language, analysis_type)

    tree = parser.parse(source.encode("utf-8"))
    root = tree.root_node

    if analysis_type == "structure":
        return _extract_structure(root, source)
    elif analysis_type == "imports":
        return _extract_imports(root, source)
    elif analysis_type == "signatures":
        return _extract_signatures(root, source)
    elif analysis_type == "complexity":
        return _estimate_complexity(root, source)
    else:
        return {"error": f"未知分析类型: {analysis_type}"}


def _extract_structure(root_node, source: str) -> dict[str, Any]:
    """提取代码的类、函数层次结构"""
    classes = []
    functions = []

    def walk(node, depth=0):
        node_type = node.type

        if node_type in ("class_definition", "class_declaration"):
            name = _get_child_text(node, "name", source)
            methods = []
            for child in node.children:
                if child.type in ("function_definition", "method_definition", "method_declaration"):
                    m_name = _get_child_text(child, "name", source)
                    methods.append(m_name)
            classes.append({"name": name, "methods": methods, "line": node.start_point[0] + 1})

        elif node_type in ("function_definition", "function_declaration") and depth == 0:
            name = _get_child_text(node, "name", source)
            functions.append({"name": name, "line": node.start_point[0] + 1})

        for child in node.children:
            walk(child, depth + 1)

    walk(root_node)
    return {"classes": classes, "functions": functions, "total_lines": source.count("\n") + 1}


def _extract_imports(root_node, source: str) -> dict[str, Any]:
    """提取导入语句"""
    imports = []
    for child in root_node.children:
        if child.type in ("import_statement", "import_from_statement",
                          "import_declaration", "import_specifier"):
            text = source[child.start_byte:child.end_byte].strip()
            imports.append(text)
    return {"imports": imports}


def _extract_signatures(root_node, source: str) -> dict[str, Any]:
    """提取函数签名"""
    signatures = []

    def walk(node):
        if node.type in ("function_definition", "function_declaration", "method_definition"):
            line_start = node.start_point[0]
            line_end = node.start_point[0]
            lines = source.split("\n")
            if line_start < len(lines):
                sig = lines[line_start].strip()
                signatures.append({"signature": sig, "line": line_start + 1})
        for child in node.children:
            walk(child)

    walk(root_node)
    return {"signatures": signatures}


def _estimate_complexity(root_node, source: str) -> dict[str, Any]:
    """估算圈复杂度"""
    branch_keywords = {"if_statement", "elif_clause", "for_statement",
                       "while_statement", "except_clause", "with_statement",
                       "conditional_expression", "case_clause"}
    complexity = 1

    def walk(node):
        nonlocal complexity
        if node.type in branch_keywords:
            complexity += 1
        for child in node.children:
            walk(child)

    walk(root_node)
    return {"cyclomatic_complexity": complexity, "total_lines": source.count("\n") + 1}


def _get_child_text(node, field_name: str, source: str) -> str:
    """从 AST 节点获取指定字段的文本"""
    child = node.child_by_field_name(field_name)
    if child:
        return source[child.start_byte:child.end_byte]
    for c in node.children:
        if c.type == "identifier":
            return source[c.start_byte:c.end_byte]
    return "<unknown>"


def _fallback_parse(source: str, language: str, analysis_type: str) -> dict[str, Any]:
    """tree-sitter 不可用时的降级解析（基于正则表达式）"""
    import re

    result: dict[str, Any] = {"fallback": True}
    lines = source.split("\n")

    if language == "python":
        if analysis_type in ("structure", "signatures"):
            classes = []
            functions = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("class "):
                    name = stripped.split("(")[0].split(":")[0].replace("class ", "").strip()
                    classes.append({"name": name, "line": i + 1})
                elif stripped.startswith("def "):
                    name = stripped.split("(")[0].replace("def ", "").strip()
                    functions.append({"name": name, "line": i + 1, "signature": stripped.rstrip(":")})
            result["classes"] = classes
            result["functions"] = functions

        elif analysis_type == "imports":
            imports = [line.strip() for line in lines
                       if line.strip().startswith(("import ", "from "))]
            result["imports"] = imports

        elif analysis_type == "complexity":
            branch_count = sum(1 for line in lines
                               if re.match(r'\s*(if|elif|for|while|except|with)\b', line))
            result["cyclomatic_complexity"] = 1 + branch_count

    result["total_lines"] = len(lines)
    return result


def get_all_tools() -> list:
    """返回所有可用工具列表"""
    return [vector_retrieve, graph_traverse, cypher_query, code_ast_analyze]
