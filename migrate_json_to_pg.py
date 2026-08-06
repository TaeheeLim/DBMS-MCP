"""
connections.json → PostgreSQL 레지스트리 마이그레이션 스크립트
=============================================================

기존 파일 방식(connections.json)의 연결정보를 PostgreSQL(jdbc_group / jdbc_connection)
테이블로 옮긴다. 비밀번호는 Fernet 으로 암호화하여 저장한다.

사전 준비:
  1) 레지스트리 PostgreSQL 에 schema.sql 적용:
       psql "$REGISTRY_PG_DSN" -f schema.sql
  2) Fernet 키 준비 (없으면 아래로 생성):
       python migrate_json_to_pg.py --gen-key
     → 출력된 키를 REGISTRY_ENC_KEY 로 저장 (MCP 서버 env 와 반드시 동일한 키를 쓸 것).

실행:
  # 환경변수
  #   REGISTRY_PG_DSN  : postgresql://user:pw@host:5432/mcp_registry
  #   REGISTRY_ENC_KEY : Fernet 키 (위에서 생성/보관한 값)
  python migrate_json_to_pg.py                       # 기본 connections.json 사용
  python migrate_json_to_pg.py path/to/connections.json

  --gen-key : 새 Fernet 키를 출력하고 종료 (마이그레이션 안 함)
  --dry-run : DB에 쓰지 않고 무엇을 넣을지 요약만 출력

신규 연결을 나중에 직접 추가할 때 (평문은 DB에 넣으면 안 됨 — 암호문을 만들어 넣는다):
  # REGISTRY_ENC_KEY 를 서버와 동일하게 설정한 뒤
  python migrate_json_to_pg.py --encrypt "새비밀번호"
  # → 출력된 gAAAAA... 값을 아래처럼 INSERT
  #   INSERT INTO jdbc_connection
  #     (group_name, conn_name, descr, url, db_user, db_password)
  #   VALUES ('tibero7','newconn','설명','jdbc:...','USER','gAAAAA...');

멱등성: 같은 (그룹) / (그룹,연결) 키는 UPSERT 되므로 여러 번 실행해도 안전하다.
"""

import os
import sys
import json


def _gen_key() -> None:
    from cryptography.fernet import Fernet

    print(Fernet.generate_key().decode())


