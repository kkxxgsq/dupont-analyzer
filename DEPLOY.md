# 杜邦分析 v4.5 — VPS 部署指南

## 前置要求

- Docker + Docker Compose v2
- 开放端口 8765（可在 `docker-compose.yml` 中修改映射端口）

## 快速部署

```bash
# 1. 上传项目到 VPS
rsync -avz --exclude 'cache/' --exclude 'versions/' --exclude '__pycache__/' \
  . user@your-vps:/opt/dupont-analyzer/

# 2. SSH 到 VPS 后
cd /opt/dupont-analyzer
chmod +x deploy.sh

# 3. 构建并启动
./deploy.sh rebuild

# 4. 查看日志
./deploy.sh logs
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `docker compose up -d` | 启动服务（后台） |
| `docker compose down` | 停止服务 |
| `docker compose logs -f` | 查看实时日志 |
| `docker compose build --no-cache` | 重新构建镜像 |
| `./deploy.sh rebuild` | 重建并启动 |

## 数据持久化

- **`dupont-cache`** volume：缓存已抓取的财报数据（24h 有效）
- **`dupont-akshare`** volume：akshare 本地数据目录
- 容器销毁后缓存不丢失

## 资源限制

- 内存上限：2G（`docker-compose.yml` 中可调整）
- 日志上限：每个文件 10MB，保留 3 个轮转

## Nginx 反向代理（可选）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 数据源说明

- 数据来自东方财富（通过 akshare 接口）
- A 股首次加载需 1-3 分钟（数据量大），后续走缓存
- 缓存默认存在 24 小时，可通过 `http://your-vps:8765/api/cache-clear` 清除
