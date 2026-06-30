# 리눅스 서버 배포 가이드 (팀 공용 / HTTP 전송)

내부망 리눅스 서버에 MCP 서버를 한 번 띄워두고, 팀원들은 각자 Claude Code에서
URL만 등록해 함께 쓰는 구성.

```
팀원 PC들의 Claude Code ──HTTP──► [리눅스: Docker 컨테이너] ──JDBC──► DB
```

## 1. Docker 로 배포

Docker / Docker Compose 가 설치된 서버에서, 저장소 파일을 두고
`connections.json`(리눅스 경로·`jars/...` 상대경로로 작성)만 준비하면 끝.

```bash
cd <저장소_폴더>          # connections.json 과 jars/ 가 있는 위치
docker compose up -d --build
docker compose logs -f jdbc-mcp     # 동작 확인
```

→ `http://<서버IP>:8000/mcp` 로 노출된다. 방화벽에서 **사내망에만** 8000 포트를 연다.

운영 중 변경:

| 변경 내용 | 명령 |
|-----------|------|
| `connections.json` 값 수정(기존 드라이버) | 없음 — 다음 호출에 즉시 반영 |
| `jars/` 에 **새 드라이버 jar** 추가 | `docker compose restart jdbc-mcp` |
| 코드(`jdbc_mcp.py`) 수정 | `docker compose up -d --build` |

> 코드만 이미지에 굽고 `connections.json`·`jars/` 는 read-only 볼륨으로 마운트하므로(비밀번호가 이미지에 안 박힘),
> 접속 정보를 고쳐도 재빌드가 필요 없다. 자세한 설정은 `docker-compose.yml` 참고.

## 2. 팀원 각자 — Claude Code에 등록

각 팀원 PC에서 (설치할 것 없음, URL만 등록):

```bash
claude mcp add --transport http jdbc http://<서버IP>:8000/mcp
```

등록 후 `/mcp` 로 연결 확인. 이제 팀원 누구나 "lcard DB에서 ... 조회해줘" 가능.

## 3. 보안 (내부망 권장 설정)

- **사내망에서만 접근 가능하게** 방화벽 제한 (외부 인터넷 차단).
- 가능하면 **읽기 전용 전용 DB 계정**을 만들어 connections.json 에 사용 (코드 차단 + DB 권한 이중 방어).
- connections.json 에 비밀번호가 있으므로 서버 파일 권한을 제한: `chmod 600 connections.json`.
- 더 엄격히 하려면 nginx 리버스 프록시 뒤에 두고 토큰/IP 화이트리스트 추가.
