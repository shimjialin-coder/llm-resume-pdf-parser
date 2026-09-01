"""本地嵌入模型的依赖检查与语义相似度计算。"""

from __future__ import annotations

import logging

from .errors import AppError
from .model_client import LocalModelConfig

LOGGER = logging.getLogger("resume_parser.local_model")


def validate_local_model(config: LocalModelConfig) -> None:
    """检查依赖和本地模型缓存，不允许运行时静默联网下载。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as exc:
        raise AppError(
            "local_model_dependency_missing",
            "配置已启用本地模型，但未安装 sentence-transformers。",
            "请重新运行 ./setup.sh 并选择启用本地模型，或安装 pip install -e '.[local-model]'。",
        ) from exc
    try:
        SentenceTransformer(config.embedding_model, local_files_only=True)
    except Exception as exc:
        raise AppError(
            "local_model_unavailable",
            "配置已启用本地模型，但模型文件未下载或不可用。",
            "请重新运行 ./setup.sh 并选择启用本地模型，以安装并下载模型。",
            {"model": config.embedding_model, "reason": type(exc).__name__},
        ) from exc


def semantic_similarity(left: str, right: str, config: LocalModelConfig) -> float:
    """使用本地 SentenceTransformer 计算两个文本的余弦相似度。"""
    try:
        from sentence_transformers import SentenceTransformer
        LOGGER.info("加载本地语义模型：%s", config.embedding_model)
        model = SentenceTransformer(config.embedding_model, local_files_only=True)
        embeddings = model.encode([left, right], normalize_embeddings=True, show_progress_bar=False)
        return float(embeddings[0] @ embeddings[1])
    except Exception as exc:
        raise AppError(
            "local_model_unavailable",
            "本地语义模型不可用或尚未下载。",
            "请重新运行 ./setup.sh 并选择启用本地模型，以安装并下载模型文件。",
            {"model": config.embedding_model, "reason": type(exc).__name__},
        ) from exc
