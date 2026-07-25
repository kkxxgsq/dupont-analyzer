# v3.2 Docker 部署版本（终版）

**保存时间**：2026-07-24

## v3.2 对比 v3.0 修复

| Bug | 修复 |
|---|---|
| 多股对比图表无数据 | Chart.js 实例重复创建 → 加 `chartInstances` 管理 + `destroyChart()` 先销毁 |
| 对比 API 返回内部 500 错误 | `/api/compare` 用 `JSONResponse(json.dumps(..., default=str))` |

## Docker 部署

```bash
cd /opt/dupont-analyzer/
docker compose down
docker compose up -d --build --no-cache
```

部署文件：`Dockerfile` + `docker-compose.yml` + `.dockerignore` + `requirements.txt`

## v3.0 核心功能（保留）

- 商业模式判定（6 类：品牌/周转/网络/杠杆/周期/亏损）
- 解读按模式给出重点指标 + 策略建议
- 模糊搜索（A 股/港股/美股）
- 同类推荐 + 市场参考兜底
- 刷新单股/全部按钮
- emoji 表注（🏆/⚠️/🔔）
- 24h 文件缓存