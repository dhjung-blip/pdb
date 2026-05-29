#!/bin/bash
# 사용법: ./deploy.sh <docker-hub-username> [버전태그]
# 예시:   ./deploy.sh myusername v1.0

USERNAME=${1:?"Docker Hub 사용자명을 입력하세요. 예: ./deploy.sh myusername"}
VERSION=${2:-latest}
IMAGE="$USERNAME/pdb-mcp-server"

echo "=== PDB MCP Server 빌드 및 배포 ==="
echo "이미지: $IMAGE:$VERSION"

docker build -t "$IMAGE:$VERSION" -t "$IMAGE:latest" .
docker push "$IMAGE:$VERSION"
docker push "$IMAGE:latest"

echo ""
echo "=== 완료 ==="
echo "사내 서버에서 실행할 명령어:"
echo "  docker compose pull && docker compose up -d"
