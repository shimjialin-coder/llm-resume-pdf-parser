"""resume-cli 命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .errors import AppError

LOGGER = logging.getLogger("resume_parser")


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """添加各命令共用参数。"""
    parser.add_argument("--output", help="将 JSON 保存到指定文件")
    parser.add_argument("--mock", action="store_true", help="不调用模型服务，使用确定性降级逻辑")
    parser.add_argument("--model-url", help="OpenAI-compatible 服务地址，也可用 RESUME_AI_BASE_URL")
    parser.add_argument("--model", help="模型名称，也可用 RESUME_AI_MODEL")
    parser.add_argument("--api-key", help="模型 API Key，也可用 RESUME_AI_API_KEY")
    parser.add_argument("--config", default="config.toml", help="TOML 配置文件路径，默认 config.toml")
    parser.add_argument("--no-fallback", action="store_const", const=False, default=None, help="模型失败时不降级，直接返回错误")
    parser.add_argument("--verbose", action="store_true", help="输出更多诊断日志")
    parser.add_argument("--no-ocr", action="store_true", help="禁用扫描 PDF 的 OCR 回退")


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(prog="resume-cli", description="PDF 简历解析与 JD 匹配工具")
    commands = parser.add_subparsers(dest="command", required=True)
    parse_command = commands.add_parser("parse", help="提取 PDF 文本")
    parse_command.add_argument("pdf_path")
    _add_common_options(parse_command)
    extract_command = commands.add_parser("extract", help="提取结构化简历信息")
    extract_command.add_argument("pdf_path")
    _add_common_options(extract_command)
    score_command = commands.add_parser("score", help="计算简历与 JD 的匹配分数")
    score_command.add_argument("pdf_path")
    score_command.add_argument("--jd", required=True, help="岗位描述文本文件")
    _add_common_options(score_command)
    return parser


def _read_jd(jd_path: str) -> str:
    """读取并验证 JD 文本。"""
    path = Path(jd_path)
    if not path.exists() or not path.is_file():
        raise AppError("jd_not_found", "找不到 JD 文件。", "请检查 --jd 路径。", {"path": str(path)})
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise AppError("jd_unreadable", "无法读取 JD 文件。", "请确认文件使用 UTF-8 编码。") from exc
    if not text:
        raise AppError("jd_empty", "JD 文件为空。", "请提供岗位职责和要求。")
    if len(text) > 200_000:
        raise AppError("jd_too_large", "JD 文本过长。", "请压缩到 200000 个字符以内。")
    return text


def _client_from_args(args: argparse.Namespace) -> tuple[object | None, bool, object | None]:
    """根据参数构造模型客户端和降级开关；mock 模式明确禁用网络。"""
    from .model_client import OpenAICompatibleClient, load_settings

    if args.mock:
        LOGGER.info("已启用 mock 模式，不会访问模型服务")
        return None, True, None
    settings = load_settings(args.config, args.model_url, args.model, args.api_key, args.no_fallback)
    if settings.local_model:
        from .local_model import validate_local_model

        LOGGER.info("检查本地语义模型：%s", settings.local_model.embedding_model)
        validate_local_model(settings.local_model)
    return (OpenAICompatibleClient(settings.model) if settings.model else None), settings.fallback_enabled, settings.local_model


def _write_payload(payload: dict, output: str | None) -> None:
    """将 JSON 输出到终端或文件。"""
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if not output:
        print(rendered, end="")
        return
    try:
        Path(output).write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise AppError("output_write_failed", "无法写入输出文件。", "请检查目录权限。", {"path": output}) from exc
    LOGGER.info("结果已写入 %s", output)


def _run(args: argparse.Namespace) -> dict:
    """执行具体子命令。"""
    from .pdf_reader import extract_pdf_text

    started = time.monotonic()
    LOGGER.info("[1/4] 开始读取 PDF：%s", args.pdf_path)
    from .model_client import load_settings
    ocr_enabled = load_settings(args.config).ocr_enabled and not args.no_ocr
    text, page_count = extract_pdf_text(args.pdf_path, ocr_enabled=ocr_enabled)
    LOGGER.info("[1/4] PDF 文本提取完成，耗时 %.1f 秒", time.monotonic() - started)
    if args.command == "parse":
        return {"text": text, "page_count": page_count}
    from .extraction import extract_resume

    LOGGER.info("[2/4] PDF 解析完成：页数=%d，文本字符数=%d", page_count, len(text))
    client, fallback_enabled, local_model = _client_from_args(args)
    model_started = time.monotonic()
    LOGGER.info("[3/4] 开始结构化信息抽取%s", "（模型请求进行中）" if client else "（规则降级模式）")
    resume = extract_resume(text, client, fallback_enabled)
    LOGGER.info("[3/4] 结构化信息抽取完成，耗时 %.1f 秒", time.monotonic() - model_started)
    if args.command == "extract":
        payload = resume.model_dump(mode="json")
        if client and client.last_status == "fallback":
            payload["_meta"] = {"source": "rules", "fallback": True, "warning": "LLM 调用失败，已使用规则结果"}
        return payload
    from .scoring import score_resume

    jd_text = _read_jd(args.jd)
    LOGGER.info("[4/4] 开始 JD 匹配评分")
    score_started = time.monotonic()
    score = score_resume(resume, text, jd_text, client, fallback_enabled, local_model)
    LOGGER.info("[4/4] JD 匹配评分完成，耗时 %.1f 秒", time.monotonic() - score_started)
    payload = score.model_dump(mode="json")
    if client and client.last_status == "fallback":
        payload["_meta"] = {"source": "rules", "fallback": True, "warning": "LLM 调用失败，已使用规则评分"}
    return payload


def main(argv: list[str] | None = None) -> int:
    """执行命令并将可预期错误转换为结构化输出。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        _write_payload(_run(args), args.output)
        return 0
    except AppError as exc:
        LOGGER.error("[%s] %s", exc.code, exc.message)
        if exc.hint:
            LOGGER.error("建议：%s", exc.hint)
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2))
        return exc.exit_code
    except ModuleNotFoundError as exc:
        missing = exc.name or "未知依赖"
        LOGGER.error("缺少运行依赖：%s", missing)
        print(json.dumps({"error": {"code": "dependency_missing", "message": f"缺少运行依赖：{missing}。", "hint": "请先执行 make install。"}}, ensure_ascii=False, indent=2))
        return 2
    except Exception:
        LOGGER.exception("发生未预期错误，请提交日志以便排查")
        print(json.dumps({"error": {"code": "internal_error", "message": "程序遇到未预期错误。"}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
