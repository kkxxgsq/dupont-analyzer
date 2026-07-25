import asyncio
import json
import shutil
import os
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from analyzer import fetch, detect_market, search_stocks, result_to_dict, CACHE_DIR

app = FastAPI(title="杜邦分析 v4.2")
executor = ThreadPoolExecutor(max_workers=4)

@app.get("/api/cache-clear")
async def api_clear_cache():
    if os.path.exists(CACHE_DIR):
        shutil.rmtree(CACHE_DIR)
        os.makedirs(CACHE_DIR, exist_ok=True)
    return {"status": "ok", "msg": "缓存已清除"}

@app.get("/api/refresh")
async def api_refresh(code: str = Query(), market: str = Query()):
    """清除单个股票缓存并重新拉取"""
    from analyzer import _cache_key, CACHE_DIR
    key = _cache_key(code, market)
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path):
        os.remove(path)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(executor, fetch, code, market)
    if res is None or not res.years:
        raise HTTPException(404, f"未能获取 {code} 的财务数据，请检查股票代码是否正确。")
    return JSONResponse(content=json.loads(json.dumps(result_to_dict(res), default=str)))

# ── API ─────────────────────────────────────────────────────────
@app.get("/api/search")
async def api_search(q: str = Query(min_length=1)):
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(executor, search_stocks, q)
    return {"results": results}

@app.get("/api/dupont")
async def api_dupont(code: str = Query(), market: str = Query(default=None)):
    m = market or detect_market(code)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(executor, fetch, code, m)
    if res is None or not res.years:
        raise HTTPException(404, f"未能获取 {code} 的财务数据，请检查股票代码是否正确。")
    return JSONResponse(content=json.loads(json.dumps(result_to_dict(res), default=str)))

@app.get("/api/compare")
async def api_compare(codes: str = Query(description="Comma separated: JD,BABA,AMZN"),
                      markets: str = Query(default="")):
    clist = [c.strip() for c in codes.split(",") if c.strip()]
    mlist = [m.strip() for m in markets.split(",") if m.strip()] if markets else []
    while len(mlist) < len(clist):
        mlist.append(detect_market(clist[len(mlist)]))
    mlist = mlist[:len(clist)]

    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(executor, fetch, c, m) for c, m in zip(clist, mlist)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out = []
    for c, r in zip(clist, results):
        if isinstance(r, Exception) or r is None or not r.years:
            out.append({"company": {"code": c, "name": c}, "error": "获取失败", "years": []})
        else:
            out.append(result_to_dict(r))
    return JSONResponse(content=json.loads(json.dumps({"results": out}, default=str)))

