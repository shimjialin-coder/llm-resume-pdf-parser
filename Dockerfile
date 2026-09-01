FROM python:3.11-slim

ARG INSTALL_LOCAL_MODEL=false
ARG INSTALL_OCR=false
ARG EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN if [ "${INSTALL_OCR}" = "true" ]; then apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir 'pytesseract>=0.3,<1' 'Pillow>=10,<12'; fi
RUN if [ "${INSTALL_LOCAL_MODEL}" = "true" ]; then \
      pip install --no-cache-dir 'sentence-transformers>=3,<4' && \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"; \
    fi

WORKDIR /work
ENTRYPOINT ["resume-cli"]
