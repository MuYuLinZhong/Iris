"""
=== 文件路径：core/repo_manager.py ===
作用说明：Git 仓库管理器。
使用 GitPython 实现仓库克隆、版本切换、PR diff 获取、文件遍历等操作。
支持 GitCode 和 GitHub 两个平台。
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import git

logger = logging.getLogger(__name__)


class RepoManager:
    """Git 仓库管理器"""

    def __init__(self, clone_directory: str, platforms: dict[str, Any] | None = None):
        self._clone_dir = Path(clone_directory)
        self._clone_dir.mkdir(parents=True, exist_ok=True)
        self._platforms = platforms or {}
        self._repo: git.Repo | None = None
        self._repo_path: Path | None = None

    @property
    def repo_path(self) -> str:
        """返回当前仓库的本地路径"""
        if not self._repo_path:
            raise RuntimeError("尚未克隆任何仓库")
        return str(self._repo_path)

    @property
    def repo(self) -> git.Repo:
        if not self._repo:
            raise RuntimeError("尚未克隆任何仓库")
        return self._repo

    # ----------------------------------------------------------------
    # 克隆与版本管理
    # ----------------------------------------------------------------

    def clone_repo(self, repo_url: str, version: str | None = None) -> str:
        """
        克隆远程仓库到本地。若已存在则复用。

        Args:
            repo_url: 仓库 URL
            version: tag 或 commit SHA（可选）

        Returns:
            本地仓库路径
        """
        repo_name = self._extract_repo_name(repo_url)
        local_path = self._clone_dir / repo_name

        if local_path.exists():
            logger.info("仓库已存在，复用本地副本: %s", local_path)
            self._repo = git.Repo(str(local_path))
        else:
            logger.info("开始克隆仓库: %s -> %s", repo_url, local_path)
            auth_url = self._inject_auth_token(repo_url)
            try:
                self._repo = git.Repo.clone_from(auth_url, str(local_path))
                logger.info("仓库克隆完成: %s", repo_name)
            except git.GitCommandError as e:
                logger.error("克隆失败: %s", e)
                raise

        self._repo_path = local_path

        if version:
            self.checkout_version(version)

        return str(local_path)

    def checkout_version(self, version: str) -> None:
        """切换到指定版本（tag 或 commit SHA）"""
        try:
            self.repo.git.checkout(version)
            logger.info("已切换到版本: %s", version)
        except git.GitCommandError as e:
            logger.error("版本切换失败: %s", e)
            raise

    # ----------------------------------------------------------------
    # PR Diff 处理
    # ----------------------------------------------------------------

    def get_pr_changed_files(self, base_sha: str, head_sha: str) -> list[dict[str, Any]]:
        """
        获取两个 commit 之间的文件变更列表。

        Returns:
            变更文件列表，每项包含 path, change_type (A/M/D/R), diff_content
        """
        try:
            base_commit = self.repo.commit(base_sha)
            head_commit = self.repo.commit(head_sha)
            diffs = base_commit.diff(head_commit)

            changed_files = []
            for diff in diffs:
                change_type = "M"
                if diff.new_file:
                    change_type = "A"
                elif diff.deleted_file:
                    change_type = "D"
                elif diff.renamed_file:
                    change_type = "R"

                file_path = diff.b_path or diff.a_path
                diff_content = ""
                try:
                    diff_content = diff.diff.decode("utf-8", errors="ignore") if diff.diff else ""
                except Exception:
                    pass

                changed_files.append({
                    "path": file_path,
                    "change_type": change_type,
                    "old_path": diff.a_path,
                    "new_path": diff.b_path,
                    "diff_content": diff_content,
                })

            logger.info("PR diff: %s..%s, 变更文件 %d 个", base_sha[:8], head_sha[:8], len(changed_files))
            return changed_files

        except Exception as e:
            logger.error("获取 PR diff 失败: %s", e)
            raise

    def pull_latest(self) -> None:
        """拉取最新代码"""
        try:
            self.repo.remotes.origin.pull()
            logger.info("已拉取最新代码")
        except Exception as e:
            logger.error("拉取失败: %s", e)
            raise

    # ----------------------------------------------------------------
    # 文件遍历
    # ----------------------------------------------------------------

    def get_all_files(
        self,
        supported_languages: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        max_file_size_kb: int = 500,
    ) -> list[dict[str, str]]:
        """
        遍历仓库中所有符合条件的代码文件。

        Returns:
            文件信息列表，每项包含 path（相对路径）, language, content
        """
        ext_map = {
            "python": [".py"],
            "javascript": [".js", ".jsx"],
            "typescript": [".ts", ".tsx"],
            "java": [".java"],
            "go": [".go"],
            "rust": [".rs"],
        }

        allowed_extensions: set[str] = set()
        if supported_languages:
            for lang in supported_languages:
                allowed_extensions.update(ext_map.get(lang, []))
        else:
            for exts in ext_map.values():
                allowed_extensions.update(exts)

        ignore = set(ignore_patterns or [])
        files = []
        repo_path = Path(self.repo_path)

        for file_path in repo_path.rglob("*"):
            if not file_path.is_file():
                continue

            rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")

            if any(pattern.rstrip("/") in rel_path for pattern in ignore):
                continue

            if file_path.suffix not in allowed_extensions:
                continue

            size_kb = file_path.stat().st_size / 1024
            if size_kb > max_file_size_kb:
                logger.debug("跳过大文件: %s (%.1f KB)", rel_path, size_kb)
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            lang = "unknown"
            for language, exts in ext_map.items():
                if file_path.suffix in exts:
                    lang = language
                    break

            files.append({
                "path": rel_path,
                "language": lang,
                "content": content,
            })

        logger.info("扫描完成: 共 %d 个代码文件", len(files))
        return files

    def get_file_content(self, relative_path: str) -> str | None:
        """读取单个文件内容"""
        full_path = Path(self.repo_path) / relative_path
        if not full_path.is_file():
            return None
        try:
            return full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

    def detect_modules(self, module_config: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """
        检测仓库中的模块结构。

        Args:
            module_config: 用户指定的模块路径→优先级映射

        Returns:
            模块列表，每项包含 path, name, priority, files_count
        """
        repo_path = Path(self.repo_path)
        modules = []

        if module_config:
            for mod_path, priority in module_config.items():
                full = repo_path / mod_path
                if full.is_dir():
                    file_count = sum(1 for _ in full.rglob("*") if _.is_file())
                    modules.append({
                        "path": mod_path,
                        "name": Path(mod_path).name,
                        "priority": priority,
                        "files_count": file_count,
                    })
        else:
            for entry in repo_path.iterdir():
                if entry.is_dir() and not entry.name.startswith((".", "_")):
                    file_count = sum(1 for _ in entry.rglob("*") if _.is_file())
                    modules.append({
                        "path": str(entry.relative_to(repo_path)),
                        "name": entry.name,
                        "priority": "medium",
                        "files_count": file_count,
                    })

        logger.info("检测到 %d 个模块", len(modules))
        return modules

    # ----------------------------------------------------------------
    # 内部工具方法
    # ----------------------------------------------------------------

    @staticmethod
    def _extract_repo_name(repo_url: str) -> str:
        """从 URL 中提取仓库名"""
        name = repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name

    def _inject_auth_token(self, repo_url: str) -> str:
        """注入认证 token 到 URL 中"""
        for platform_key, platform_cfg in self._platforms.items():
            base_url = platform_cfg.get("base_url", "")
            token_ref = platform_cfg.get("api_token", "")

            if base_url and base_url in repo_url:
                token = self._resolve_env(token_ref)
                if token:
                    if "://" in repo_url:
                        protocol, rest = repo_url.split("://", 1)
                        return f"{protocol}://oauth2:{token}@{rest}"
        return repo_url

    @staticmethod
    def _resolve_env(value: str) -> str:
        """解析环境变量"""
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            return os.getenv(value[2:-1], "")
        return value

    def cleanup(self) -> None:
        """清理克隆的仓库（谨慎使用）"""
        if self._repo_path and self._repo_path.exists():
            shutil.rmtree(self._repo_path, ignore_errors=True)
            logger.warning("已清理仓库: %s", self._repo_path)
            self._repo = None
            self._repo_path = None
