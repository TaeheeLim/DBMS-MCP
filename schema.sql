-- =====================================================================
-- JDBC MCP 레지스트리 스키마 (PostgreSQL)
-- ---------------------------------------------------------------------
-- connections.json 을 대체하는 연결정보 저장소.
--   jdbc_group      : DB그룹(=connections.json 의 _defaults) — 그룹 공통 driver/jars
--   jdbc_connection : 개별 연결 — url/user/password(암호문) + 필요시 driver/jars 오버라이드
--
-- 연결은 'DB그룹/연결이름' 으로 가리킨다 (예: tibero7/lcard).
-- 비밀번호(db_password)는 애플리케이션에서 Fernet 으로 암호화한 '암호문'을 저장한다.
-- (평문을 넣지 말 것. 적재는 migrate_json_to_pg.py 참고.)
--
-- 실행:  psql "postgresql://user:pw@host:5432/mcp_registry" -f schema.sql
-- =====================================================================

-- 그룹 공통값 (connections.json 의 _defaults 에 해당)
CREATE TABLE IF NOT EXISTS jdbc_group (
    group_name  text PRIMARY KEY,            -- 예: 'tibero7', 'tibero6', 'oracle'
    driver      text NOT NULL,               -- JDBC 드라이버 클래스명
    jars        text NOT NULL                -- JDBC jar 경로(MCP 서버 호스트 기준, 상대/절대)
);

-- 개별 연결
CREATE TABLE IF NOT EXISTS jdbc_connection (
    group_name  text NOT NULL REFERENCES jdbc_group(group_name) ON UPDATE CASCADE,
    conn_name   text NOT NULL,               -- 예: 'lcard', 'tmoney'
    descr       text,                        -- list_connections 가 노출하는 설명(자연어 매칭용)
    url         text NOT NULL,               -- 전체 JDBC URL (통째로 저장; host/port로 쪼개지 말 것)
    db_user     text NOT NULL,               -- 접속 계정
    db_password text NOT NULL,               -- ★ Fernet 암호문 (평문 금지)
    driver      text,                        -- NULL 이면 그룹(jdbc_group) 값 사용, 값 있으면 오버라이드
    jars        text,                        -- NULL 이면 그룹(jdbc_group) 값 사용, 값 있으면 오버라이드
    enabled     boolean NOT NULL DEFAULT true,
    PRIMARY KEY (group_name, conn_name)
);

-- 조회 편의를 위한 인덱스 (enabled 연결만 스캔)
CREATE INDEX IF NOT EXISTS idx_jdbc_connection_enabled
    ON jdbc_connection (enabled);

-- =====================================================================
-- 권한 권장 (선택):
--   MCP 서버는 읽기 전용 계정으로 접속하게 하면 레지스트리 변조를 막을 수 있다.
--     CREATE ROLE mcp_reader LOGIN PASSWORD '...';
--     GRANT SELECT ON jdbc_group, jdbc_connection TO mcp_reader;
--   연결 등록/수정은 별도 관리 계정으로만 수행한다.
-- =====================================================================
