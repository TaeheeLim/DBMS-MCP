# 범용 JDBC MCP 서버

`Claude  ->  이 MCP 서버  ->  임의의 JDBC DB` 구조의 데이터베이스 도구.
조회(SELECT/WITH)와 변경(INSERT/UPDATE/DELETE/MERGE)을 모두 지원한다(DDL·다중 문장은 차단).
JDBC 드라이버 jar 와 접속 URL 만 있으면 Tibero / Oracle / PostgreSQL / MySQL 등
어떤 DB든 동일한 코드로 사용할 수 있다.

## 구조

```
.mcp.json         ← 한 번만 설정 (이후 안 건드림). connections.json 위치만 알려줌
   │
connections.json  ← DB 목록을 여기서 관리 (추가/변경 시 이 파일만 수정)
   │
jdbc_mcp.py       ← 호출할 때마다 connections.json 을 다시 읽음 → Claude 재시작 불필요
   │
jars/             ← JDBC 드라이버 jar 모음 (connections.json 의 jars 가 가리킴)
```

> `connections.json` 에는 IP·계정·비밀번호가 들어가므로 git 에 커밋하지 않는다(`.gitignore` 포함).
> 형식은 아래 예시와 `connections copy.json`(플레이스홀더 템플릿) 을 참고하라.

## 제공 도구

| 도구 | 설명 |
|------|------|
| `list_connections()` | 등록된 DB 연결 목록 (`name`/`desc`/`driver`/`url`/`user`, 비밀번호 제외) |
| `run_query(sql, connection?)` | SQL 실행 — 조회(SELECT/WITH, 행 수 제한) + 변경(INSERT/UPDATE/DELETE/MERGE, 자동 커밋) |
| `list_tables(connection?, schema?)` | 테이블/뷰 목록 (JDBC 표준 메타데이터) |
| `describe_table(table_name, connection?, schema?)` | 컬럼 정보 (JDBC 표준 메타데이터) |

- `connection` 인자를 생략하면 `connections.json` 의 `default` 연결을 사용한다.
- 사용자가 "개발 티베로", "운영 오라클" 같은 **자연어로 DB를 지칭**하면 Claude 는 먼저
  `list_connections` 로 각 연결의 `desc`(설명)를 보고 알맞은 `그룹/연결` 이름을 고른다.
  그래서 connections.json 의 각 연결에 `desc` 를 적어두는 것이 중요하다.

## 사전 준비

1. **JDK/JRE** 설치 (jaydebeapi가 JVM을 사용)
   ```powershell
   java -version   # 확인
   ```
2. **해당 DB의 JDBC jar** 준비 — 이 저장소는 `jars/` 폴더에 Tibero6/7, Oracle 드라이버를 포함한다.
   다른 DB는 해당 jar 를 `jars/` 에 추가한다.

## 설치

