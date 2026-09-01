#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME:?}/.local/bin"
CONFIG_FILE="${PROJECT_DIR}/config.toml"
IMAGE_NAME="resume-pdf-parser:local"
cd "${PROJECT_DIR}"
info() { printf '\033[36m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

PYTHON_BIN="$(command -v python3 || true)"
[[ -n "${PYTHON_BIN}" ]] || fail "未找到 python3，请先安装 Python 3.10 或更高版本。"
"${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || fail "需要 Python 3.10 或更高版本。"

printf '选择运行环境：\n  1) 本地 Python（推荐开发调试）\n  2) Docker（隔离依赖）\n请输入 [1/2，默认 1]：'
read -r RUNTIME_CHOICE
RUNTIME_CHOICE="${RUNTIME_CHOICE:-1}"
[[ "${RUNTIME_CHOICE}" == "1" || "${RUNTIME_CHOICE}" == "2" ]] || fail "运行环境只能选择 1 或 2。"
printf '是否启用本地语义模型？[y/N]：'
read -r ENABLE_LOCAL_MODEL
if [[ "${ENABLE_LOCAL_MODEL}" =~ ^[Yy]$ ]]; then
  LOCAL_MODEL_ENABLED=true
  printf '本地嵌入模型（默认 BAAI/bge-small-zh-v1.5）：'
  read -r EMBEDDING_MODEL
  EMBEDDING_MODEL="${EMBEDDING_MODEL:-BAAI/bge-small-zh-v1.5}"
else
  LOCAL_MODEL_ENABLED=false
  EMBEDDING_MODEL="BAAI/bge-small-zh-v1.5"
fi
printf '是否安装 OCR 依赖（支持扫描型 PDF）？[Y/n]：'
read -r ENABLE_OCR
[[ "${ENABLE_OCR}" =~ ^[Nn]$ ]] && OCR_ENABLED=false || OCR_ENABLED=true

if [[ "${RUNTIME_CHOICE}" == "1" ]]; then
  info "创建本地虚拟环境并安装依赖。"
  "${PYTHON_BIN}" -m venv "${PROJECT_DIR}/.venv"
  "${PROJECT_DIR}/.venv/bin/python" -m pip install --upgrade pip
  "${PROJECT_DIR}/.venv/bin/python" -m pip install --no-build-isolation -e "${PROJECT_DIR}[dev]"
  if [[ "${OCR_ENABLED}" == "true" ]]; then
    "${PROJECT_DIR}/.venv/bin/python" -m pip install --no-build-isolation -e "${PROJECT_DIR}[ocr]"
  fi
  if [[ "${LOCAL_MODEL_ENABLED}" == "true" ]]; then
    "${PROJECT_DIR}/.venv/bin/python" -m pip install --no-build-isolation -e "${PROJECT_DIR}[local-model]"
    info "下载本地语义模型：${EMBEDDING_MODEL}"
    "${PROJECT_DIR}/.venv/bin/python" -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"
  fi
  COMMAND_TARGET="${PROJECT_DIR}/.venv/bin/resume-cli"
else
  command -v docker >/dev/null 2>&1 || fail "未找到 Docker，请安装并启动 Docker Desktop 后重试。"
  docker build --build-arg "INSTALL_LOCAL_MODEL=${LOCAL_MODEL_ENABLED}" --build-arg "INSTALL_OCR=${OCR_ENABLED}" --build-arg "EMBEDDING_MODEL=${EMBEDDING_MODEL}" -t "${IMAGE_NAME}" "${PROJECT_DIR}"
  WRAPPER_FILE="${PROJECT_DIR}/.resume-cli-docker"
  printf '%s\n' '#!/usr/bin/env bash' 'set -Eeuo pipefail' 'exec docker run --rm -i -v "${PWD}:/work" -v "'"${CONFIG_FILE}"':/work/config.toml:ro" -w /work resume-pdf-parser:local "$@"' > "${WRAPPER_FILE}"
  chmod +x "${WRAPPER_FILE}"
  COMMAND_TARGET="${WRAPPER_FILE}"
fi

if [[ ! -f "${CONFIG_FILE}" ]]; then cp "${PROJECT_DIR}/config.toml.example" "${CONFIG_FILE}"; fi
printf '是否现在配置 LLM？[y/N]：'
read -r CONFIGURE_LLM
if [[ "${CONFIGURE_LLM}" =~ ^[Yy]$ ]]; then
  printf 'provider（默认 ollama）：'; read -r PROVIDER; PROVIDER="${PROVIDER:-ollama}"
  printf 'base_url（默认 http://localhost:11434/v1）：'; read -r BASE_URL; BASE_URL="${BASE_URL:-http://localhost:11434/v1}"
  printf 'model（默认 qwen2.5:7b）：'; read -r MODEL_NAME; MODEL_NAME="${MODEL_NAME:-qwen2.5:7b}"
  printf 'api_key（可留空；输入不回显）：'; read -rs API_KEY; printf '\n'
  printf '是否启用 LLM？[Y/n]：'; read -r ENABLED; ENABLED="${ENABLED:-y}"
  printf 'LLM 失败时是否自动降级？[Y/n]：'; read -r FALLBACK; FALLBACK="${FALLBACK:-y}"
  [[ "${ENABLED}" =~ ^[Nn]$ ]] && ENABLED_VALUE=false || ENABLED_VALUE=true
  [[ "${FALLBACK}" =~ ^[Nn]$ ]] && FALLBACK_VALUE=false || FALLBACK_VALUE=true
  "${PYTHON_BIN}" - "${CONFIG_FILE}" "${PROVIDER}" "${BASE_URL}" "${MODEL_NAME}" "${API_KEY}" "${ENABLED_VALUE}" "${FALLBACK_VALUE}" <<'PY'
from pathlib import Path
import sys

path, provider, base_url, model, api_key, enabled, fallback = sys.argv[1:]
def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
Path(path).write_text(
    f'[runtime]\nfallback_enabled = {fallback}\n\n[llm]\nenabled = {enabled}\nprovider = {quote(provider)}\nbase_url = {quote(base_url)}\nmodel = {quote(model)}\napi_key = {quote(api_key)}\ntimeout_seconds = 20\nresponse_path = ""\ncontent_type = "text"\n\n[local_model]\nenabled = false\nembedding_model = "BAAI/bge-small-zh-v1.5"\n',
    encoding='utf-8',
)
PY
else
  info "已跳过 LLM 配置，保留现有 config.toml。"
fi

"${PYTHON_BIN}" - "${CONFIG_FILE}" "${EMBEDDING_MODEL}" "${LOCAL_MODEL_ENABLED}" <<'PY'
from pathlib import Path
import re
import sys

path, embedding_model, enabled = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8")
replacement = f'enabled = {enabled}\nembedding_model = "{embedding_model}"'
section = re.search(r'(?ms)^\[local_model\]\s*(.*?)(?=^\[|\Z)', text)
if section:
    body = section.group(1)
    body = re.sub(r'(?m)^enabled\s*=\s*(?:true|false)\s*$', f'enabled = {enabled}', body, count=1)
    body = re.sub(r'(?m)^embedding_model\s*=\s*".*"\s*$', f'embedding_model = "{embedding_model}"', body, count=1)
    text = text[:section.start(1)] + body + text[section.end(1):]
else:
    text = text.rstrip() + f'\n\n[local_model]\n{replacement}\n'
path.write_text(text, encoding="utf-8")
PY

"${PYTHON_BIN}" - "${CONFIG_FILE}" "${OCR_ENABLED}" <<'PY'
from pathlib import Path
import re
import sys

path, enabled = Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
section = re.search(r'(?ms)^\[ocr\]\s*(.*?)(?=^\[|\Z)', text)
if section:
    body = re.sub(r'(?m)^enabled\s*=\s*(?:true|false)\s*$', f'enabled = {enabled}', section.group(1), count=1)
    text = text[:section.start(1)] + body + text[section.end(1):]
else:
    text = text.rstrip() + f'\n\n[ocr]\nenabled = {enabled}\n'
path.write_text(text, encoding="utf-8")
PY

mkdir -p "${BIN_DIR}"
ln -sfn "${COMMAND_TARGET}" "${BIN_DIR}/resume-cli"
case "${SHELL:-}" in
  */zsh) SHELL_RC="${HOME}/.zshrc" ;;
  */bash) SHELL_RC="${HOME}/.bashrc" ;;
  *) SHELL_RC="" ;;
esac
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [[ -n "${SHELL_RC}" ]] && ! grep -Fqx "${PATH_LINE}" "${SHELL_RC}" 2>/dev/null; then
  printf '\n%s\n' "${PATH_LINE}" >> "${SHELL_RC}"
fi
info "安装完成。当前终端请执行：export PATH=\"${BIN_DIR}:\$PATH\""
printf '之后可以直接运行：\n  resume-cli --help\n  resume-cli extract ./resume.pdf --mock\n'