# ── frontend ────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>杜邦分析 v4.2</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
body{background:#f5f7fa;color:#1a1a2e;min-height:100vh}
.header{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);color:#fff;padding:28px 40px}
.header h1{font-size:22px;font-weight:700;letter-spacing:0.5px}
.header p{font-size:13px;color:#8899b0;margin-top:4px}
.container{max-width:1400px;margin:0 auto;padding:24px}
.search-card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:20px;overflow:visible}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:20px;overflow:hidden}
.card-header{padding:16px 24px;border-bottom:1px solid #edf2f7;font-weight:600;font-size:15px;display:flex;align-items:center;gap:10px}
.card-body{padding:20px 24px}
/* search */
.search-bar{display:flex;gap:10px;flex-wrap:wrap}
.search-bar input{flex:1;min-width:200px;padding:10px 16px;border:1px solid #d1d9e6;border-radius:8px;font-size:14px;outline:none;transition:.2s}
.search-bar input:focus{border-color:#4a6cf7;box-shadow:0 0 0 3px rgba(74,108,247,.1)}
.market-tabs{display:flex;gap:2px;background:#f0f2f5;border-radius:8px;padding:3px}
.market-tab{padding:8px 16px;border:none;background:transparent;border-radius:6px;cursor:pointer;font-size:13px;font-weight:500;color:#5a6a7e;transition:.15s}
.market-tab.active{background:#fff;color:#1a1a2e;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.btn{padding:10px 24px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;transition:.15s}
.btn-primary{background:#4a6cf7;color:#fff}
.btn-primary:hover{background:#3b5de7}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.btn-sm{padding:6px 14px;font-size:13px;font-weight:500}
.btn-outline{background:transparent;border:1px solid #d1d9e6;color:#5a6a7e}
.btn-outline:hover{background:#f5f7fa}
.btn-outline.active{background:#e8edfd;border-color:#4a6cf7;color:#4a6cf7}
/* stocks selector */
.stock-tags{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0}
.stock-tag{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;background:#e8edfd;border-radius:20px;font-size:13px;color:#4a6cf7}
.stock-tag .del{cursor:pointer;font-weight:700;font-size:15px;color:#8899b0;line-height:1}
.stock-tag .del:hover{color:#e74c3c}
/* tables */
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:10px 14px;text-align:right;white-space:nowrap;border-bottom:1px solid #edf2f7}
th{background:#f8fafc;font-weight:600;color:#5a6a7e;font-size:12px;text-transform:uppercase;letter-spacing:.3px;position:sticky;top:0}
td{color:#1a1a2e}
td:first-child,th:first-child{text-align:left;font-weight:600}
tr:hover td{background:#f8fafc}
.text-green{color:#10b981}
.text-red{color:#ef4444}
.text-muted{color:#8899b0;font-size:12px}
/* charts */
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:16px}
@media(max-width:900px){.chart-grid{grid-template-columns:1fr}}
.chart-box{padding:12px;min-height:260px}
.chart-box canvas{width:100%!important;height:260px!important}
/* multi stock compare */
.flex-row{display:flex;gap:24px;flex-wrap:wrap}
.flex-col{flex:1;min-width:280px}
.stat-card{padding:16px;background:#f8fafc;border-radius:8px;text-align:center}
.stat-card .num{font-size:24px;font-weight:700;color:#1a1a2e}
.stat-card .lbl{font-size:12px;color:#8899b0;margin-top:2px}
.stat-card .sub{font-size:11px;color:#8899b0;margin-top:4px}
/* analysis text */
.analysis-box{line-height:1.8;font-size:14px;color:#2d3748}
.analysis-box p{margin:6px 0}
.analysis-box .highlight{color:#4a6cf7;font-weight:600}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600}
.badge-up{background:#d1fae5;color:#065f46}
.badge-down{background:#fee2e2;color:#991b1b}
/* tabs */
.tab-bar{display:flex;gap:0;border-bottom:1px solid #edf2f7;padding:0 24px}
.tab-item{padding:12px 20px;font-size:14px;font-weight:500;color:#5a6a7e;cursor:pointer;border-bottom:2px solid transparent;transition:.15s}
.tab-item:hover{color:#1a1a2e}
.tab-item.active{color:#4a6cf7;border-bottom-color:#4a6cf7}
.tab-content{display:none}
.tab-content.active{display:block}
/* loading */
.loading{text-align:center;padding:60px;color:#8899b0}
.spinner{display:inline-block;width:28px;height:28px;border:3px solid #e2e8f0;border-top-color:#4a6cf7;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
/* year analysis */
.year-compare{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.year-card{padding:20px;border-radius:10px}
.year-card.best{background:#ecfdf5;border:1px solid #a7f3d0}
.year-card.worst{background:#fef2f2;border:1px solid #fecaca}
.year-card h3{font-size:16px;margin-bottom:8px}
.year-card .val{font-size:28px;font-weight:700}
.year-card .items{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
.year-card .item .l{font-size:11px;color:#6b7280}
.year-card .item .v{font-size:15px;font-weight:600}
.error-box{color:#dc2626;background:#fef2f2;padding:20px;border-radius:8px;font-size:14px}
</style>
</head>
<body>

<div class="header">
  <h1>杜邦分析 <span style="font-size:11px;color:#8899b0;font-weight:400">v4.2</span></h1>
  <p>基于公开财报数据的杜邦分解（支持美股/港股/A股）</p>
</div>

<div class="container">
  <!-- ── input ── -->
<div class="search-card">
    <div class="card-body">
      <div class="search-bar">
        <input id="stockInput" type="text" placeholder="输入股票代码或名称，如 JD / 京东 / 00700 / 600519" onkeydown="if(event.key==='Enter') addStock()">
        <button class="btn btn-primary" onclick="addStock()">添加</button>
        <div style="display:flex;gap:8px">
          <button class="btn btn-sm btn-outline" data-code="JD,PDD,BABA,AMZN" onclick="quickCompare(this)">电商对比</button>
          <button class="btn btn-sm btn-outline" data-code="600519.SH,000858.SZ,000568.SZ" onclick="quickCompare(this)">白酒对比</button>
          <button class="btn btn-sm btn-outline" data-code="00700.HK,03690.HK,09988.HK" onclick="quickCompare(this)">港股科技</button>
        </div>
      </div>
      <div class="stock-tags" id="stockTags"></div>
    </div>
  </div>

  <!-- ── main content ── -->
  <div id="mainContent">
  </div>
</div>

<script>
const stockTags = document.getElementById('stockTags');
const mainContent = document.getElementById('mainContent');
const stockInput = document.getElementById('stockInput');
const stocks = [];

function detectMarket(code) {
  const c = code.toUpperCase();
  if (/\.(HK|SH|SZ|BJ)$/.test(c)) return c.includes('HK')?'hk':c.includes('SH')||c.includes('SZ')||c.includes('BJ')?'a':'us';
  if (/^\d{6}$/.test(code)) return 'a';
  if (/^\d{5}$/.test(code)) return 'hk';
  return 'us';
}

// ── autocomplete dropdown ──
const acBox = document.createElement('div');
acBox.style.cssText = 'position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid #d1d9e6;border-radius:8px;margin-top:4px;max-height:300px;overflow-y:auto;display:none;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,.1)';
stockInput.parentElement.style.position = 'relative';
stockInput.parentElement.appendChild(acBox);

let acTimer = null;
stockInput.addEventListener('input', () => {
  clearTimeout(acTimer);
  acBox.style.display = 'none';
  const v = stockInput.value.trim();
  if (!v || v.length < 1) return;
  acTimer = setTimeout(async () => {
    try {
      const resp = await fetch(`/api/search?q=${encodeURIComponent(v)}`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data.results || !data.results.length) return;
      acBox.innerHTML = data.results.map(r =>
        `<div class="ac-item" data-code="${r.code}" data-market="${r.market}" data-name="${r.name}" onclick="selectAc(this)">${r.name} <span class="text-muted">${r.code} [${r.market.toUpperCase()}]</span></div>`
      ).join('');
      acBox.style.display = 'block';
    } catch(e) {}
  }, 300);
});
document.addEventListener('click', (e) => { if (!e.target.closest('.search-bar')) acBox.style.display = 'none'; });
const acStyle = document.createElement('style');
acStyle.textContent = '.ac-item{padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:1px solid #edf2f7}.ac-item:hover,.ac-item.hl{background:#f0f4ff}.ac-item:last-child{border:none}';
document.head.appendChild(acStyle);

function selectAc(el) {
  acBox.style.display = 'none';
  stockInput.value = '';
  addStockWithInfo(el.dataset.code, el.dataset.market, el.dataset.name);
}

// ── add stock ──
async function addStock(code) {
  const val = (code || stockInput.value).trim();
  if (!val) return;
  stockInput.value = '';

  // If it looks like a name (has Chinese), search first
  if (/[\u4e00-\u9fff]/.test(val)) {
    try {
      const resp = await fetch(`/api/search?q=${encodeURIComponent(val)}`);
      if (resp.ok) {
        const data = await resp.json();
        if (data.results && data.results.length > 0) {
          const r = data.results[0];
          for (const s of stocks) { if (s.code.toUpperCase() === r.code.toUpperCase()) return; }
          return addStockWithInfo(r.code, r.market, r.name);
        }
      }
    } catch(e) {}
    return showErrorMsg(`未找到"${val}"，请尝试股票代码如 03690.HK（美团）、NVO（诺和诺德）`);
  }

  if (stocks.some(s => s.code.toUpperCase() === val.toUpperCase())) {
    const idx = stocks.findIndex(s => s.code.toUpperCase() === val.toUpperCase());
    if (idx !== -1) selectStock(idx);
    return;
  }
  addStockWithInfo(val, detectMarket(val), val);
}

function addStockWithInfo(code, market, name) {
  const idx = stocks.length;
  stocks.push({code, market, name, loading: true, error: false, errorMsg: ''});
  renderTags();
  showLoadingMsg(name);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90000);
  const marketName = {us:'美股', hk:'港股', a:'A股'}[market] || market;

  fetch(`/api/dupont?code=${encodeURIComponent(code)}&market=${market}`, {signal: controller.signal})
    .then(resp => {
      if (!resp.ok) throw new Error(`获取失败（${marketName}），请检查股票代码`);
      return resp.json();
    })
    .then(data => {
      clearTimeout(timeout);
      stocks[idx] = {code, market, name: data.company.name, loading: false, error: false, errorMsg: '', data};
      renderTags();
      renderMain();
    })
    .catch(e => {
      clearTimeout(timeout);
      const msg = e.name === 'AbortError' ? '请求超时，请检查网络或稍后重试' : e.message;
      stocks[idx] = {code, market, name, loading: false, error: true, errorMsg: msg};
      renderTags();
      renderMain();
    });
}

function removeStock(idx) {
  stocks.splice(idx, 1);
  renderTags();
  if (stocks.length === 0) {
    if (loadingTimer) clearInterval(loadingTimer);
    mainContent.innerHTML = EMPTY_HTML;
  } else {
    renderMain();
  }
}

const EMPTY_HTML = `<div class="card"><div class="card-body" style="text-align:center;padding:60px 20px;color:#8899b0">
  <p style="font-size:15px;margin-bottom:6px">搜索并添加至少一只股票开始分析</p>
  <p style="font-size:12px">支持中文名或代码（京东 / JD / 00700.HK / 600519.SH）</p>
  <p style="font-size:12px;margin-top:4px">美股港股秒级响应，A股首次加载约需 1-3 分钟（数据量大），之后会缓存到本地</p>
  <div style="margin-top:16px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
    <a class="quick-link" onclick="addStock('JD')" style="cursor:pointer;background:#f0f2f5;border-radius:6px;padding:4px 12px;font-size:12px;text-decoration:none;color:inherit">⚡ 京东</a>
    <a class="quick-link" onclick="addStock('00700.HK')" style="cursor:pointer;background:#f0f2f5;border-radius:6px;padding:4px 12px;font-size:12px;text-decoration:none;color:inherit">⚡ 腾讯</a>
    <a class="quick-link" onclick="addStock('600519.SH')" style="cursor:pointer;background:#f0f2f5;border-radius:6px;padding:4px 12px;font-size:12px;text-decoration:none;color:inherit">⚡ 茅台</a>
    <a class="quick-link" onclick="addStock('NVO')" style="cursor:pointer;background:#f0f2f5;border-radius:6px;padding:4px 12px;font-size:12px;text-decoration:none;color:inherit">⚡ 诺和诺德</a>
    <a class="quick-link" onclick="addStock('BABA')" style="cursor:pointer;background:#f0f2f5;border-radius:6px;padding:4px 12px;font-size:12px;text-decoration:none;color:inherit">⚡ 阿里巴巴</a>
    <a class="quick-link" onclick="addStock('TSLA')" style="cursor:pointer;background:#f0f2f5;border-radius:6px;padding:4px 12px;font-size:12px;text-decoration:none;color:inherit">⚡ 特斯拉</a>
  </div>
</div></div>`;

async function quickCompare(btn) {
  const codes = btn.dataset.code.split(',');
  for (const c of codes) {
    if (!stocks.some(s => s.code.toUpperCase() === c.toUpperCase())) {
      await addStock(c);
    }
  }
  const tab = document.querySelector('.tab-item[data-tab="compare"]');
  if (tab) switchTab(tab);
}

function renderTags() {
  stockTags.innerHTML = stocks.map((s, i) => {
    const isActive = i === activeIdx;
    const bg = s.error ? '#fef2f2' : (isActive ? '#dbe4ff' : '#e8edfd');
    const border = isActive ? '2px solid #4a6cf7' : '2px solid transparent';
    if (s.error && s.errorMsg) {
      return `<span class="stock-tag" data-idx="${i}" onclick="selectStock(${i})" title="${s.errorMsg}" style="background:${bg};color:#dc2626;border:${border};cursor:pointer">⚠️ ${s.name} <span class="del" onclick="event.stopPropagation();removeStock(${i})">×</span></span>`;
    }
    return `<span class="stock-tag" data-idx="${i}" onclick="selectStock(${i})" style="background:${bg};border:${border};cursor:pointer">${s.loading ? '⏳' : ''} ${s.name} <span class="del" onclick="event.stopPropagation();removeStock(${i})">×</span></span>`;
  }).join('');
}

let activeIdx = -1;

function selectStock(idx) {
  activeIdx = idx;
  renderTags();
  const s = stocks[idx];
  if (s.data && !s.error && !s.loading) {
    renderSingle(s);
  } else if (s.error) {
    showErrorMsg(s.errorMsg);
  } else {
    showLoadingMsg(s.name);
  }
}

let loadingTimer = null;

function showLoadingMsg(name) {
  const startTime = Date.now();
  mainContent.innerHTML = `<div class="card"><div class="card-body"><div class="loading"><div class="spinner"></div><p style="margin-top:12px;font-size:15px;font-weight:500">正在获取 ${name} 的财务数据…</p><p style="margin-top:6px;font-size:13px;color:#8899b0" id="loadingHint">首次加载可能需 1-3 分钟（A股较慢），数据会缓存到本地</p></div></div></div>`;
  if (loadingTimer) clearInterval(loadingTimer);
  loadingTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const hint = document.getElementById('loadingHint');
    if (hint) {
      if (elapsed > 60) hint.textContent = `⏳ 已等待 ${elapsed}s，A股数据量较大请耐心等待…`;
      else if (elapsed > 30) hint.textContent = `⏳ 已等待 ${elapsed}s，即将完成…`;
    }
  }, 5000);
}

function showErrorMsg(msg) {
  if (loadingTimer) clearInterval(loadingTimer);
  mainContent.innerHTML = `<div class="card"><div class="card-body"><div class="error-box">⚠️ ${msg}</div></div></div>`;
}

// ── render main ────────────────────────────────────────────────
function renderMain() {
  const ready = stocks.filter(s => s.data && !s.loading && !s.error);
  if (ready.length === 0) {
    mainContent.innerHTML = `<div class="card"><div class="card-body" style="text-align:center;padding:60px 20px;color:#8899b0"><p>数据加载中或添加更多股票…</p></div></div>`;
    return;
  }
  if (ready.length === 1) {
    activeIdx = stocks.findIndex(s => s.data && !s.loading && !s.error);
    renderSingle(ready[0]);
  } else {
    if (activeIdx >= 0) {
      const s = stocks[activeIdx];
      if (s && s.data && !s.error && !s.loading) {
        renderSingle(s);
        return;
      }
    }
    renderCompare(ready);
  }
}

function yearCell(y, markings, cautionYears) {
  let tag = '';
  if (markings.best === y.year) tag = ' 🏆';
  else if (markings.worst === y.year) tag = ' ⚠️';
  if (cautionYears.includes(y.year)) tag = ' 🔔';
  return `<tr>
    <td>${y.year}${tag}</td>
    <td>${y.revenue.toFixed(2)}</td>
    <td>${y.net_profit.toFixed(2)}</td>
    <td>${y.npm.toFixed(2)}</td>
    <td>${y.at.toFixed(4)}</td>
    <td>${y.em.toFixed(4)}</td>
    <td><strong class="${y.roe > 10 ? 'text-green' : y.roe < 0 ? 'text-red' : ''}">${y.roe.toFixed(2)}%</strong></td>
  </tr>`;
}

function dupontTable(years, markings, cautionYears) {
  let html = `<div class="table-wrap"><table>
    <tr><th>年份</th><th>营收(亿)</th><th>净利润(亿)</th><th>净利率</th><th>周转率</th><th>权益乘数</th><th>ROE</th></tr>`;
  years.forEach(y => html += yearCell(y, markings, cautionYears));
  return html + `</table></div>`;
}

function renderProfile(p) {
  if (!p) return '';
  return `
    <div style="margin-bottom:18px;padding:16px 20px;background:#f5f7fb;border-radius:10px;border-left:4px solid #4a6cf7">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <span style="background:#4a6cf7;color:#fff;border-radius:4px;padding:2px 10px;font-size:12px;font-weight:600">${p.industry}</span>
      </div>
      <div style="font-size:14px;color:#1a1a2e;font-weight:500;margin-bottom:6px">${p.desc}</div>
      <div style="font-size:13px;color:#4a5568;line-height:1.7;margin-bottom:6px"><strong>主营：</strong>${p.biz}</div>
      <div style="font-size:13px;color:#4a5568;line-height:1.7;margin-bottom:6px"><strong>护城河：</strong>${p.moat}</div>
      <div style="font-size:13px;color:#4a5568;line-height:1.7"><strong>周期属性：</strong>${p.cycle}</div>
    </div>`;
}

function renderAnalysis(d) {
  if (!d.analysis || !d.years || d.years.length === 0) return '';
  const a = d.analysis;
  const m = a.model || {};
  const keyMetricName = m.key_metric === 'npm' ? '净利润率' : m.key_metric === 'at' ? '资产周转率' : m.key_metric === 'em' ? '权益乘数' : 'ROE';
  const modelColors = {brand:'#db2777', turnover:'#0284c7', network:'#7c3aed', leverage:'#ea580c', cyclical:'#ca8a04', lossmaking:'#6b7280'};
  const mColor = modelColors[m.model] || '#4a6cf7';
  const cautionYears = (d.cautions || []).map(c => c.year);
  const cautionList = (d.cautions || []).map(c =>
    `<div style="padding:8px 12px;background:#fff7ed;border-radius:6px;margin-bottom:6px;font-size:13px;color:#9a3412"><strong>🔔 ${c.year}年</strong>（ROE ${c.roe}%）— ${c.reasons.join('；')}</div>`
  ).join('');
  return `
    <div style="margin-top:20px;padding:16px 20px;background:#fff;border:1px solid #edf2f7;border-radius:10px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
        <span style="font-weight:600;font-size:14px;color:#1a1a2e">📖 解读</span>
        ${m.label ? `<span style="background:${mColor}18;color:${mColor};border:1px solid ${mColor}33;border-radius:6px;padding:3px 10px;font-size:12px;font-weight:600">🏗️ ${m.label} → 重点看${keyMetricName}</span>` : ''}
        ${a.tag ? `<span style="background:#eef2ff;color:#4a6cf7;border-radius:6px;padding:3px 10px;font-size:12px;font-weight:600">${a.tag}</span>` : ''}
      </div>
      ${m.desc ? `<div style="font-size:13px;color:#4a5568;line-height:1.8;margin-bottom:8px;padding:8px 12px;background:#f5f7fb;border-radius:6px">${m.desc}</div>` : ''}
      ${a.drivers && a.drivers.length ? `<div style="font-size:13px;color:#4a5568;line-height:1.7;margin-bottom:8px">${a.drivers.join('<br>')}</div>` : ''}
      <div style="font-size:13px;color:#4a5568;line-height:1.8;margin-bottom:8px">${a.summary}</div>
      ${a.advice ? `<div style="font-size:13px;color:#1a1a2e;line-height:1.8;padding:10px 14px;background:#f0f4ff;border-radius:6px;border-left:3px solid ${mColor}"><strong>选股建议：</strong>${a.advice}</div>` : ''}
      ${cautionList ? `<div style="margin-top:12px"><div style="font-weight:600;font-size:13px;margin-bottom:6px;color:#9a3412">⚠️ 警惕年份</div>${cautionList}</div>` : ''}
    </div>`;
}

// ── PEG valuation (manual input with recommendation tiers) ──────
const PEG_RECOMMENDATIONS = {
  brand: {
    tier1: {key:'pe_ttm', label:'PE-TTM（市盈率）', reason:'品牌型企业利润稳定，PE 是衡量市场定价的核心指标。'},
    tier2: {key:'pe_dynamic', label:'动态PE', reason:'当 PE-TTM 不可用（或利润大幅波动），可改用动态 PE。'},
    tier3: {key:'pb', label:'PB（市净率）', reason:'备选。品牌型企业净资产偏轻，PB 意义有限。'},
  },
  turnover: {
    tier1: {key:'ps', label:'PS（市销率）', reason:'周转型企业低毛利高周转，销售额比利润更能反映规模与成长。'},
    tier2: {key:'pe_ttm', label:'PE-TTM（市盈率）', reason:'当利润趋于稳定，PE 也变得有意义。'},
    tier3: {key:'ev_ebitda', label:'EV/EBITDA', reason:'考虑债务与折旧，适合重资产零售/物流企业。'},
  },
  leverage: {
    tier1: {key:'pb', label:'PB（市净率）', reason:'银行/金融企业净资产是经营核心，PB 比 PE 更准确衡量估值。'},
    tier2: {key:'pe_ttm', label:'PE-TTM', reason:'利润波动小时 PE 可作为参考。'},
    tier3: {key:'roe_decomp', label:'ROE 拆解（P/B vs ROE）', reason:'通过 ROE 与 PB 的错配判断金融股定价合理性。'},
  },
  network: {
    tier1: {key:'pe_ttm', label:'PE-TTM', reason:'成熟平台进入利润兑现期，PE 衡量市场对其成长的预期。'},
    tier2: {key:'ps', label:'PS（市销率）', reason:'若利润波动大（仍处于投资期），销售额更代表规模。'},
    tier3: {key:'peg_growth', label:'PEG(增速版)', reason:'对标海外同行，用定性归因。'},
  },
  cyclical: {
    tier1: {key:'pb', label:'PB（市净率）', reason:'周期企业利润波动剧烈，PE 容易产生假象。PB 看资产折价更稳定。'},
    tier2: {key:'ev_ebitda', label:'EV/EBITDA', reason:'去杠杆估值，剔除折旧与债务影响，适合重资产周期行业。'},
    tier3: {key:'pe_ttm', label:'PE-TTM（慎用）', reason:'仅景气高峰或低谷瞬间使用，需注意周期性失真。'},
  },
  lossmaking: {
    tier1: {key:'ps', label:'PS（市销率）', reason:'亏损企业无利润，唯一尚有意义的估值基准是销售额。'},
    tier2: {key:'pb', label:'PB（市净率）', reason:'备选。看净资产的折价空间。'},
    tier3: {key:'none', label:'— 暂无可选', reason:'亏损阶段需观察营收增速/毛利率拐点。'},
  },
};

function loadPeg(code, market) {
  const box = document.getElementById(`pegBox-${code}`);
  if (!box) return;
  const s = stocks.find(s => s.code === code);
  if (!s || !s.data || !s.data.years) {
    box.innerHTML = `<div style="font-size:12px;color:#8899b0">暂无数据</div>`;
    return;
  }
  const years = s.data.years;
  const analysis = s.data.analysis || {};
  const model = (analysis.model && analysis.model.model) || 'brand';
  const rec = PEG_RECOMMENDATIONS[model] || PEG_RECOMMENDATIONS.brand;
  const latest = years[years.length - 1];

  const netProfits = years.map(y => ({year: y.year, np: y.net_profit * 1e8}));
  const cagr3 = calcCagr(netProfits, 3);
  const cagr5 = calcCagr(netProfits, 5);
  const avgRoe = (years.reduce((a,y)=>a+y.roe,0)/years.length).toFixed(1);
  const npVal = latest.net_profit.toFixed(2);

  const tierHtml = (t, label) => `<option value="${t.key}" data-label="${t.label}" data-reason="${t.reason}">${label} ${t.label}</option>`;

  const roeVal = avgRoe;
  box.innerHTML = `
    <div style="margin-top:8px;display:flex;flex-direction:column;gap:12px">
      <div style="padding:16px 20px;background:linear-gradient(135deg,#f9fafb,#f0f4ff);border:1px solid #dbe4ff;border-radius:10px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
          <span style="font-weight:600;font-size:14px;color:#1a1a2e">💹 估值参考（基于利润增速）</span>
          <span style="background:#f0f4ff;color:#4a6cf7;border-radius:6px;padding:3px 10px;font-size:11px;font-weight:500">本地计算</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px">
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">最新净利润</div><div style="font-size:18px;font-weight:700;color:#1a1a2e;margin-top:4px">${npVal}亿</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">利润 CAGR(3Y)</div><div style="font-size:18px;font-weight:700;color:#1a1a2e;margin-top:4px">${cagr3!==null?cagr3.toFixed(1)+'%':'—'}</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">利润 CAGR(5Y)</div><div style="font-size:18px;font-weight:700;color:#1a1a2e;margin-top:4px">${cagr5!==null?cagr5.toFixed(1)+'%':'—'}</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">年均 ROE</div><div style="font-size:18px;font-weight:700;color:#1a1a2e;margin-top:4px">${roeVal}%</div></div>
        </div>
        <div style="font-size:11px;color:#8899b0">PEG 需券商 PE 配合使用。利润 CAGR 显示成长性，ROE 反映质量。成长股 PEG≈1 以下通常更具性价比。</div>
      </div>

      <div style="padding:16px 20px;background:linear-gradient(135deg,#f9fafb,#f0f4ff);border:1px solid #dbe4ff;border-radius:10px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-weight:600;font-size:14px;color:#1a1a2e">💹 估值计算器</span>
          ${analysis.model ? `<span style="background:#eef2ff;color:#4a6cf7;border-radius:6px;padding:3px 10px;font-size:11px;font-weight:500">🏗️ ${analysis.model.label||'通用'}</span>` : ''}
        </div>

        <div id="pegReason-${code}" style="font-size:12px;color:#4a5568;margin-bottom:8px;line-height:1.6;background:#f5f7fb;border-radius:6px;padding:8px 12px">
          <strong>🥇 推荐：</strong>${rec.tier1.reason}
        </div>

        <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
          <select id="pegMetric-${code}" onchange="onPegMetricChange('${code}')" style="flex:1;min-width:140px;padding:8px 12px;border:1px solid #d1d9e6;border-radius:6px;font-size:13px;background:#fff;outline:none">
            ${tierHtml(rec.tier1, '🥇')}
            ${tierHtml(rec.tier2, '🥈')}
            ${tierHtml(rec.tier3, '🥉')}
          </select>
          <input id="pegVal-${code}" type="number" step="0.01" placeholder="${rec.tier1.label}" style="flex:2;min-width:100px;padding:8px 12px;border:1px solid #d1d9e6;border-radius:6px;font-size:13px;outline:none" onkeydown="if(event.key==='Enter')calcPeg('${code}')">
          <button onclick="calcPeg('${code}')" style="background:#4a6cf7;color:#fff;border:none;border-radius:6px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap">计算</button>
        </div>

        <div id="pegResult-${code}"></div>
      </div>
    </div>`;
}

function onPegMetricChange(code) {
  const sel = document.getElementById(`pegMetric-${code}`);
  const input = document.getElementById(`pegVal-${code}`);
  const reasonBox = document.getElementById(`pegReason-${code}`);
  if (sel && input) {
    const opt = sel.options[sel.selectedIndex];
    input.placeholder = opt.dataset.label || '';
    if (reasonBox && opt.dataset.reason) {
      const tierIcon = opt.textContent.trim().startsWith('🥇') ? '🥇 推荐' : opt.textContent.trim().startsWith('🥈') ? '🥈 备选' : '🥉 备选';
      reasonBox.innerHTML = `<strong>${tierIcon}：</strong>${opt.dataset.reason}`;
    }
  }
}

function calcPeg(code) {
  const resultDiv = document.getElementById(`pegResult-${code}`);
  if (!resultDiv) return;
  const s = stocks.find(s => s.code === code);
  if (!s || !s.data) { resultDiv.innerHTML = `<div style="color:#dc2626;font-size:12px">暂无数据</div>`; return; }

  const valStr = document.getElementById(`pegVal-${code}`)?.value?.trim();
  if (!valStr || isNaN(parseFloat(valStr))) {
    resultDiv.innerHTML = `<div style="color:#dc2626;font-size:12px">请输入有效数值</div>`;
    return;
  }
  const val = parseFloat(valStr);
  const sel = document.getElementById(`pegMetric-${code}`);
  const metricKey = sel ? sel.value : 'pe_ttm';

  const years = s.data.years;
  const netProfits = years.map(y => ({year: y.year, np: y.net_profit * 1e8}));
  const cagr3 = calcCagr(netProfits, 3);
  const latest = years[years.length - 1];
  const cagrPct = cagr3 !== null ? cagr3 : 0;

  let pegVal = null;
  let resultLines = [];

  if (['pe_ttm', 'pe_dynamic'].includes(metricKey)) {
    if (cagr3 !== null && cagr3 > 0) pegVal = val / cagr3;
    resultLines.push(`📊 填入 PE: ${val.toFixed(1)}x`);
  } else if (metricKey === 'ps') {
    resultLines.push(`📊 填入 PS: ${val.toFixed(1)}x`);
    resultLines.push(`💡 市销率看规模效率，PEG 用 PE/CAGR。PS 可横向对比同业。`);
  } else if (metricKey === 'pb') {
    resultLines.push(`📊 填入 PB: ${val.toFixed(1)}x`);
    resultLines.push(`💡 市净率看资产折价空间，适合金融/周期企业。`);
  } else if (metricKey === 'ev_ebitda') {
    resultLines.push(`📊 填入 EV/EBITDA: ${val.toFixed(1)}x`);
  } else if (metricKey === 'roe_decomp') {
    resultLines.push(`📊 填入估值基准: ${val.toFixed(1)}`);
    resultLines.push(`💡 ROE vs PB 错配判断金融股吸引力。`);
  } else if (metricKey === 'peg_growth') {
    resultLines.push(`📊 填入估值基准: ${val.toFixed(1)}`);
  }

  if (pegVal !== null) {
    resultLines.push(`PEG = ${val.toFixed(1)} ÷ ${cagrPct.toFixed(1)}% = <strong>${pegVal.toFixed(2)}</strong>`);
    const zone = pegVal < 0.8 ? '🟢 低估区间（PEG < 0.8）' : pegVal <= 1.5 ? '🟡 合理区间（0.8 ≤ PEG ≤ 1.5）' : '🔴 高估区间（PEG > 1.5）';
    const zoneColor = pegVal < 0.8 ? '#10b981' : pegVal <= 1.5 ? '#f59e0b' : '#ef4444';
    resultLines.push(`<span style="color:${zoneColor};font-weight:600">${zone}</span>`);
  }

  resultLines.push(`<span style="font-size:11px;color:#8899b0">净利润 CAGR(3y): ${cagr3!==null?cagr3.toFixed(1)+'%':'—'} · 最新净利润: ${latest.net_profit.toFixed(2)}亿</span>`);

  resultDiv.innerHTML = `<div style="margin-top:12px;padding:12px 16px;background:#fff;border-radius:8px;border:1px solid #edf2f7;font-size:13px;color:#1a1a2e;line-height:1.8">${resultLines.join('<br>')}</div>`;
}

function calcCagr(profits, yearsBack) {
  if (profits.length < yearsBack + 1) return null;
  const ps = profits.slice(-yearsBack - 1);
  const start = ps[0].np;
  const end = ps[ps.length - 1].np;
  if (start <= 0 || end <= 0) return null;
  return (Math.pow(end / start, 1.0 / yearsBack) - 1) * 100;
}

function renderPegButton(code, market) {
  return `<button onclick="loadPeg('${code}','${market}')" style="display:inline-flex;align-items:center;gap:6px;margin-top:10px;background:linear-gradient(135deg,#4a6cf7,#6d8aff);color:#fff;border:none;border-radius:8px;padding:10px 18px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(74,108,247,.25);transition:all .2s" onmouseover="this.style.transform='translateY(-1px)';this.style.boxShadow='0 4px 12px rgba(74,108,247,.35)'" onmouseout="this.style.transform='';this.style.boxShadow='0 2px 8px rgba(74,108,247,.25)'">💹 估值参考 <span style="font-size:10px;font-weight:400;opacity:.85"> 增长与计算</span></button>`;
}

// ── single stock view ──────────────────────────────────────────
function refreshStock(code, market) {
  const idx = stocks.findIndex(s => s.code.toUpperCase() === code.toUpperCase());
  if (idx < 0) return;
  stocks[idx].loading = true;
  stocks[idx].data = null;
  delete stocks[idx].error;
  stocks[idx].errorMsg = '';
  activeIdx = idx;
  renderTags();
  showLoadingMsg(stocks[idx].name);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90000);
  fetch(`/api/refresh?code=${encodeURIComponent(code)}&market=${encodeURIComponent(market)}`, {signal: controller.signal})
    .then(resp => {
      if (!resp.ok) throw new Error('刷新数据失败，请稍后重试');
      return resp.json();
    })
    .then(data => {
      clearTimeout(timeout);
      stocks[idx] = {code, market, name: data.company.name, loading: false, error: false, errorMsg: '', data};
      renderTags();
      renderMain();
    })
    .catch(e => {
      clearTimeout(timeout);
      const msg = e.name === 'AbortError' ? '刷新超时' : e.message;
      stocks[idx].loading = false;
      stocks[idx].error = true;
      stocks[idx].errorMsg = msg;
      renderTags();
      renderMain();
    });
}

function refreshAll() {
  const ready = stocks.filter(s => s.data && !s.loading && !s.error);
  if (ready.length === 0) return;
  // Refresh all stocks in parallel
  activeIdx = -1;
  for (const s of ready) {
    const idx = stocks.findIndex(x => x.code === s.code);
    if (idx >= 0) {
      stocks[idx].loading = true;
      stocks[idx].data = null;
    }
  }
  renderTags();
  showLoadingMsg('全部股票');
  const pending = ready.map(s =>
    fetch(`/api/refresh?code=${encodeURIComponent(s.code)}&market=${encodeURIComponent(s.market)}`)
      .then(resp => { if (!resp.ok) throw new Error('fail'); return resp.json(); })
      .catch(e => null)
  );
  Promise.all(pending).then(results => {
    for (let i = 0; i < ready.length; i++) {
      const idx = stocks.findIndex(x => x.code === ready[i].code);
      if (idx >= 0 && results[i]) {
        stocks[idx] = {code: ready[i].code, market: ready[i].market, name: results[i].company.name, loading: false, error: false, errorMsg: '', data: results[i]};
      } else if (idx >= 0) {
        stocks[idx].loading = false;
        stocks[idx].error = true;
        stocks[idx].errorMsg = '刷新失败';
      }
    }
    renderTags();
    renderMain();
  });
}

function renderSingle(s) {
  const d = s.data;
  const y = d.years;
  const by = d.best_year;
  const wy = d.worst_year;

  let html = `<div class="card"><div class="card-header">📊 ${d.company.name} <span style="font-weight:400;font-size:13px;color:#8899b0">(${d.company.code}) — 杜邦分析</span>`;
  html += `<button onclick="refreshStock('${s.code}','${s.market}')" style="float:right;background:#f0f2f5;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;color:#4a5568;margin-left:6px" title="刷新该股票数据">🔄</button>`;
  if (stocks.filter(s=>s.data).length > 1) html += `<button onclick="activeIdx=-1;renderMain()" style="float:right;background:#f0f2f5;border:1px solid #e2e8f0;border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer;color:#4a5568">← 回到对比</button>`;
  html += `</div>`;

  html += `<div style="clear:both"></div>`;

  // Tab bar
  html += `<div class="tab-bar">`;
  html += `<div class="tab-item active" data-tab="dupont" onclick="switchTab(this)">杜邦分解</div>`;
  if (by && wy) html += `<div class="tab-item" data-tab="years" onclick="switchTab(this)">最优/最差年份</div>`;
  html += `<div class="tab-item" data-tab="chart" onclick="switchTab(this)">趋势图</div>`;
  if (stocks.filter(s=>s.data).length > 1) html += `<div class="tab-item" data-tab="compare" onclick="switchTab(this)">对比</div>`;
  html += `</div>`;

  // Tab content: dupont
  html += `<div class="tab-content active" data-content="dupont"><div class="card-body">`;
  html += renderProfile(d.profile);
  const markings = {best: by ? by.year : null, worst: wy ? wy.year : null};
  const cautionYears = (d.cautions || []).map(c => c.year);
  html += `<div style="display:flex;gap:12px;font-size:12px;color:#4a5568;margin-bottom:8px">
    <span>🏆 最优年份</span><span>⚠️ 最差年份</span><span>🔔 警惕年份</span>
  </div>`;
  html += dupontTable(y, markings, cautionYears);
  html += renderAnalysis(d);
  html += renderPegButton(s.code, s.market);
  html += `<div id="pegBox-${s.code}" style="margin-top:12px"></div>`;
  html += `</div></div>`;

  // Tab content: best/worst years
  if (by && wy) {
    html += `<div class="tab-content" data-content="years"><div class="card-body"><div class="year-compare">`;

    // best
    html += `<div class="year-card best"><h3>🏆 最优年份</h3>`;
    html += `<div class="val text-green">${by.year}年</div>`;
    html += `<div style="font-size:32px;font-weight:700;color:#10b981;margin:6px 0">ROE ${by.roe}%</div>`;
    html += `<div class="items">`;
    html += `<div class="item"><div class="l">净利率</div><div class="v">${by.npm}%</div></div>`;
    html += `<div class="item"><div class="l">资产周转率</div><div class="v">${by.at}</div></div>`;
    html += `<div class="item"><div class="l">权益乘数</div><div class="v">${by.em}</div></div>`;
    html += `</div></div>`;

    // worst
    html += `<div class="year-card worst"><h3>⚠️ 最差年份</h3>`;
    html += `<div class="val text-red">${wy.year}年</div>`;
    html += `<div style="font-size:32px;font-weight:700;color:#ef4444;margin:6px 0">ROE ${wy.roe}%</div>`;
    html += `<div class="items">`;
    html += `<div class="item"><div class="l">净利率</div><div class="v">${wy.npm}%</div></div>`;
    html += `<div class="item"><div class="l">资产周转率</div><div class="v">${wy.at}</div></div>`;
    html += `<div class="item"><div class="l">权益乘数</div><div class="v">${wy.em}</div></div>`;
    html += `</div></div>`;

    html += `</div>`;

    // Gap analysis
    const gapNpm = (by.npm - wy.npm).toFixed(2);
    const gapAt = (by.at - wy.at).toFixed(4);
    const gapEm = (by.em - wy.em).toFixed(4);
    html += `<div style="margin-top:20px;padding:16px;background:#f8fafc;border-radius:8px;font-size:13px;line-height:1.8">`;
    html += `<strong>差距分析：</strong>最优年份(${by.year}) vs 最差年份(${wy.year})<br>`;
    html += `净利率差距 ${gapNpm}pct，周转率差距 ${gapAt}，权益乘数差距 ${gapEm}<br>`;
    // Determine main driver
    const npmEffect = ((by.npm-wy.npm)/100 * wy.at * wy.em * 100).toFixed(2);
    const atEffect = (by.npm/100 * (by.at-wy.at) * wy.em * 100).toFixed(2);
    const emEffect = (by.npm/100 * by.at * (by.em-wy.em) * 100).toFixed(2);
    html += `其中净利率变化贡献 ${npmEffect}pct，周转率贡献 ${atEffect}pct，杠杆贡献 ${emEffect}pct<br>`;
    const maxEff = Math.max(Math.abs(parseFloat(npmEffect)), Math.abs(parseFloat(atEffect)), Math.abs(parseFloat(emEffect)));
    let mainDriver = '';
    if (maxEff === Math.abs(parseFloat(npmEffect))) mainDriver = '净利率是主要驱动因素';
    else if (maxEff === Math.abs(parseFloat(atEffect))) mainDriver = '资产周转率是主要驱动因素';
    else mainDriver = '财务杠杆是主要驱动因素';
    html += `<strong>结论：${mainDriver}</strong>`;
    html += `</div>`;

    html += `</div></div>`;
  }

  // Tab content: chart
  html += `<div class="tab-content" data-content="chart"><div class="card-body">`;
  html += `<div class="chart-grid">`;
  html += `<div class="chart-box"><canvas id="chartRoe"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartNpm"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartAt"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartEm"></canvas></div>`;
  html += `</div></div></div>`;

  // Tab content: compare (only if multi stock)
  const ready = stocks.filter(s => s.data);
  if (ready.length > 1) {
    html += `<div class="tab-content" data-content="compare"><div class="card-body">`;
    html += renderCompareTable(ready);
    html += `<div class="chart-grid">`;
    html += `<div class="chart-box"><canvas id="chartCompRoe"></canvas></div>`;
    html += `<div class="chart-box"><canvas id="chartCompNpm"></canvas></div>`;
    html += `</div></div></div>`;
  }

  // ── peers section ──
  if (d.peers && d.peers.length > 0) {
    const isRef = d.peers.some(p => p.ref);
    html += `<div class="card" style="margin-top:16px"><div class="card-header">🏷️ ${isRef ? '同类参考标的' : '同类公司参考'} <span class="text-muted" style="font-weight:400">${isRef ? '暂无同行业标的，提供同市场知名公司作为参考' : '点击可加载其杜邦分析'}</span></div><div class="card-body">`;
    html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px">`;
    for (const p of d.peers) {
      const already = stocks.some(s => s.code === p.code);
      html += `<div class="peer-card" data-code="${p.code}" data-market="${p.market}" data-name="${p.name}" onclick="clickPeer(this)" style="padding:14px 16px;background:#f8fafc;border-radius:10px;border:1px solid #edf2f7;cursor:pointer;transition:.15s;${already ? 'opacity:.5' : ''}" onmouseover="this.style.borderColor='#4a6cf7';this.style.background='#f0f4ff'" onmouseout="this.style.borderColor='#edf2f7';this.style.background='#f8fafc'">`;
      html += `<div style="font-weight:600;font-size:15px;color:#1a1a2e">${p.name}</div>`;
      html += `<div style="font-size:12px;color:#8899b0;margin-top:2px">${p.code} <span style="display:inline-block;padding:1px 6px;background:#e8edfd;border-radius:4px;font-size:11px;color:#4a6cf7;margin-left:4px">${p.market.toUpperCase()}</span></div>`;
      html += already ? `<div style="font-size:11px;color:#10b981;margin-top:6px">✓ 已添加</div>` : `<div style="font-size:11px;color:#4a6cf7;margin-top:6px">点击分析 →</div>`;
      html += `</div>`;
    }
    html += `</div></div></div>`;
  }

  html += `</div>`;
  mainContent.innerHTML = html;

  // Draw charts
  setTimeout(() => drawCharts(d), 50);
  if (ready.length > 1) setTimeout(() => drawCompareCharts(ready), 100);
}

// ── peer click handler ──
function clickPeer(el) {
  const code = el.dataset.code;
  const market = el.dataset.market;
  const name = el.dataset.name;
  const idx = stocks.findIndex(s => s.code === code);
  if (idx >= 0) { selectStock(idx); return; }
  addStockWithInfo(code, market, name);
}

// ── compare view ──────────────────────────────────────────────
function renderCompare(ready) {
  let html = `<div class="card"><div class="card-header">📊 多股对比`;
  html += `<button onclick="refreshAll()" style="float:right;background:#f0f2f5;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;color:#4a5568;margin-left:6px" title="刷新全部股票数据">🔄 全部刷新</button>`;
  html += `</div><div class="card-body">`;
  html += renderCompareTable(ready);
  html += `<div class="chart-grid">`;
  html += `<div class="chart-box"><canvas id="chartCompRoe"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartCompNpm"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartCompAt"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartCompEm"></canvas></div>`;
  html += `</div></div></div>`;
  mainContent.innerHTML = html;
  setTimeout(() => drawCompareCharts(ready), 50);
}

function renderCompareTable(ready) {
  // Get common years
  const yearSets = ready.map(s => new Set(s.data.years.map(y=>y.year)));
  const common = [...yearSets[0]].filter(y => yearSets.every(ys => ys.has(y))).sort();

  let html = `<div class="table-wrap"><table><tr><th>年份</th>`;
  ready.forEach(s => { html += `<th colspan="4">${s.data.company.name}</th>`; });
  html += `</tr><tr><th></th>`;
  ready.forEach(() => { html += `<th>净利率</th><th>周转率</th><th>杠杆</th><th>ROE</th>`; });
  html += `</tr>`;

  for (const yr of common) {
    html += `<tr><td>${yr}</td>`;
    for (const s of ready) {
      const y = s.data.years.find(y => y.year === yr);
      if (y) {
        html += `<td>${y.npm}%</td><td>${y.at}</td><td>${y.em}</td>`;
        html += `<td><strong class="${y.roe > 15 ? 'text-green' : y.roe < 5 ? 'text-red' : ''}">${y.roe}%</strong></td>`;
      } else {
        html += `<td class="text-muted">—</td><td class="text-muted">—</td><td class="text-muted">—</td><td class="text-muted">—</td>`;
      }
    }
    html += `</tr>`;
  }
  return html + `</table></div>`;
}

// ── charts ─────────────────────────────────────────────────────
function drawCharts(d) {
  const years = d.years.map(y=>y.year);
  const roes = d.years.map(y=>y.roe);
  const npms = d.years.map(y=>y.npm);
  const ats = d.years.map(y=>y.at);
  const ems = d.years.map(y=>y.em);

  makeBar('chartRoe', `ROE 趋势 — ${d.company.name}`, years, roes, '#4a6cf7', '%');
  makeBar('chartNpm', `净利率趋势 — ${d.company.name}`, years, npms, '#10b981', '%');
  makeLine('chartAt', `资产周转率趋势 — ${d.company.name}`, years, ats, '#f59e0b', '次');
  makeLine('chartEm', `权益乘数趋势 — ${d.company.name}`, years, ems, '#8b5cf6', '');
}

function makeBar(id, label, labels, data, color, suffix) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  destroyChart(id);
  chartInstances[id] = new Chart(ctx, {type:'bar', data:{labels, datasets:[{label, data, backgroundColor:color+'33', borderColor:color, borderWidth:2,borderRadius:4}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>v+suffix}}}}});
}

function makeLine(id, label, labels, data, color, suffix) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  destroyChart(id);
  const fill = id === 'chartEm' ? false : id === 'chartAt' ? {target:'origin',above:color+'15'} : false;
  chartInstances[id] = new Chart(ctx, {type:'line', data:{labels, datasets:[{label, data, borderColor:color, backgroundColor:color+'20', fill, tension:.3, pointRadius:4, pointBackgroundColor:color, borderWidth:2}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>v+suffix}}}}});
}

const chartInstances = {};

function destroyChart(id) {
  if (chartInstances[id]) { chartInstances[id].destroy(); delete chartInstances[id]; }
}

function drawCompareCharts(ready) {
  const colors = ['#4a6cf7','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899'];
  const yearSets = ready.map(s => new Set(s.data.years.map(y=>y.year)));
  const common = [...yearSets[0]].filter(y => yearSets.every(ys => ys.has(y))).sort();

  ['chartCompRoe','chartCompNpm','chartCompAt','chartCompEm'].forEach((id, i) => {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    destroyChart(id);
    const datasets = ready.map((s, j) => {
      const vals = common.map(y => { const yr = s.data.years.find(vy => vy.year===y); if (!yr) return null; return i===0 ? yr.roe : i===1 ? yr.npm : i===2 ? yr.at : yr.em; });
      return {label: s.data.company.name, data: vals, borderColor: colors[j%colors.length], backgroundColor: colors[j%colors.length]+'20', fill: false, tension: .3, pointRadius: 3, borderWidth: 2, spanGaps: true};
    });
    const labels = ['ROE','净利率','周转率','权益乘数'];
    const suffixes = ['%','%','次',''];
    chartInstances[id] = new Chart(ctx, {type:'line', data:{labels:common, datasets},
      options:{responsive:true,maintainAspectRatio:false,plugins:{title:{display:true,text:labels[i],font:{size:13}}},scales:{y:{beginAtZero:true,ticks:{callback:v=>v+suffixes[i]}}}}});
  });
}

// ── tab switching ──────────────────────────────────────────────
function switchTab(el) {
  const parent = el.closest('.card') || document;
  parent.querySelectorAll('.tab-item').forEach(t => t.classList.remove('active'));
  parent.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  const target = parent.querySelector(`[data-content="${el.dataset.tab}"]`);
  if (target) target.classList.add('active');

  // Re-render charts in case canvas was hidden
  setTimeout(() => {
    const ready = stocks.filter(s => s.data);
    if (ready.length === 1) drawCharts(ready[0].data);
    if (ready.length > 1) drawCompareCharts(ready);
  }, 50);
}

// ── init ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  stockInput.focus();
  mainContent.innerHTML = EMPTY_HTML;
});
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=True)
