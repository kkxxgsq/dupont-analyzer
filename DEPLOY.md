# 杜邦分析 v7.1 — VPS 部署指南

## 前置要求

- Docker + Docker Compose v2
- 开放端口 8765（可在 `docker-compose.yml` 中修改映射端口）

## 快速部署

### 方案一：使用 `versions/v7.1/` 部署包（推荐）

VPS 上拉取最新代码后部署：

```bash
git clone https://github.com/kkxxgsq/dupont-analyzer.git
cd dupont-analyzer/versions/v7.1
./deploy.sh rebuild
```

### 方案二：从本地 rsync 到 VPS

```bash
rsync -avz --exclude 'cache/' --exclude 'versions/' --exclude '__pycache__/' \
  . user@your-vps:/opt/dupont-analyzer/

ssh user@your-vps
cd /opt/dupont-analyzer
./deploy.sh rebuild
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `docker compose up -d` | 启动服务（后台） |
| `docker compose down` | 停止服务 |
| `docker compose logs -f` | 查看实时日志 |
| `docker compose build` | 重新构建镜像 |
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

## 更新升级

```bash
cd /opt/dupont-analyzer
git pull                          # 拉取最新代码
docker compose down               # 停旧容器
docker compose build              # 重新构建
docker compose up -d              # 启动新版
```
