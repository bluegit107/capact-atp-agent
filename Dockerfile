FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend/src

WORKDIR /app

# 의존성 레이어 분리(버전은 pyproject 와 동일하게 고정)
RUN pip install --no-cache-dir \
    "pydantic==2.14.3" \
    "ortools==9.15.6755" \
    "fastapi==0.162.2" \
    "uvicorn[standard]==0.54.0" \
    "psycopg[binary]==3.3.8" \
    "cryptography==50.0.2" \
    "anthropic==2.7.0"

COPY backend/src /app/backend/src
# 런타임 데이터(합성·도출, provenance 포함)와 정적 화면. 원본 데이터룸은 이미지에 넣지 않는다.
COPY extensions /app/extensions
COPY frontend /app/frontend
COPY agent_replay /app/agent_replay

# 비루트 실행
RUN useradd --create-home --uid 10001 app
USER app

# axhub 가 PORT 환경변수를 주입한다. 0.0.0.0 바인딩 필수.
EXPOSE 8080
CMD ["python", "-m", "dealcockpit.adapters.api"]
