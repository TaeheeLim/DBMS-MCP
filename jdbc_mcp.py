"""
범용 JDBC MCP 서버 (연결 프로파일 방식)
==================================================

구조:  Claude  ->  (이 MCP 서버)  ->  임의의 JDBC 데이터베이스

여러 DB 연결을 레지스트리에 미리 등록해두고, 호출할 때 'DB그룹/연결이름'으로
골라 쓴다. 레지스트리는 두 가지 백엔드를 지원한다 (REGISTRY_BACKEND 로 선택):

  - postgres (기본) : 연결정보를 PostgreSQL 테이블(jdbc_group / jdbc_connection)에서 읽는다.
                      비밀번호는 Fernet 으로 암호화되어 저장되고, 복호화 키는 서버 env 로만 주입된다.
                      DB 내용을 고치면 다음 호출(캐시 TTL 이내면 refresh_connections 후)에 반영된다.
  - file            : 연결정보를 connections.json 파일에서 읽는다 (기존 방식).
                      파일을 고치면 다음 호출에 즉시 반영된다 (재시작 불필요).

- 연결: JDBC (jaydebeapi + 해당 DB의 JDBC jar, 내부적으로 JVM 사용)
- 테이블/컬럼 조회: JDBC 표준 DatabaseMetaData 사용 (DB 종류와 무관)
- 실행 가능: 조회(SELECT/WITH) + 변경(INSERT/UPDATE/DELETE/MERGE, 자동 커밋)
- 안전장치: DDL(CREATE/DROP/ALTER 등) 차단, 다중 문장 차단, 조회 행 수 상한

환경변수
--------
  REGISTRY_BACKEND   : 'postgres'(기본) 또는 'file'
  DB_MAX_ROWS        : 최대 반환 행 수 (기본 200)

  [postgres 백엔드]
  REGISTRY_PG_DSN    : 레지스트리 PostgreSQL 접속 문자열
                       예) postgresql://mcp_reader:pw@10.0.0.5:5432/mcp_registry
  REGISTRY_ENC_KEY   : 비밀번호 복호화용 Fernet 키 (연결 비밀번호는 DB에 암호문으로 저장됨)
  REGISTRY_CACHE_TTL : PG 조회 결과 캐시 유지 시간(초, 기본 30). 0 이면 매 호출 조회.

  [file 백엔드]
  JDBC_CONNECTIONS   : connections.json 경로
                       (미지정 시 이 스크립트와 같은 폴더의 connections.json)

레지스트리 구조 (두 백엔드 공통)
--------------------------------
  connections[그룹][_defaults] = 그룹 공통값(driver/jars)
  connections[그룹][연결]      = 개별 연결(url/user/password, 필요시 driver/jars 오버라이드)
  연결은 'DB그룹/연결이름' 으로 가리킨다 (예: tibero7/lcard).

  postgres 백엔드의 테이블 구조는 schema.sql 을, 마이그레이션은 migrate_json_to_pg.py 를 참고.
"""

import os
import re
import json
import time
import jaydebeapi

from mcp.server.fastmcp import FastMCP

# --- 전송(transport) 설정 ----------------------------------------------------
#   MCP_TRANSPORT = "stdio" : Claude Code가 로컬에서 직접 실행 (개발/단독 사용)
#                 = "http"  : 네트워크로 노출 (팀 공용, 리눅스 서버 배포)
TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
HOST = os.environ.get("MCP_HOST", "0.0.0.0")   # http 모드에서 바인딩할 주소
PORT = int(os.environ.get("MCP_PORT", "8000"))  # http 모드 포트

mcp = FastMCP("jdbc", host=HOST, port=PORT)

MAX_ROWS = int(os.environ.get("DB_MAX_ROWS", "200"))

