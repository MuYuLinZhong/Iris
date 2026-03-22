"""
=== 文件路径：core/explainer_agent.py ===
作用说明：LangGraph Agent 主逻辑。
完整实现 StateGraph（Router → Tool Executor → Reflection → 循环或 Final Answer）。
这是 Iris 的大脑——Agentic + GraphRAG 的核心编排。
"""

import json
import logging
from typing import Annotated, Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END, add_messages

from utils.prompt_templates import (
    ROUTER_PROMPT,
    TOOL_EXECUTOR_PROMPT,
    REFLECTION_PROMPT,
    GENERATE_ANSWER_PROMPT,
)
from utils.tools import get_all_tools
from utils.visualize import generate_trace_mermaid

logger = logging.getLogger(__name__)


# ================================================================
# Agent State 定义
# ================================================================

class AgentState(dict):
    """
    LangGraph Agent 的完整状态定义。
    使用 dict 子类以兼容 LangGraph 的 state 管理。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    query_type: str  # local_detail / cross_module / global_risk / evolution
    selected_tools: list[str]
    vector_results: list[dict]
    graph_results: list[dict]
    cypher_results: list[dict]
    ast_results: list[dict]
    retrieved_context: str
    critique: str
    reflection_passed: bool
    reflection_count: int
    max_reflections: int
    final_answer: str
    module_priority: str
    nodes_visited: list[str]
    tools_used: list[str]


# Agent State 的 schema（供 StateGraph 使用）
AGENT_STATE_SCHEMA = {
    "messages": Annotated[list[BaseMessage], add_messages],
    "user_query": str,
    "query_type": str,
    "selected_tools": list,
    "vector_results": list,
    "graph_results": list,
    "cypher_results": list,
    "ast_results": list,
    "retrieved_context": str,
    "critique": str,
    "reflection_passed": bool,
    "reflection_count": int,
    "max_reflections": int,
    "final_answer": str,
    "module_priority": str,
    "nodes_visited": list,
    "tools_used": list,
}


# ================================================================
# Agent 构建器
# ================================================================

class ExplainerAgent:
    """Iris 智能问答 Agent，基于 LangGraph StateGraph 实现"""

    def __init__(self, llm: BaseChatModel, config: dict[str, Any]):
        self._llm = llm
        self._config = config
        self._agent_config = config.get("agent", {})
        self._graph = self._build_graph()
        self._compiled = self._graph.compile()

    @property
    def compiled_graph(self):
        """返回编译后的图（供可视化使用）"""
        return self._compiled

    # ----------------------------------------------------------------
    # 构建 StateGraph
    # ----------------------------------------------------------------

    def _build_graph(self) -> StateGraph:
        """构建完整的 LangGraph StateGraph"""

        graph = StateGraph(dict)

        # 注册所有节点
        graph.add_node("router", self._router_node)
        graph.add_node("tool_executor", self._tool_executor_node)
        graph.add_node("reflection", self._reflection_node)
        graph.add_node("generate", self._generate_node)

        # 设置入口
        graph.set_entry_point("router")

        # 边：Router → Tool Executor（总是）
        graph.add_edge("router", "tool_executor")

        # 边：Tool Executor → Reflection（总是）
        graph.add_edge("tool_executor", "reflection")

        # 条件边：Reflection → 继续循环 or 生成最终回答
        graph.add_conditional_edges(
            "reflection",
            self._should_continue,
            {
                "continue": "tool_executor",
                "generate": "generate",
            },
        )

        # 边：Generate → END
        graph.add_edge("generate", END)

        logger.info("LangGraph StateGraph 构建完成（4 节点 + 条件边）")
        return graph

    # ----------------------------------------------------------------
    # 节点实现
    # ----------------------------------------------------------------

    def _router_node(self, state: dict) -> dict:
        """
        Router 节点：分析用户问题，分类问题类型，选择工具组合。
        """
        user_query = state.get("user_query", "")
        logger.info("[Router] 处理问题: %s", user_query[:100])

        prompt = ROUTER_PROMPT.format(user_query=user_query)

        try:
            response = self._llm.invoke(prompt)
            content = response.content

            # 解析 LLM 返回的 JSON
            result = self._parse_json_response(content)

            query_type = result.get("query_type", "local_detail")
            suggested_tools = result.get("suggested_tools", ["vector_retrieve"])

            logger.info("[Router] 分类结果: type=%s, tools=%s", query_type, suggested_tools)

        except Exception as e:
            logger.error("[Router] 分类失败，使用默认值: %s", e)
            query_type = "local_detail"
            suggested_tools = ["vector_retrieve", "graph_traverse"]

        nodes_visited = state.get("nodes_visited", [])
        nodes_visited.append("router")

        return {
            **state,
            "query_type": query_type,
            "selected_tools": suggested_tools,
            "messages": [AIMessage(content=f"[Router] 问题类型: {query_type}")],
            "nodes_visited": nodes_visited,
        }

    def _tool_executor_node(self, state: dict) -> dict:
        """
        Tool Executor 节点：根据 Router 的指令调用工具并聚合结果。
        """
        user_query = state.get("user_query", "")
        selected_tools = state.get("selected_tools", [])
        query_type = state.get("query_type", "")
        module_priority = state.get("module_priority", "medium")
        critique = state.get("critique", "")

        logger.info("[ToolExecutor] 调用工具: %s", selected_tools)

        retry_context = ""
        if critique:
            retry_context = f"上一轮 Reflection 的反馈：\n{critique}\n请根据反馈调整检索策略。"

        # 准备工具调用
        prompt = TOOL_EXECUTOR_PROMPT.format(
            user_query=user_query,
            query_type=query_type,
            module_priority=module_priority,
            selected_tools=", ".join(selected_tools),
            retry_context=retry_context,
        )

        all_tools = {t.name: t for t in get_all_tools()}
        tools_used = state.get("tools_used", [])

        vector_results = state.get("vector_results", [])
        graph_results = state.get("graph_results", [])
        cypher_results = state.get("cypher_results", [])
        ast_results = state.get("ast_results", [])

        # 执行选定的工具
        for tool_name in selected_tools:
            if tool_name not in all_tools:
                logger.warning("未知工具: %s", tool_name)
                continue

            tool_fn = all_tools[tool_name]
            if tool_name not in tools_used:
                tools_used.append(tool_name)

            try:
                if tool_name == "vector_retrieve":
                    result = tool_fn.invoke({"query": user_query, "top_k": 10})
                    vector_results = self._safe_parse_list(result)
                elif tool_name == "graph_traverse":
                    keywords = self._extract_keywords(user_query)
                    for kw in keywords[:3]:
                        result = tool_fn.invoke({
                            "start_node": kw,
                            "max_depth": self._agent_config.get("graph_max_depth", 3),
                        })
                        graph_results.extend(self._safe_parse_list(result))
                elif tool_name == "cypher_query":
                    result = tool_fn.invoke({"natural_language_query": user_query})
                    cypher_results = self._safe_parse_list(result)
                elif tool_name == "code_ast_analyze":
                    file_paths = self._extract_file_paths(user_query, vector_results)
                    for fp in file_paths[:3]:
                        result = tool_fn.invoke({"file_path": fp, "analysis_type": "structure"})
                        ast_results.append(self._safe_parse_dict(result))
            except Exception as e:
                logger.error("[ToolExecutor] 工具 %s 执行失败: %s", tool_name, e)

        # 聚合上下文
        context_parts = []
        if vector_results:
            context_parts.append("【向量检索结果】\n" + json.dumps(vector_results[:5], ensure_ascii=False, indent=2, default=str))
        if graph_results:
            context_parts.append("【图遍历结果】\n" + json.dumps(graph_results[:5], ensure_ascii=False, indent=2, default=str))
        if cypher_results:
            context_parts.append("【Cypher 查询结果】\n" + json.dumps(cypher_results[:5], ensure_ascii=False, indent=2, default=str))
        if ast_results:
            context_parts.append("【AST 分析结果】\n" + json.dumps(ast_results[:3], ensure_ascii=False, indent=2, default=str))

        retrieved_context = "\n\n".join(context_parts) if context_parts else "未检索到相关信息。"

        nodes_visited = state.get("nodes_visited", [])
        nodes_visited.append("tool_executor")

        return {
            **state,
            "vector_results": vector_results,
            "graph_results": graph_results,
            "cypher_results": cypher_results,
            "ast_results": ast_results,
            "retrieved_context": retrieved_context,
            "tools_used": tools_used,
            "messages": [AIMessage(content=f"[ToolExecutor] 已调用 {len(selected_tools)} 个工具")],
            "nodes_visited": nodes_visited,
        }

    def _reflection_node(self, state: dict) -> dict:
        """
        Reflection 节点：评估工具检索结果的完整性、一致性、可溯源性。
        决定是否需要重新检索。
        """
        user_query = state.get("user_query", "")
        query_type = state.get("query_type", "")
        retrieved_context = state.get("retrieved_context", "")
        reflection_count = state.get("reflection_count", 0)

        logger.info("[Reflection] 第 %d 轮反思", reflection_count + 1)

        prompt = REFLECTION_PROMPT.format(
            user_query=user_query,
            query_type=query_type,
            retrieved_context=retrieved_context[:4000],
        )

        try:
            response = self._llm.invoke(prompt)
            result = self._parse_json_response(response.content)

            passed = result.get("passed", True)
            critique = result.get("critique", "")
            retry_tools = result.get("suggested_retry_tools", [])

            logger.info("[Reflection] 通过=%s, 评价: %s", passed, critique[:100])

        except Exception as e:
            logger.error("[Reflection] 评估失败，默认通过: %s", e)
            passed = True
            critique = "反思模块异常，默认通过"
            retry_tools = []

        nodes_visited = state.get("nodes_visited", [])
        nodes_visited.append("reflection")

        new_state = {
            **state,
            "reflection_passed": passed,
            "critique": critique,
            "reflection_count": reflection_count + 1,
            "messages": [AIMessage(content=f"[Reflection] {'通过' if passed else '需要重试'}: {critique[:200]}")],
            "nodes_visited": nodes_visited,
        }

        if not passed and retry_tools:
            new_state["selected_tools"] = retry_tools

        return new_state

    def _generate_node(self, state: dict) -> dict:
        """
        Generate 节点：基于所有检索结果生成最终结构化回答。
        """
        user_query = state.get("user_query", "")
        query_type = state.get("query_type", "")
        module_priority = state.get("module_priority", "medium")
        retrieved_context = state.get("retrieved_context", "")
        response_language = self._agent_config.get("response_language", "zh-CN")

        logger.info("[Generate] 生成最终回答")

        prompt = GENERATE_ANSWER_PROMPT.format(
            user_query=user_query,
            query_type=query_type,
            module_priority=module_priority,
            retrieved_context=retrieved_context[:6000],
            response_language=response_language,
        )

        try:
            response = self._llm.invoke(prompt)
            final_answer = response.content
        except Exception as e:
            logger.error("[Generate] 生成失败: %s", e)
            final_answer = f"抱歉，生成回答时发生错误：{str(e)}"

        nodes_visited = state.get("nodes_visited", [])
        nodes_visited.append("generate")

        return {
            **state,
            "final_answer": final_answer,
            "messages": [AIMessage(content=final_answer)],
            "nodes_visited": nodes_visited,
        }

    # ----------------------------------------------------------------
    # 条件判断（Conditional Edge）
    # ----------------------------------------------------------------

    def _should_continue(self, state: dict) -> Literal["continue", "generate"]:
        """
        Reflection 后的条件判断：
        - 如果反思通过 或 已达最大循环次数 → 生成回答
        - 否则 → 继续循环（重新执行工具）
        """
        reflection_passed = state.get("reflection_passed", True)
        reflection_count = state.get("reflection_count", 0)
        max_reflections = state.get("max_reflections",
                                     self._agent_config.get("max_reflections", 3))

        if reflection_passed:
            logger.info("[Condition] 反思通过 → 生成回答")
            return "generate"

        if reflection_count >= max_reflections:
            logger.warning("[Condition] 已达最大反思次数 %d → 强制生成回答", max_reflections)
            return "generate"

        logger.info("[Condition] 反思未通过 → 继续循环（第 %d/%d 轮）",
                     reflection_count, max_reflections)
        return "continue"

    # ----------------------------------------------------------------
    # 执行入口
    # ----------------------------------------------------------------

    async def query(self, question: str, module_priority: str = "medium") -> dict[str, Any]:
        """
        执行一次完整的 Agent 问答流程。

        Args:
            question: 用户问题
            module_priority: 模块优先级 (high/medium/low)

        Returns:
            包含 answer, trace, sources 的结果字典
        """
        initial_state = {
            "messages": [HumanMessage(content=question)],
            "user_query": question,
            "query_type": "",
            "selected_tools": [],
            "vector_results": [],
            "graph_results": [],
            "cypher_results": [],
            "ast_results": [],
            "retrieved_context": "",
            "critique": "",
            "reflection_passed": False,
            "reflection_count": 0,
            "max_reflections": self._agent_config.get("max_reflections", 3),
            "final_answer": "",
            "module_priority": module_priority,
            "nodes_visited": [],
            "tools_used": [],
        }

        logger.info("Agent 开始处理: '%s'", question[:100])

        final_state = await self._run_graph(initial_state)

        nodes_visited = final_state.get("nodes_visited", [])
        tools_used = final_state.get("tools_used", [])

        trace_mermaid = generate_trace_mermaid(nodes_visited, tools_used)

        return {
            "answer": final_state.get("final_answer", "未能生成回答"),
            "trace": {
                "nodes_visited": nodes_visited,
                "tools_used": tools_used,
                "query_type": final_state.get("query_type", ""),
                "reflection_count": final_state.get("reflection_count", 0),
                "mermaid_trace": trace_mermaid,
            },
            "sources": self._extract_sources(final_state),
        }

    async def _run_graph(self, initial_state: dict) -> dict:
        """运行编译后的 StateGraph"""
        try:
            final_state = initial_state
            async for output in self._compiled.astream(initial_state):
                for node_name, node_state in output.items():
                    if isinstance(node_state, dict):
                        final_state.update(node_state)
            return final_state
        except Exception as e:
            logger.error("Agent 执行失败: %s", e)
            return {**initial_state, "final_answer": f"Agent 执行失败: {str(e)}"}

    # ----------------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------------

    @staticmethod
    def _parse_json_response(content: str) -> dict:
        """从 LLM 响应中解析 JSON（兼容 markdown 代码块）"""
        text = content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        # 尝试找到 JSON 对象
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return {}

    @staticmethod
    def _extract_keywords(query: str) -> list[str]:
        """从查询中提取关键词（用于图遍历的起始节点）"""
        import re
        words = re.findall(r'[a-zA-Z_]\w+', query)
        chinese_terms = re.findall(r'[\u4e00-\u9fa5]+', query)
        keywords = [w for w in words if len(w) > 2 and w.lower() not in {
            "the", "this", "that", "what", "how", "why", "from", "with",
            "for", "and", "not", "are", "was", "has", "have",
        }]
        return keywords[:5]

    @staticmethod
    def _extract_file_paths(query: str, vector_results: list) -> list[str]:
        """从查询和向量结果中提取文件路径"""
        import re
        paths = re.findall(r'[\w/]+\.(?:py|js|ts|java|go|rs)', query)

        if not paths and vector_results:
            for r in vector_results[:3]:
                if isinstance(r, dict):
                    meta = r.get("metadata", {})
                    if isinstance(meta, dict) and meta.get("file_path"):
                        paths.append(meta["file_path"])

        return paths

    @staticmethod
    def _extract_sources(state: dict) -> list[dict]:
        """从最终状态中提取引用的源文件"""
        sources = []
        seen = set()

        for result in state.get("vector_results", []):
            if isinstance(result, dict):
                meta = result.get("metadata", {})
                if isinstance(meta, dict):
                    fp = meta.get("file_path", "")
                    if fp and fp not in seen:
                        sources.append({"file": fp, "type": "vector_search"})
                        seen.add(fp)

        return sources[:10]

    @staticmethod
    def _safe_parse_list(raw: str) -> list:
        """安全解析 JSON 列表"""
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, list) else [data]
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _safe_parse_dict(raw: str) -> dict:
        """安全解析 JSON 字典"""
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, dict) else {"raw": data}
        except (json.JSONDecodeError, TypeError):
            return {"raw": str(raw)}
