#!/usr/bin/env bash
set -euo pipefail

# 杜邦分析 v5.1 — Docker Compose 部署脚本
# 用法: ./deploy.sh [up|down|rebuild|logs]

CMD="${1:-up}"

case "$CMD" in
  up)
    echo "🚀 启动服务..."
    docker compose up -d
    echo "✅ 服务已启动: http://$(curl -s ifconfig.me):8765"
    ;;
  down)
    echo "🛑 停止服务..."
    docker compose down
    echo "✅ 服务已停止"
    ;;
  rebuild)
    echo "🔧 重新构建并启动..."
    docker compose down
    docker compose build --no-cache
    docker compose up -d
    echo "✅ 重建完成"
    ;;
  logs)
    docker compose logs -f --tail=50
    ;;
  *)
    echo "用法: $0 [up|down|rebuild|logs]"
    exit 1
    ;;
esac