# --- 레지스트리 백엔드 설정 --------------------------------------------------
#   REGISTRY_BACKEND = "postgres"(기본) : 연결정보를 PostgreSQL 에서 읽는다.
#                    = "file"           : 연결정보를 connections.json 에서 읽는다.
REGISTRY_BACKEND = os.environ.get("REGISTRY_BACKEND", "postgres").strip().lower()
REGISTRY_CACHE_TTL = float(os.environ.get("REGISTRY_CACHE_TTL", "30"))

# postgres 백엔드 조회 결과 캐시 (파일 백엔드는 매 호출 재읽기라 캐시 안 함)
_PG_CACHE = {"data": None, "ts": 0.0}


def _connections_path() -> str:
    """connections.json 경로를 결정한다."""
    env = os.environ.get("JDBC_CONNECTIONS", "")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "connections.json")


def _load_config() -> dict:
    """레지스트리에서 연결 설정을 읽어 공통 dict 형태로 반환한다.

    반환 형태(백엔드 무관):
      {"connections": {그룹: {"_defaults": {...}, 연결: {...}}}}
    REGISTRY_BACKEND 에 따라 파일(connections.json) 또는 PostgreSQL 에서 읽는다.
    """
    if REGISTRY_BACKEND == "file":
        return _load_config_from_file()
    return _load_config_from_pg()


