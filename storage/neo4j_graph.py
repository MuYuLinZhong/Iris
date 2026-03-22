"""
=== 文件路径：storage/neo4j_graph.py ===
作用说明：Neo4j 图数据库连接管理与操作封装。
提供节点/关系的 CRUD、Cypher 查询执行、图遍历等核心能力。
"""

import logging
import os
from typing import Any

from neo4j import GraphDatabase, Driver, Session

logger = logging.getLogger(__name__)


class Neo4jGraph:
    """Neo4j 图数据库操作封装类"""

    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        self._uri = uri
        self._username = username
        # 支持环境变量引用（如 ${NEO4J_PASSWORD}）
        self._password = self._resolve_env(password)
        self._database = database
        self._driver: Driver | None = None

    @staticmethod
    def _resolve_env(value: str) -> str:
        """解析环境变量引用，如 ${VAR_NAME}"""
        if value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            return os.getenv(env_key, "")
        return value

    def connect(self) -> None:
        """建立 Neo4j 连接"""
        try:
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._username, self._password),
            )
            self._driver.verify_connectivity()
            logger.info("Neo4j 连接成功: %s", self._uri)
        except Exception as e:
            logger.error("Neo4j 连接失败: %s", e)
            raise

    def close(self) -> None:
        """关闭连接"""
        if self._driver:
            self._driver.close()
            logger.info("Neo4j 连接已关闭")

    def _get_session(self) -> Session:
        """获取数据库会话"""
        if not self._driver:
            raise RuntimeError("Neo4j 未连接，请先调用 connect()")
        return self._driver.session(database=self._database)

    def health_check(self) -> bool:
        """健康检查：验证连接是否可用"""
        try:
            with self._get_session() as session:
                session.run("RETURN 1")
            return True
        except Exception as e:
            logger.warning("Neo4j 健康检查失败: %s", e)
            return False

    # ----------------------------------------------------------------
    # 节点操作
    # ----------------------------------------------------------------

    def create_node(self, label: str, properties: dict[str, Any]) -> dict[str, Any]:
        """
        创建单个节点。
        返回创建后的节点属性（含内部 id）。
        """
        props_str = ", ".join(f"{k}: ${k}" for k in properties)
        query = f"CREATE (n:{label} {{{props_str}}}) RETURN n, elementId(n) AS eid"
        with self._get_session() as session:
            result = session.run(query, **properties)
            record = result.single()
            if record:
                node_data = dict(record["n"])
                node_data["_element_id"] = record["eid"]
                return node_data
        return {}

    def merge_node(self, label: str, match_keys: dict[str, Any],
                   set_properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        MERGE 节点：存在则更新，不存在则创建。
        match_keys: 用于匹配的属性
        set_properties: 额外设置的属性
        """
        match_str = ", ".join(f"{k}: $match_{k}" for k in match_keys)
        params: dict[str, Any] = {f"match_{k}": v for k, v in match_keys.items()}

        query = f"MERGE (n:{label} {{{match_str}}})"
        if set_properties:
            set_str = ", ".join(f"n.{k} = $set_{k}" for k in set_properties)
            query += f" SET {set_str}"
            params.update({f"set_{k}": v for k, v in set_properties.items()})
        query += " RETURN n, elementId(n) AS eid"

        with self._get_session() as session:
            result = session.run(query, **params)
            record = result.single()
            if record:
                node_data = dict(record["n"])
                node_data["_element_id"] = record["eid"]
                return node_data
        return {}

    def delete_node_by_property(self, label: str, key: str, value: Any) -> int:
        """根据属性删除节点及其所有关系，返回删除数量"""
        query = f"MATCH (n:{label} {{{key}: $value}}) DETACH DELETE n RETURN count(n) AS cnt"
        with self._get_session() as session:
            result = session.run(query, value=value)
            record = result.single()
            return record["cnt"] if record else 0

    # ----------------------------------------------------------------
    # 关系操作
    # ----------------------------------------------------------------

    def create_relationship(
        self,
        from_label: str, from_key: str, from_value: Any,
        to_label: str, to_key: str, to_value: Any,
        rel_type: str, properties: dict[str, Any] | None = None,
    ) -> bool:
        """
        在两个已存在的节点之间创建关系。
        使用 MERGE 避免重复关系。
        """
        props_str = ""
        params: dict[str, Any] = {"from_val": from_value, "to_val": to_value}
        if properties:
            props_str = " {" + ", ".join(f"{k}: $rel_{k}" for k in properties) + "}"
            params.update({f"rel_{k}": v for k, v in properties.items()})

        query = (
            f"MATCH (a:{from_label} {{{from_key}: $from_val}}), "
            f"(b:{to_label} {{{to_key}: $to_val}}) "
            f"MERGE (a)-[r:{rel_type}{props_str}]->(b) "
            f"RETURN type(r) AS rtype"
        )
        with self._get_session() as session:
            result = session.run(query, **params)
            return result.single() is not None

    # ----------------------------------------------------------------
    # 查询操作
    # ----------------------------------------------------------------

    def execute_cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """执行任意 Cypher 查询，返回结果列表"""
        with self._get_session() as session:
            result = session.run(query, **(params or {}))
            return [dict(record) for record in result]

    def traverse(
        self,
        start_label: str,
        start_key: str,
        start_value: Any,
        relationship_types: list[str] | None = None,
        max_depth: int = 3,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """
        从指定节点出发，沿关系遍历图。
        direction: "out" / "in" / "both"
        返回路径上的所有节点和关系信息。
        """
        rel_filter = "|".join(relationship_types) if relationship_types else ""
        rel_pattern = f":{rel_filter}" if rel_filter else ""

        if direction == "out":
            path_pattern = f"-[r{rel_pattern}*1..{max_depth}]->"
        elif direction == "in":
            path_pattern = f"<-[r{rel_pattern}*1..{max_depth}]-"
        else:
            path_pattern = f"-[r{rel_pattern}*1..{max_depth}]-"

        query = (
            f"MATCH p=(start:{start_label} {{{start_key}: $start_val}})"
            f"{path_pattern}(end) "
            f"RETURN [n IN nodes(p) | properties(n)] AS nodes, "
            f"[r IN relationships(p) | {{type: type(r), props: properties(r)}}] AS rels "
            f"LIMIT 50"
        )
        try:
            with self._get_session() as session:
                result = session.run(query, start_val=start_value)
                paths = []
                for record in result:
                    paths.append({
                        "nodes": record["nodes"],
                        "relationships": record["rels"],
                    })
                return paths
        except Exception as e:
            logger.error("图遍历失败: %s", e)
            return []

    def get_node_neighbors(self, label: str, key: str, value: Any, depth: int = 1) -> list[dict[str, Any]]:
        """获取节点的直接邻居"""
        query = (
            f"MATCH (n:{label} {{{key}: $value}})-[r]-(neighbor) "
            f"RETURN properties(neighbor) AS neighbor, type(r) AS rel_type, "
            f"labels(neighbor) AS labels "
            f"LIMIT 100"
        )
        with self._get_session() as session:
            result = session.run(query, value=value)
            return [dict(record) for record in result]

    def get_shortest_path(
        self,
        from_label: str, from_key: str, from_value: Any,
        to_label: str, to_key: str, to_value: Any,
    ) -> list[dict[str, Any]]:
        """查找两个节点之间的最短路径"""
        query = (
            f"MATCH p=shortestPath("
            f"(a:{from_label} {{{from_key}: $from_val}})-[*..10]-"
            f"(b:{to_label} {{{to_key}: $to_val}}))"
            f"RETURN [n IN nodes(p) | properties(n)] AS nodes, "
            f"[r IN relationships(p) | {{type: type(r)}}] AS rels"
        )
        with self._get_session() as session:
            result = session.run(query, from_val=from_value, to_val=to_value)
            record = result.single()
            if record:
                return [{"nodes": record["nodes"], "relationships": record["rels"]}]
            return []

    # ----------------------------------------------------------------
    # 索引与约束
    # ----------------------------------------------------------------

    def create_indexes(self) -> None:
        """创建必要的索引以加速查询"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS FOR (n:Repository) ON (n.name)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Module) ON (n.path)",
            "CREATE INDEX IF NOT EXISTS FOR (n:File) ON (n.path)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Class) ON (n.name)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Function) ON (n.name)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Community) ON (n.id)",
        ]
        with self._get_session() as session:
            for idx_query in indexes:
                session.run(idx_query)
        logger.info("Neo4j 索引创建完成（共 %d 个）", len(indexes))

    def clear_database(self) -> None:
        """清空数据库中所有节点和关系（危险操作）"""
        with self._get_session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.warning("Neo4j 数据库已清空")
