# JDBC MCP 서버 (HTTP 전송) — Python + JDK
# connections.json 과 jars/ 는 이미지에 굽지 않고 볼륨으로 마운트한다
# (비밀번호가 이미지에 안 박히고, 수정 시 재빌드 불필요).

FROM python:3.12-slim

# jaydebeapi 가 JVM 을 사용하므로 JDK 설치
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# JPype 가 JVM 을 찾도록 JAVA_HOME 지정
ENV JAVA_HOME=/usr/lib/jvm/default-java

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 코드만 복사 (connections.json / jars 는 런타임에 마운트)
COPY jdbc_mcp.py .

# HTTP 전송 기본 설정 (compose 에서 덮어쓸 수 있음)
ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    JDBC_CONNECTIONS=/app/connections.json \
    DB_MAX_ROWS=200

EXPOSE 8000

CMD ["python", "jdbc_mcp.py"]
