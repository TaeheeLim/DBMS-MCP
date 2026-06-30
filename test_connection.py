"""
Claude에 붙이기 전에 DB 연결을 먼저 확인하는 스크립트 (연결 프로파일 방식).

실행:
    python test_connection.py            # default 연결 점검
    python test_connection.py lcard_tibero   # 특정 연결 점검
"""

import sys
import jdbc_mcp as t


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else ""

    print("[1] 등록된 연결 목록...")
    cfg = t._load_config()
    print("    default:", cfg.get("default", ""))
    for n in t._available_names(cfg.get("connections", {})):
        print("    -", n)

    print(f"[2] 연결 시도... ({name or 'default'})")
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

    print("[4] 읽기 전용 검증 테스트...")
    for sql, expect_ok in [
        ("SELECT 1", True),
        ("WITH x AS (SELECT 1 a) SELECT * FROM x", True),
        ("DELETE FROM foo", False),
        ("SELECT 1; DROP TABLE foo", False),
    ]:
        try:
            t._assert_read_only(sql)
            ok = True
        except Exception:
            ok = False
        mark = "OK" if ok == expect_ok else "FAIL"
        print(f"    [{mark}] allowed={ok}  <-  {sql}")

    print("\n모든 점검 완료. Claude에 등록해도 좋습니다.")


if __name__ == "__main__":
    main()
