"""
=== 文件路径：utils/visualize.py ===
作用说明：LangGraph 可视化辅助函数。
提供 Agent StateGraph 的 Mermaid 图生成、HTML 渲染、PNG 导出功能。
用于 /visualize-agent 端点和设计文档嵌入。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mermaid.js CDN HTML 模板
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Iris Agent Graph</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 40px 20px;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
            margin: 0;
        }}
        h1 {{
            font-size: 1.8rem;
            margin-bottom: 8px;
            background: linear-gradient(135deg, #4ECDC4, #45B7D1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            color: #94a3b8;
            margin-bottom: 32px;
        }}
        .mermaid {{
            background: #1e293b;
            border-radius: 12px;
            padding: 32px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.3);
            max-width: 90vw;
            overflow-x: auto;
        }}
        .footer {{
            margin-top: 32px;
            color: #64748b;
            font-size: 0.85rem;
        }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>
        mermaid.initialize({{
            startOnLoad: true,
            theme: 'dark',
            themeVariables: {{
                primaryColor: '#4ECDC4',
                primaryTextColor: '#0f172a',
                lineColor: '#64748b',
                secondaryColor: '#45B7D1',
                tertiaryColor: '#1e293b'
            }}
        }});
    </script>
</head>
<body>
    <h1>Iris Agent Graph</h1>
    <p class="subtitle">Agentic + GraphRAG 智能问答流程</p>
    <div class="mermaid">
{mermaid_code}
    </div>
    <p class="footer">Iris v1.0 — 由 LangGraph StateGraph 驱动</p>
</body>
</html>"""


def visualize_graph(graph: Any, output_format: str = "mermaid") -> str | bytes:
    """
    将 LangGraph StateGraph 转换为可视化输出。

    Args:
        graph: 编译后的 LangGraph StateGraph（CompiledGraph）
        output_format:
            - "mermaid": 返回 Mermaid 文本字符串
            - "mermaid_png": 返回 PNG 图片字节
            - "html": 返回完整 HTML 页面字符串

    Returns:
        str（mermaid/html）或 bytes（png）
    """
    try:
        drawable = graph.get_graph()
    except Exception:
        drawable = graph

    if output_format == "mermaid":
        return _generate_mermaid(drawable)
    elif output_format == "mermaid_png":
        return _generate_png(drawable)
    elif output_format == "html":
        return _generate_html(drawable)
    else:
        raise ValueError(f"不支持的输出格式: {output_format}")


def _generate_mermaid(drawable: Any) -> str:
    """生成 Mermaid 语法文本"""
    try:
        mermaid_code = drawable.draw_mermaid()
        logger.info("Mermaid 图生成成功")
        return mermaid_code
    except AttributeError:
        logger.warning("draw_mermaid() 不可用，使用静态 fallback")
        return _fallback_mermaid()


def _generate_png(drawable: Any) -> bytes:
    """生成 PNG 图片字节"""
    try:
        png_bytes = drawable.draw_mermaid_png()
        logger.info("PNG 图生成成功，大小: %d bytes", len(png_bytes))
        return png_bytes
    except Exception as e:
        logger.error("PNG 生成失败（可能需要 pyppeteer）: %s", e)
        raise RuntimeError(
            "PNG 生成失败。请确保已安装 pyppeteer: pip install pyppeteer"
        ) from e


def _generate_html(drawable: Any) -> str:
    """生成包含 Mermaid.js 的完整 HTML 页面"""
    mermaid_code = _generate_mermaid(drawable)
    return _HTML_TEMPLATE.format(mermaid_code=mermaid_code)


def _fallback_mermaid() -> str:
    """当 LangGraph draw_mermaid() 不可用时的静态 Mermaid 图"""
    return """graph TD
    Start([用户提问]) --> Router

    Router{Router Agent<br/>问题分类}
    Router -->|局部细节| ToolExec[Tool Executor]
    Router -->|跨模块关系| ToolExec
    Router -->|全局风险| ToolExec
    Router -->|演进历史| ToolExec

    ToolExec --> Tools[[工具层<br/>vector_retrieve<br/>graph_traverse<br/>cypher_query<br/>code_ast_analyze]]

    Tools --> Aggregate[结果聚合]
    Aggregate --> Reflection{Reflection Node<br/>自我反思}

    Reflection -->|结果完整且一致| Generate[生成最终回答]
    Reflection -->|不完整/有矛盾| Rewrite[重写查询]
    Rewrite --> ToolExec

    Generate --> End([返回结构化回答])

    style Router fill:#4ECDC4,stroke:#333,color:#000
    style Reflection fill:#FF6B6B,stroke:#333,color:#000
    style Tools fill:#45B7D1,stroke:#333,color:#000
    style Generate fill:#96CEB4,stroke:#333,color:#000"""


def generate_trace_mermaid(nodes_visited: list[str], tools_used: list[str]) -> str:
    """
    根据单次查询的实际执行轨迹生成 Mermaid 图。
    高亮实际经过的节点和边。

    Args:
        nodes_visited: 实际访问的节点列表（按顺序）
        tools_used: 实际调用的工具列表

    Returns:
        Mermaid 语法字符串
    """
    lines = ["graph TD"]

    # 根据实际执行轨迹构建节点和边
    for i, node in enumerate(nodes_visited):
        node_id = f"N{i}"
        display_name = _node_display_name(node)

        if node == "router":
            lines.append(f"    {node_id}{{{display_name}}}")
        elif node == "reflection":
            lines.append(f"    {node_id}{{{display_name}}}")
        elif node == "tool_executor":
            tools_str = ", ".join(tools_used) if tools_used else "tools"
            lines.append(f"    {node_id}[{display_name}<br/>{tools_str}]")
        elif node == "generate":
            lines.append(f"    {node_id}[{display_name}]")
        else:
            lines.append(f"    {node_id}[{display_name}]")

        if i > 0:
            prev_id = f"N{i - 1}"
            lines.append(f"    {prev_id} --> {node_id}")

    # 样式
    for i, node in enumerate(nodes_visited):
        node_id = f"N{i}"
        if node == "router":
            lines.append(f"    style {node_id} fill:#4ECDC4,stroke:#333,color:#000")
        elif node == "reflection":
            lines.append(f"    style {node_id} fill:#FF6B6B,stroke:#333,color:#000")
        elif node == "generate":
            lines.append(f"    style {node_id} fill:#96CEB4,stroke:#333,color:#000")
        else:
            lines.append(f"    style {node_id} fill:#45B7D1,stroke:#333,color:#000")

    return "\n".join(lines)


def _node_display_name(node_name: str) -> str:
    """将节点内部名称转为可读的显示名称"""
    names = {
        "router": "Router Agent",
        "tool_executor": "Tool Executor",
        "reflection": "Reflection",
        "generate": "Generate Answer",
    }
    return names.get(node_name, node_name)