```powershell
cd <프로젝트_경로>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## DB 등록 — connections.json

DB 추가/변경은 이 파일만 수정하면 된다. IP·포트·계정·비밀번호가 바뀌면 이 파일의 값만
고치면 **즉시 반영**된다(호출 때마다 다시 읽음 → Claude/서버 재시작 불필요).
단, **새 JDBC 드라이버 jar 가 필요한 경우**에는 예외다 → 아래 [새 DBMS 추가](#새-연결--새-dbms-추가-시) 참고.

### 구조 — DB그룹 / 연결 (2단계)

`connections` 아래 **DB 종류별 그룹**을 두고, 그 안에 여러 연결을 둔다.
각 그룹의 `_defaults` 는 그 그룹 공통값(driver/jars)이고, 나머지 키는 개별 연결이다.
연결은 `_defaults` 를 상속하고 겹치는 값(url/user/password)만 덮어쓴다.

연결은 **`그룹/연결` 형식**으로 가리킨다 (예: `tibero7/dev1`).

```jsonc
{
  "default": "tibero7/dev1",          // 그룹/연결 형식. 이름 생략 시 이 연결 사용
  "connections": {
    "tibero7": {
      "_defaults": {                  // tibero7 그룹 공통값
        "driver": "com.tmax.tibero.jdbc.TbDriver",
        "jars":   "jars/tibero7-jdbc.jar"
      },
      "dev1": {
        "desc":     "A 시스템 개발 티베로",   // list_connections 가 보여주는 설명 (자연어 매칭에 사용)
        "url":      "jdbc:tibero:thin:@<DB호스트>:8724:tibero7",
        "user":     "DEV_USER1",
        "password": "..."
      },
      "dev2": {
        "desc":     "B 시스템 개발 티베로",
        "url":      "jdbc:tibero:thin:@<DB호스트>:8724:tibero7",
        "user":     "DEV_USER2",
        "password": "..."
      }
    },
    "oracle": {
      "_defaults": { "driver": "oracle.jdbc.driver.OracleDriver", "jars": "jars/ojdbc-8.jar" },
      "dev1": {
        "desc":     "C 시스템 개발 오라클",
        "url":      "jdbc:oracle:thin:@<DB호스트>:1521:orcl",
        "user":     "DEV_USER3",
        "password": "..."
      }
    }
  }
}
```

- 각 연결에 `desc` 를 적어두면 `list_connections` 결과에 노출되어 자연어로 DB를 고를 때 쓰인다.
- 같은 DB의 다른 환경(개발/테스트/운영)이나 다른 계정은 같은 그룹 안에 연결을 더 추가하면 된다.
- `_defaults` 는 예약 키다 — 연결 이름으로 쓸 수 없다.
- 특정 연결만 driver/jars 가 다르면 그 연결에 직접 적으면 `_defaults` 를 덮어쓴다.

### DB별 driver / url 예시

| DB | driver | url |
|----|--------|-----|
| Tibero | `com.tmax.tibero.jdbc.TbDriver` | `jdbc:tibero:thin:@host:8629:tibero` |
| Oracle | `oracle.jdbc.driver.OracleDriver` | `jdbc:oracle:thin:@host:1521:ORCL` |
| PostgreSQL | `org.postgresql.Driver` | `jdbc:postgresql://host:5432/db` |
| MySQL | `com.mysql.cj.jdbc.Driver` | `jdbc:mysql://host:3306/db` |
| MariaDB | `org.mariadb.jdbc.Driver` | `jdbc:mariadb://host:3306/db` |

- `jars` 경로는 **상대경로면 connections.json 이 있는 폴더 기준**으로 해석된다(예: `jars/ojdbc-8.jar`). 절대경로도 가능하다.
- jar 가 여러 개면 `;` 또는 `,` 로 구분하거나 배열로 적어도 된다.
- 서버는 등록된 **모든** jar 를 한 classpath 에 올리므로, 어떤 연결을 먼저 호출하든 모든 드라이버를 쓸 수 있다.

### 새 연결 / 새 DBMS 추가 시

| 상황 | 필요한 작업 | 재시작 |
|------|-------------|--------|
| 기존 그룹과 **같은 드라이버(jar)** 를 쓰는 연결 추가 (예: 티베로 계정 하나 더) | `connections.json` 에 연결 항목 추가 | **불필요** (다음 호출에 즉시 반영) |
| **새 드라이버 jar 가 필요한 DBMS** 추가 (예: 처음으로 MySQL 추가) | jar 를 `jars/` 에 넣고 `connections.json` 에 그룹/연결 추가 | **필요** |

새 jar 가 재시작을 요구하는 이유: JVM 의 classpath 는 **첫 DB 연결 때 한 번만 고정**된다.
이미 서버가 떠서 한 번이라도 연결을 맺은 뒤 새 jar 를 추가하면 그 jar 는 classpath 에 올라가지 않는다.
따라서 새 드라이버를 추가했으면 서버 프로세스(또는 컨테이너)를 재기동해야 한다.

- 로컬 stdio: Claude Code 를 재시작(MCP 서버 재기동).
- Docker: `docker compose restart jdbc-mcp`.
- systemd: `sudo systemctl restart jdbc-mcp`.

## 연결 먼저 검증

```powershell
python test_connection.py                 # default 연결
python test_connection.py tibero7/dev1     # 특정 연결 (그룹/연결 형식)
```
연결 목록 → 접속 → 테이블 조회 → 읽기 전용 검증의 4단계 점검이 모두 통과하면 붙일 준비 끝.

## Claude Code에 등록 (로컬 단독 / stdio)

`.mcp.json` 은 한 번만 설정한다(이미 생성됨, git 에는 올리지 않음):

```json
{
  "mcpServers": {
    "jdbc": {
      "command": "python",
      "args": ["<프로젝트_경로>\\jdbc_mcp.py"],
      "env": {
        "JDBC_CONNECTIONS": "<프로젝트_경로>\\connections.json",
        "DB_MAX_ROWS": "200"
      }
    }
  }
}
```

