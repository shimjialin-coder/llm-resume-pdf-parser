"""PDF 文本读取与输入安全校验。"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
import warnings
from pathlib import Path

from .errors import AppError

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PAGES = 80
LOGGER = logging.getLogger("resume_parser.pdf")


def extract_pdf_text(pdf_path: str | Path, ocr_enabled: bool = True) -> tuple[str, int]:
    """提取本地 PDF 文本，并返回文本和页数。"""
    path = Path(pdf_path)
    if not path.exists() or not path.is_file():
        raise AppError("file_not_found", "找不到 PDF 文件。", "请检查文件路径。", {"path": str(path)})
    if path.suffix.lower() != ".pdf":
        raise AppError("invalid_file_type", "输入文件不是 PDF。", "请传入 .pdf 文件。", {"path": str(path)})
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AppError(
            "pdf_too_large",
            "PDF 文件超过大小限制。",
            "请压缩或拆分文件后重试。",
            {"max_bytes": MAX_FILE_BYTES},
        )

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"The `fitz` API is deprecated.*")
            import fitz
    except ModuleNotFoundError as exc:
        raise AppError("dependency_missing", "缺少 PDF 读取依赖 PyMuPDF。", "请先执行 make install。") from exc

    try:
        with fitz.open(path) as document:
            page_count = document.page_count
            if page_count > MAX_PAGES:
                raise AppError(
                    "pdf_too_many_pages",
                    "PDF 页数超过限制。",
                    "请拆分文件后重试。",
                    {"max_pages": MAX_PAGES, "actual_pages": page_count},
                )
            text = "\n".join(page.get_text("text") for page in document)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            "pdf_unreadable",
            "无法读取 PDF 文件。",
            "请确认文件未损坏、未加密，并尝试重新导出。",
            {"reason": type(exc).__name__},
        ) from exc

    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized and ocr_enabled:
        LOGGER.info("PDF 没有文本层，开始尝试 OCR")
        ocr_started = time.monotonic()
        normalized = _ocr_pdf(path)
        LOGGER.info("OCR 处理完成，耗时 %.1f 秒", time.monotonic() - ocr_started)
    if not normalized:
        hint = "请启用 OCR 并安装依赖后重试。" if not ocr_enabled else "该文件可能是扫描件；请确认已安装 OCR 依赖。"
        raise AppError("pdf_text_empty", "PDF 中未提取到文本。", hint, {"page_count": page_count})
    return normalized, page_count


def _ocr_pdf(path: Path) -> str:
    """使用本机 pdftoppm 与 Tesseract 对无文本 PDF 做可选 OCR。"""
    try:
        import pytesseract
        from PIL import Image
    except ModuleNotFoundError:
        return ""
    try:
        with tempfile.TemporaryDirectory(prefix="resume-ocr-") as directory:
            prefix = Path(directory) / "page"
            subprocess.run(
                ["pdftoppm", "-r", "200", "-png", str(path), str(prefix)],
                check=True,
                capture_output=True,
                timeout=120,
            )
            pages = sorted(Path(directory).glob("page-*.png"))
            texts = []
            for index, page in enumerate(pages, start=1):
                LOGGER.info("OCR 处理第 %d/%d 页", index, len(pages))
                texts.append(pytesseract.image_to_string(Image.open(page), lang="chi_sim+eng"))
            return "\n".join(texts).strip()
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return ""
