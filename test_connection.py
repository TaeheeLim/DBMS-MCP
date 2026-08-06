"""
Claude에 붙이기 전에 DB 연결을 먼저 확인하는 스크립트 (연결 프로파일 방식).

실행:
    python test_connection.py            # default 연결 점검
    python test_connection.py tibero7/dev1   # 특정 연결 점검 (그룹/연결 형식)
"""

import sys
import jdbc_mcp as t


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else ""

    print(f"[1] 레지스트리 백엔드: {t.REGISTRY_BACKEND}")
    print("    등록된 연결 목록...")
    cfg = t._load_config()
    names = t._available_names(cfg.get("connections", {}))
    for n in names:
        print("    -", n)
    if not name:
        if not names:
            raise SystemExit("등록된 연결이 없습니다.")
        name = names[0]
        print(f"    (연결 미지정 → 첫 연결로 점검: {name})")

    print(f"[2] 연결 시도... ({name})")
    conn = t.get_conn(name)
    p = t._resolve_profile(name)
    print("    OK  ->", p["url"])

    print("[3] 테이블/뷰 목록 (최대 10개, JDBC 메타데이터)...")
    meta = conn.jconn.getMetaData()
    rs = meta.getTables(None, None, "%", None)
    count = 0
    while rs.next() and count < 10:
        ttype = rs.getString("TABLE_TYPE")
        if ttype not in ("TABLE", "VIEW"):
            continue
        print(f"    - [{ttype}] {rs.getString('TABLE_SCHEM')}.{rs.getString('TABLE_NAME')}")
        count += 1
    rs.close()
    conn.close()

    print("[4] 문장 검증 테스트 (허용: SELECT/WITH/INSERT/UPDATE/DELETE/MERGE, 차단: DDL·다중문장)...")
    # (sql, 기대 결과) — 기대 결과는 허용 시 분류('read'/'write'), 차단 시 None
    cases = [
        ("SELECT 1", "read"),
        ("WITH x AS (SELECT 1 a) SELECT * FROM x", "read"),
        ("INSERT INTO foo(a) VALUES (1)", "write"),
        ("UPDATE foo SET a = 1 WHERE id = 1", "write"),
        ("DELETE FROM foo WHERE id = 1", "write"),
        ("DROP TABLE foo", None),               # DDL 차단
        ("CREATE TABLE foo (a int)", None),      # DDL 차단
        ("SELECT 1; DROP TABLE foo", None),      # 다중 문장 차단
    ]
    for sql, expect in cases:
        try:
            kind, _ = t._classify(sql)
        except Exception:
            kind = None
        mark = "OK" if kind == expect else "FAIL"
        print(f"    [{mark}] kind={kind!s:<5}  <-  {sql}")

    print("\n모든 점검 완료. Claude에 등록해도 좋습니다.")


if __name__ == "__main__":
    main()
