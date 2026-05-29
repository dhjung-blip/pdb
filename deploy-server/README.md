# deploy-server/ — 서버 배포 자산 (선택적/레거시)

이 폴더는 PDB MCP 서버를 **사내 Linux 서버에 Docker 컨테이너로 중앙 배포**하던
시기의 자산입니다. 본 프로젝트의 **1차 배포 경로는 `.mcpb` 번들**(연구원 PC에
Claude Desktop이 직접 설치)로 전환되었습니다.

이 폴더는 다음 경우에만 사용하세요:
- 사내 표준이 중앙 서버 + Apache 리버스 프록시 구조여야 하는 경우
- 동시 다수 연구원이 단일 인스턴스 로그/모니터링을 공유해야 하는 경우
- 외부에서 Streamable HTTP / SSE 클라이언트로 접근해야 하는 경우

위가 아니라면 프로젝트 루트의 `.mcpb` 배포 흐름을 사용하세요.

---

## 구성

| 파일 | 역할 |
|---|---|
| `Dockerfile` | 멀티스테이지 이미지 (Python 3.12-slim, 비루트 `mcp` 유저, healthcheck) |
| `docker-compose.yml` | streamable-http transport, `127.0.0.1:8000`만 노출, named volume |
| `apache/pdb-mcp.conf` | Apache 80 → `127.0.0.1:8000` 리버스 프록시 (`/mcp/*`) |
| `deploy.sh` | 로컬 빌드 + Docker Hub push (`./deploy.sh <user> [tag]`) |

## 빌드 / 실행

빌드 컨텍스트는 **프로젝트 루트**입니다 (`server.py`, `tools/`, `models/`,
`requirements.txt` 가 거기 있어서). 따라서 `-f deploy-server/Dockerfile` 로
경로를 명시합니다.

```bash
cd /Users/jungdohoon/Desktop/PDBMCP

# 이미지 빌드
docker build -f deploy-server/Dockerfile -t pdb-mcp-server:latest .

# compose 로 띄우기 (compose 파일은 deploy-server/ 안)
docker compose -f deploy-server/docker-compose.yml up -d
```

Apache 설정은 `deploy-server/apache/pdb-mcp.conf`를 사내 서버의
`/etc/apache2/sites-available/`에 복사한 뒤 `a2ensite`로 활성화합니다.

## 관련 환경변수 (`server.py --transport streamable-http`)

- `PDB_MCP_OUTPUT_DIR` — Excel 저장 경로 (compose 기본값 `/app/outputs`)
- `PDB_MCP_HTTP_PATH` — MCP HTTP transport 경로 (기본 `/mcp`)
- `PDB_MCP_PUBLIC_PREFIX` — Apache 프리픽스 (기본 `/mcp`)
- `PDB_MCP_PUBLIC_BASE_URL` — 응답에 다운로드 URL로 표시할 외부 베이스 URL