def _encrypt_one(plaintext: str) -> None:
    """평문 비밀번호를 REGISTRY_ENC_KEY 로 암호화한 암호문을 출력한다.

    신규 연결을 SQL 로 직접 INSERT 할 때 db_password 에 넣을 값이다.
    """
    key = os.environ.get("REGISTRY_ENC_KEY", "").strip()
    if not key:
        raise SystemExit(
            "REGISTRY_ENC_KEY 환경변수가 없습니다 (서버와 동일한 Fernet 키를 설정하세요)."
        )
    from cryptography.fernet import Fernet

    print(Fernet(key.encode()).encrypt(plaintext.encode()).decode())


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        raise SystemExit(f"connections.json 을 찾을 수 없습니다: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iter_rows(cfg: dict):
    """(group_row, conn_rows) 형태로 그룹별 데이터를 순회한다.

    group_row : (group_name, driver, jars)
    conn_rows : [(group_name, conn_name, descr, url, user, password_plain, driver, jars), ...]
                driver/jars 는 연결 자체에 명시된 오버라이드만 담고, 없으면 None.
    """
    for group_name, items in cfg.get("connections", {}).items():
        if not isinstance(items, dict) or not items:
            continue
        defaults = items.get("_defaults", {})
        gdriver = defaults.get("driver")
        gjars = defaults.get("jars")
        if not gdriver or not gjars:
            raise SystemExit(
                f"[{group_name}] 그룹에 _defaults.driver/jars 가 없습니다. "
                f"schema 상 jdbc_group.driver/jars 는 필수입니다."
            )

        conn_rows = []
        for conn_name, prof in items.items():
            if conn_name == "_defaults":
                continue
            if not isinstance(prof, dict):
                continue
            conn_rows.append(
                (
                    group_name,
                    conn_name,
                    prof.get("desc", ""),
                    prof["url"],
                    prof["user"],
                    prof.get("password", ""),
                    prof.get("driver"),  # 연결 레벨 오버라이드만 (없으면 None → 그룹값 사용)
                    prof.get("jars"),
                )
            )
        yield (group_name, gdriver, gjars), conn_rows


def main() -> None:
    args = [a for a in sys.argv[1:]]
    if "--gen-key" in args:
        _gen_key()
        return

    if "--encrypt" in args:
        i = args.index("--encrypt")
        if i + 1 >= len(args):
            raise SystemExit('사용법: python migrate_json_to_pg.py --encrypt "평문비밀번호"')
        _encrypt_one(args[i + 1])
        return

    dry_run = "--dry-run" in args
    paths = [a for a in args if not a.startswith("--")]
    here = os.path.dirname(os.path.abspath(__file__))
    json_path = paths[0] if paths else os.path.join(here, "connections.json")

    cfg = _load_json(json_path)

    groups = list(_iter_rows(cfg))
    n_groups = len(groups)
    n_conns = sum(len(cr) for _, cr in groups)
    print(f"[읽음] {json_path}")
    print(f"[대상] 그룹 {n_groups}개, 연결 {n_conns}개")

    if dry_run:
        for (gname, gdriver, gjars), conn_rows in groups:
            print(f"  - group {gname}: driver={gdriver}, jars={gjars}")
            for r in conn_rows:
                ov = []
                if r[6]:
                    ov.append("driver*")
                if r[7]:
                    ov.append("jars*")
                mark = f" ({','.join(ov)} 오버라이드)" if ov else ""
                print(f"      · {gname}/{r[1]}  user={r[4]}  url={r[3]}{mark}")
        print("[dry-run] DB에 쓰지 않았습니다.")
        return

    # Fernet 준비 (실제 적재 시에만 필요 — 비밀번호 암호화용)
    key = os.environ.get("REGISTRY_ENC_KEY", "").strip()
    if not key:
        raise SystemExit(
            "REGISTRY_ENC_KEY 환경변수가 없습니다. "
            "'python migrate_json_to_pg.py --gen-key' 로 키를 만들어 설정하세요."
        )
    from cryptography.fernet import Fernet

    fernet = Fernet(key.encode())

    dsn = os.environ.get("REGISTRY_PG_DSN", "").strip()
    if not dsn:
        raise SystemExit("REGISTRY_PG_DSN 환경변수가 없습니다 (레지스트리 PostgreSQL 접속 문자열).")

    import psycopg

    upserted_g = upserted_c = 0
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for (gname, gdriver, gjars), conn_rows in groups:
                cur.execute(
                    "INSERT INTO jdbc_group (group_name, driver, jars) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (group_name) DO UPDATE "
                    "SET driver = EXCLUDED.driver, jars = EXCLUDED.jars",
                    (gname, gdriver, gjars),
                )
                upserted_g += 1

                for (g, cname, descr, url, user, pw_plain, cdriver, cjars) in conn_rows:
                    enc = fernet.encrypt(str(pw_plain).encode()).decode()
                    cur.execute(
                        "INSERT INTO jdbc_connection "
                        "(group_name, conn_name, descr, url, db_user, db_password, "
                        " driver, jars, enabled) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE) "
                        "ON CONFLICT (group_name, conn_name) DO UPDATE SET "
                        "  descr = EXCLUDED.descr, url = EXCLUDED.url, "
                        "  db_user = EXCLUDED.db_user, db_password = EXCLUDED.db_password, "
                        "  driver = EXCLUDED.driver, jars = EXCLUDED.jars, "
                        "  enabled = TRUE",
                        (g, cname, descr, url, user, enc, cdriver, cjars),
                    )
                    upserted_c += 1
        conn.commit()

    print(f"[완료] 그룹 {upserted_g}개, 연결 {upserted_c}개 적재(UPSERT).")
    print("       MCP 서버 env 의 REGISTRY_ENC_KEY 가 이 스크립트와 동일한지 확인하세요.")


if __name__ == "__main__":
    main()