이 폴더에서 Claude Code 실행 시 `jdbc` 서버가 자동 인식된다. `/mcp` 로 상태 확인.

### 두 가지 사용 모드

| 모드 | 전송 | 용도 | 방법 |
|------|------|------|------|
| **로컬 단독** | stdio | 내 PC에서 혼자 사용/개발 | 위 `.mcp.json` (기본값) |
| **팀 공용** | HTTP | 서버에 배포해 팀원이 함께 사용 | 아래 [팀 공용 배포](#팀-공용-배포--http) (Docker) — 자세히는 [DEPLOY.md](DEPLOY.md) |

## 팀 공용 배포 — HTTP

서버를 한 번 띄워두고 팀원은 각자 Claude Code 에서 URL 만 등록해 함께 쓴다.
서버는 `MCP_TRANSPORT=http` 로 실행되어 `http://<서버IP>:8000/mcp` 로 노출된다.

```
팀원 PC들의 Claude Code ──HTTP──► [서버: jdbc_mcp.py + JDK + jars] ──JDBC──► DB
```

### Docker 로 배포 (권장)

이 저장소의 `Dockerfile` / `docker-compose.yml` 를 그대로 쓴다.
**코드(jdbc_mcp.py)만 이미지에 굽고, `connections.json` 과 `jars/` 는 read-only 볼륨으로 마운트**한다
— 비밀번호가 이미지에 박히지 않고, 접속 정보를 고쳐도 재빌드가 필요 없다.

1. 서버에 Docker / Docker Compose 설치, 저장소 파일 배치.
2. **`connections.json` 준비** — jar 는 상대경로 `jars/...` 로 적는다(컨테이너 안 `/app/jars` 로 마운트됨).
   서버에서 각 DB 로 네트워크 접속이 되는지 먼저 확인.
3. 빌드 & 기동:
   ```bash
   docker compose up -d --build
   docker compose logs -f jdbc-mcp     # 로그 확인
   ```
4. `http://<서버IP>:8000/mcp` 로 노출된다. 방화벽에서 **사내망에만** 8000 포트를 연다.

운영 중 변경 시:

| 변경 내용 | 명령 |
|-----------|------|
| `connections.json` 값 수정(기존 드라이버) | 없음 — 다음 호출에 즉시 반영 |
| `jars/` 에 **새 드라이버 jar** 추가 | `docker compose restart jdbc-mcp` (JVM classpath 재초기화) |
| 코드(`jdbc_mcp.py`) 수정 | `docker compose up -d --build` (이미지 재빌드) |

> 환경변수(`MCP_PORT`, `DB_MAX_ROWS` 등)는 `docker-compose.yml` 에서 조정한다.

### 팀원 등록

각 팀원 PC 에서(설치할 것 없음, URL 만 등록):

```bash
claude mcp add --transport http jdbc http://<서버IP>:8000/mcp
```

등록 후 `/mcp` 로 연결 확인.

## 사용 예 (자연어)

- "등록된 DB 연결 뭐뭐 있어?" → `list_connections`
- "USER 테이블에 데이터 몇 건이야?" → default 연결로 `run_query`

## 안전장치

- **허용 문장**: 조회(SELECT/WITH) + 변경(INSERT/UPDATE/DELETE/MERGE). **DDL(CREATE/DROP/ALTER/TRUNCATE)은 차단**.
- **다중 문장 차단**: `;` 로 이어붙인 쿼리 거부
- **조회 행 수 제한**: `DB_MAX_ROWS` (기본 200)
- ⚠ **변경 문장은 자동 커밋되어 되돌릴 수 없다.** UPDATE/DELETE 는 WHERE 절 누락 시 전체 행이 바뀌므로 주의.
- **권장**: DB 계정 권한을 실제 필요한 범위로 제한하라. 조회만 필요한 연결은 **읽기 전용 계정**을 쓰면 코드 차단과 DB 권한으로 이중 방어가 된다.

## 보안 주의

- `connections.json` 에는 비밀번호가 들어 있으므로 git 등에 커밋하지 말 것 (`.gitignore` 에 포함됨).
- HTTP(팀 공용) 모드는 **사내망에서만** 접근 가능하도록 방화벽으로 제한한다(외부 인터넷 차단).
- 더 엄격히 하려면 nginx 리버스 프록시 뒤에 두고 토큰/IP 화이트리스트를 둔다.