def _load_config_from_file() -> dict:
    """connections.json 을 읽어 파싱한다 (매 호출마다 최신값 반영)."""
    path = _connections_path()
    if not os.path.exists(path):
        raise RuntimeError(f"연결 설정 파일이 없습니다: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_fernet():
    """REGISTRY_ENC_KEY(env)로 Fernet 객체를 만든다 (비밀번호 복호화용)."""
    key = os.environ.get("REGISTRY_ENC_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "REGISTRY_ENC_KEY 환경변수가 없습니다 (비밀번호 복호화용 Fernet 키)."
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise RuntimeError(
            "cryptography 패키지가 필요합니다. 'pip install cryptography' 후 다시 시도하세요."
        ) from e
    return Fernet(key.encode())


def _decrypt_password(fernet, token) -> str:
    """DB에 저장된 암호문(token)을 평문 비밀번호로 복호화한다."""
    if token is None or token == "":
        return ""
    try:
        return fernet.decrypt(str(token).encode()).decode()
    except Exception as e:
        raise RuntimeError(
            f"비밀번호 복호화에 실패했습니다 (REGISTRY_ENC_KEY 불일치 가능): {e}"
        ) from e


def _load_config_from_pg() -> dict:
    """PostgreSQL 레지스트리(jdbc_group / jdbc_connection)에서 연결 설정을 조립한다.

    비밀번호는 암호문으로 저장되어 있어 Fernet 으로 복호화한다.
    결과는 REGISTRY_CACHE_TTL 초 동안 캐시한다 (매 호출 DB 조회 부하/지연 완화).
    캐시를 즉시 비우려면 refresh_connections 도구를 호출한다.
    """
    now = time.monotonic()
    if (
        REGISTRY_CACHE_TTL > 0
        and _PG_CACHE["data"] is not None
        and (now - _PG_CACHE["ts"]) < REGISTRY_CACHE_TTL
    ):
        return _PG_CACHE["data"]

    dsn = os.environ.get("REGISTRY_PG_DSN", "").strip()
    if not dsn:
        raise RuntimeError(
            "REGISTRY_PG_DSN 환경변수가 없습니다 (레지스트리 PostgreSQL 접속 문자열)."
        )
    try:
        import psycopg
    except ImportError as e:
        raise RuntimeError(
            "psycopg 패키지가 필요합니다. 'pip install \"psycopg[binary]\"' 후 다시 시도하세요."
        ) from e

    fernet = _get_fernet()
    connections: dict = {}

    with psycopg.connect(dsn) as conn:
        # 그룹 공통값(_defaults): driver / jars
        for group_name, driver, jars in conn.execute(
            "SELECT group_name, driver, jars FROM jdbc_group"
        ):
            connections.setdefault(group_name, {})["_defaults"] = {
                "driver": driver,
                "jars": jars,
            }
        # 개별 연결
        for (
            group_name,
            conn_name,
            descr,
            url,
            db_user,
            db_password,
            driver,
            jars,
        ) in conn.execute(
            "SELECT group_name, conn_name, descr, url, db_user, db_password, "
            "driver, jars FROM jdbc_connection WHERE enabled IS TRUE"
        ):
            prof = {
                "desc": descr or "",
                "url": url,
                "user": db_user,
                "password": _decrypt_password(fernet, db_password),
            }
            # driver/jars 는 값이 있을 때만 넣어 그룹 _defaults 를 오버라이드한다
            # (없으면 _resolve_profile 이 _defaults 값을 그대로 쓴다)
            if driver:
                prof["driver"] = driver
            if jars:
                prof["jars"] = jars
            connections.setdefault(group_name, {})[conn_name] = prof

    cfg = {"connections": connections}
    _PG_CACHE["data"] = cfg
    _PG_CACHE["ts"] = now
    return cfg


def _available_names(conns: dict) -> list:
    """등록된 모든 연결을 'group/name' 형식 리스트로 반환한다 (_defaults 제외)."""
    names = []
    for group, items in conns.items():
        if not isinstance(items, dict):
            continue
        for key in items:
            if key == "_defaults":
                continue
            names.append(f"{group}/{key}")
    return names


def _resolve_profile(name: str) -> dict:
    """'group/name' 에 해당하는 연결 프로파일을 반환한다.

    구조: connections[group]['_defaults'] (그룹 공통값) 에 각 연결 값을 덮어써서 합친다.
    → DB 종류별로 driver/jars 는 _defaults 에 한 번만, 환경별 url/user/password 만
      각 연결에 적으면 된다.
    """
    cfg = _load_config()
    conns = cfg.get("connections", {})
    if not conns:
        raise RuntimeError("등록된 연결이 없습니다.")
    available = _available_names(conns)
    if not name:
        raise RuntimeError(
            f"연결 이름을 지정하세요 (형식: 'DB그룹/연결이름'). 사용 가능: {', '.join(available)}"
        )
    if "/" not in name:
        raise RuntimeError(
            f"'{name}' 형식이 올바르지 않습니다. 'DB그룹/연결이름' 형식으로 지정하세요. "
            f"사용 가능: {', '.join(available)}"
        )
    group, inst = name.split("/", 1)
    if group not in conns or not isinstance(conns[group], dict):
        raise RuntimeError(f"'{group}' DB 그룹을 찾을 수 없습니다. 사용 가능: {', '.join(available)}")
    grp = conns[group]
    if inst == "_defaults" or inst not in grp:
        raise RuntimeError(f"'{name}' 연결을 찾을 수 없습니다. 사용 가능: {', '.join(available)}")
    merged = dict(grp.get("_defaults", {}))  # 그룹 공통값 먼저
    merged.update(grp[inst])                 # 연결별 값으로 덮어쓰기
    return merged


def _jar_list(jars) -> list:
    """jars 값(문자열 또는 리스트)을 jar 경로 리스트로 변환한다."""
    if isinstance(jars, list):
        return [str(p).strip() for p in jars if str(p).strip()]
    raw = str(jars).replace(",", ";")
    return [p.strip() for p in raw.split(";") if p.strip()]


def _resolve_jar(path: str, base: str) -> str:
    """jar 경로를 절대경로로 만든다. 상대경로면 base(=connections.json 폴더) 기준."""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base, path))


def _all_jars() -> list:
    """connections.json 에 등록된 모든 jar 경로를 모아 중복 없이 반환한다.

    JVM(classpath)은 첫 연결 때 한 번 정해지므로, 어떤 DB를 먼저 호출하든
    모든 드라이버를 쓸 수 있도록 등록된 jar 전부를 classpath 에 올린다.
    jar 경로가 상대경로면 connections.json 이 있는 폴더 기준으로 해석한다.
    """
    cfg = _load_config()
    base = os.path.dirname(os.path.abspath(_connections_path()))
    seen, out = set(), []
    for items in cfg.get("connections", {}).values():
        if not isinstance(items, dict):
            continue
        for prof in items.values():
            if not isinstance(prof, dict) or not prof.get("jars"):
                continue
            for j in _jar_list(prof["jars"]):
                j = _resolve_jar(j, base)
                if j not in seen:
                    seen.add(j)
                    out.append(j)
    return out


