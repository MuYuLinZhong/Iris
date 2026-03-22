"""
=== 文件路径：storage/chroma_db.py ===
作用说明：Chroma 向量数据库连接管理与操作封装。
提供代码片段和摘要的向量化存储、语义相似度检索能力。
"""

import logging
from typing import Any

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


class ChromaDB:
    """Chroma 向量数据库操作封装类"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8000,
        collection_name: str = "iris_code",
        persist_directory: str = "./data/chroma",
    ):
        self._host = host
        self._port = port
        self._collection_name = collection_name
        self._persist_directory = persist_directory
        self._client: chromadb.ClientAPI | None = None
        self._collection: chromadb.Collection | None = None

    def connect(self) -> None:
        """连接到 Chroma 服务（HTTP 模式，适配 Docker 部署）"""
        try:
            self._client = chromadb.HttpClient(
                host=self._host,
                port=self._port,
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "Chroma 连接成功: %s:%s, collection=%s",
                self._host, self._port, self._collection_name,
            )
        except Exception as e:
            logger.error("Chroma 连接失败: %s", e)
            raise

    def health_check(self) -> bool:
        """健康检查"""
        try:
            if self._client:
                self._client.heartbeat()
                return True
        except Exception as e:
            logger.warning("Chroma 健康检查失败: %s", e)
        return False

    @property
    def collection(self) -> chromadb.Collection:
        if not self._collection:
            raise RuntimeError("Chroma 未连接，请先调用 connect()")
        return self._collection

    # ----------------------------------------------------------------
    # 写入操作
    # ----------------------------------------------------------------

    def add_documents(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        embeddings: list[list[float]] | None = None,
    ) -> None:
        """
        批量写入文档。
        如果不提供 embeddings，Chroma 会使用默认嵌入模型自动生成。
        """
        kwargs: dict[str, Any] = {
            "ids": ids,
            "documents": documents,
        }
        if metadatas:
            kwargs["metadatas"] = metadatas
        if embeddings:
            kwargs["embeddings"] = embeddings

        self.collection.add(**kwargs)
        logger.info("已写入 %d 条文档到 Chroma", len(ids))

    def upsert_documents(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        embeddings: list[list[float]] | None = None,
    ) -> None:
        """批量写入或更新文档（存在则更新，不存在则插入）"""
        kwargs: dict[str, Any] = {
            "ids": ids,
            "documents": documents,
        }
        if metadatas:
            kwargs["metadatas"] = metadatas
        if embeddings:
            kwargs["embeddings"] = embeddings

        self.collection.upsert(**kwargs)
        logger.info("已 upsert %d 条文档到 Chroma", len(ids))

    # ----------------------------------------------------------------
    # 查询操作
    # ----------------------------------------------------------------

    def query(
        self,
        query_texts: list[str],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        语义相似度检索。
        返回 Chroma 原始结果字典，包含 ids, documents, metadatas, distances。
        """
        kwargs: dict[str, Any] = {
            "query_texts": query_texts,
            "n_results": n_results,
        }
        if where:
            kwargs["where"] = where
        if where_document:
            kwargs["where_document"] = where_document

        results = self.collection.query(**kwargs)
        return results

    def query_with_embedding(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """使用预计算的嵌入向量进行检索"""
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
        }
        if where:
            kwargs["where"] = where

        return self.collection.query(**kwargs)

    # ----------------------------------------------------------------
    # 删除操作
    # ----------------------------------------------------------------

    def delete_by_ids(self, ids: list[str]) -> None:
        """根据 ID 列表删除文档"""
        self.collection.delete(ids=ids)
        logger.info("已从 Chroma 删除 %d 条文档", len(ids))

    def delete_by_metadata(self, where: dict[str, Any]) -> None:
        """根据 metadata 条件删除文档"""
        self.collection.delete(where=where)
        logger.info("已按条件删除 Chroma 文档: %s", where)

    # ----------------------------------------------------------------
    # 统计
    # ----------------------------------------------------------------

    def count(self) -> int:
        """返回 collection 中的文档总数"""
        return self.collection.count()

    def get_by_ids(self, ids: list[str]) -> dict[str, Any]:
        """根据 ID 获取文档"""
        return self.collection.get(ids=ids)

    def reset_collection(self) -> None:
        """重置 collection（删除后重建）"""
        if self._client:
            self._client.delete_collection(self._collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.warning("Chroma collection '%s' 已重置", self._collection_name)
