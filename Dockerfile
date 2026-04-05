# ClaudeRTOS-Insight Host — Docker Image
# Python 3.11-slim 기반, 재현 가능한 환경

FROM python:3.11-slim

LABEL maintainer="ClaudeRTOS-Insight"
LABEL description="AI-assisted FreeRTOS debugging host"

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY host/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY host/ ./host/
COPY examples/ ./examples/

# Python 경로 설정
ENV PYTHONPATH=/app/host
ENV PYTHONUNBUFFERED=1

# 기본 실행 (검증 모드)
CMD ["python3", "examples/integrated_demo.py", "--validate"]