def get_conn(connection: str = ""):
    """프로파일 이름으로 새 JDBC 연결을 생성한다."""
    p = _resolve_profile(connection)
    for key in ("driver", "url", "jars", "user"):
        if not p.get(key):
            raise RuntimeError(f"연결 프로파일에 '{key}' 값이 없습니다.")
    # 등록된 모든 jar 를 classpath 에 올린다 (여러 DB 혼용 대비)
    return jaydebeapi.connect(
        p["driver"], p["url"], [p["user"], p.get("password", "")], _all_jars()
    )


# --- 문장 검증 --------------------------------------------------------------
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")

# 조회 문장(결과 집합 반환) / 변경 문장(행 수 반환)
_READ_STMTS = ("select", "with")
_WRITE_STMTS = ("insert", "update", "delete", "merge")


def _strip_comments(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return sql.strip()


def _classify(sql: str) -> tuple[str, str]:
    """단일 문장인지 확인하고 (종류, 본문)을 반환한다.

    종류: 'read'  → SELECT/WITH (결과 집합 반환)
          'write' → INSERT/UPDATE/DELETE/MERGE (커밋 후 영향 행 수 반환)
    DDL(CREATE/DROP/ALTER/TRUNCATE 등)과 다중 문장은 여전히 차단한다.
    """
    clean = _strip_comments(sql)
    if not clean:
        raise ValueError("빈 쿼리입니다.")

    body = clean[:-1] if clean.endswith(";") else clean
    if ";" in body:
        raise ValueError("다중 문장은 허용되지 않습니다. 한 번에 한 문장만 실행하세요.")

    first = body.lstrip().split(None, 1)[0].lower()
    if first in _READ_STMTS:
        return "read", body
    if first in _WRITE_STMTS:
        return "write", body
    raise ValueError(
        "허용되지 않는 문장입니다. "
        "SELECT/WITH/INSERT/UPDATE/DELETE/MERGE 만 가능합니다 "
        f"(요청: '{first.upper()}')."
    )


def _norm(v):
    """JDBC 반환값을 JSON 직렬화 가능한 형태로 정규화한다."""
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


# --- 도구(tool) 정의 ---------------------------------------------------------
@mcp.tool()
def list_connections() -> dict:
    """등록된 DB 연결 목록을 반환한다 (비밀번호는 제외).

    사용자가 'lcard', '롯데카드', '운영 티베로' 같은 자연어로 DB를 지칭하면,
    먼저 이 도구를 호출해 desc/name 을 보고 알맞은 connection 값을 고른 뒤
    run_query / list_tables / describe_table 를 호출하라.

    반환: {connections: [{name, desc, driver, url, user}, ...]}
    """
    cfg = _load_config()
    conns = cfg.get("connections", {})
    out = []
    for full in _available_names(conns):
        p = _resolve_profile(full)
        out.append(
            {
                "name": full,
                "desc": p.get("desc", ""),
                "driver": p.get("driver", ""),
                "url": p.get("url", ""),
                "user": p.get("user", ""),
            }
        )
    return {"connections": out}


@mcp.tool()
def refresh_connections() -> dict:
    """레지스트리 캐시를 비운다 (postgres 백엔드).

    PostgreSQL 의 jdbc_group / jdbc_connection 을 방금 수정했고 캐시 TTL(기본 30초)을
    기다리지 않고 즉시 반영하고 싶을 때 호출한다. file 백엔드는 매 호출 재읽기라 무의미하다.
    """
    _PG_CACHE["data"] = None
    _PG_CACHE["ts"] = 0.0
    return {"ok": True, "message": "레지스트리 캐시를 비웠습니다. 다음 호출에서 다시 읽습니다."}


@mcp.tool()
def run_query(sql: str, connection: str = "") -> dict:
    """SQL 문장을 실행한다 (SELECT/WITH 조회 + INSERT/UPDATE/DELETE/MERGE 변경).

    connection: 사용할 연결 이름 'group/name' (미지정 시 default). 사용자가 별칭/한글로
        DB를 지칭하면 먼저 list_connections 로 목록을 보고 알맞은 이름을 고를 것.

    - SELECT/WITH: 결과는 DB_MAX_ROWS 행으로 제한된다.
      반환: {statement:'read', columns:[...], rows:[{...}], row_count:int, truncated:bool}
    - INSERT/UPDATE/DELETE/MERGE: 실행 후 자동 커밋된다.
      반환: {statement:'write', affected_rows:int}

    ⚠ 변경 문장은 되돌릴 수 없다. UPDATE/DELETE 는 반드시 WHERE 절을 확인할 것.
    DDL(CREATE/DROP/ALTER/TRUNCATE)과 다중 문장은 차단된다.
    """
    kind, body = _classify(sql)
    conn = get_conn(connection)
    try:
        cur = conn.cursor()
        cur.execute(body)

        if kind == "read":
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
            truncated = len(rows) > MAX_ROWS
            rows = rows[:MAX_ROWS]
            data = [{c: _norm(v) for c, v in zip(cols, r)} for r in rows]
            return {
                "statement": "read",
                "columns": cols,
                "rows": data,
                "row_count": len(data),
                "truncated": truncated,
            }

        # write: 변경 문장은 커밋해야 반영된다
        conn.commit()
        affected = getattr(cur, "rowcount", -1)
        return {"statement": "write", "affected_rows": affected}
    finally:
        conn.close()


@mcp.tool()
def list_tables(connection: str = "", schema: str = "") -> list[dict]:
    """테이블/뷰 목록을 반환한다 (JDBC 표준 메타데이터 사용).

    connection: 사용할 연결 이름 (미지정 시 default).
    schema 미지정 시 접속 계정이 접근 가능한 전체를 대상으로 한다.
    반환: [{schema, name, type}, ...]
    """
    conn = get_conn(connection)
    try:
        meta = conn.jconn.getMetaData()
        rs = meta.getTables(None, schema or None, "%", None)
        out = []
        while rs.next():
            ttype = rs.getString("TABLE_TYPE")
            if ttype not in ("TABLE", "VIEW"):
                continue
            out.append(
                {
                    "schema": rs.getString("TABLE_SCHEM"),
                    "name": rs.getString("TABLE_NAME"),
                    "type": ttype,
                }
            )
        rs.close()
        return out
    finally:
        conn.close()


@mcp.tool()
def describe_table(table_name: str, connection: str = "", schema: str = "") -> list[dict]:
    """테이블의 컬럼 정보(이름/타입/길이/NULL 허용)를 반환한다 (JDBC 표준).

    connection: 사용할 연결 이름 (미지정 시 default).
    반환: [{column, type, size, nullable}, ...]
    """
    conn = get_conn(connection)
    try:
        meta = conn.jconn.getMetaData()
        rs = meta.getColumns(None, schema or None, table_name, "%")
        out = []
        while rs.next():
            out.append(
                {
                    "column": rs.getString("COLUMN_NAME"),
                    "type": rs.getString("TYPE_NAME"),
                    "size": _norm(rs.getInt("COLUMN_SIZE")),
                    "nullable": rs.getString("IS_NULLABLE") == "YES",
                }
            )
        rs.close()
        return out
    finally:
        conn.close()


if __name__ == "__main__":
    if TRANSPORT == "http":
        # http://<HOST>:<PORT>/mcp 로 노출됨 (팀 공용)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # 기본 stdio (로컬 단독)
