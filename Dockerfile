# 원격 MCP 서버 (sh-notice-remote). 개인정보를 저장하지 않는 읽기 전용 버전.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[remote]"
ENV PORT=8080 SH_NOTICE_DATA_DIR=/tmp/sh-notice PYTHONUNBUFFERED=1
EXPOSE 8080
USER nobody
CMD ["sh-notice-remote"]
