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

app = FastAPI(title="杜邦分析 v6.0")
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

@app.get("/api/valuation")
async def api_valuation(code: str = Query(), market: str = Query(default=None)):
    """获取实时估值指标：PE, PB, PS, PCF, EV/EBITDA（腾讯行情 + 财务数据衍生计算）"""
    import urllib.request
    m = market or detect_market(code)
    clean = code.upper().replace('.HK','').replace('.SH','').replace('.SZ','').replace('.BJ','')
    result = {"pe": None, "pb": None, "ps": None, "pcf": None, "ev": None, "ebitda": None, "ev_ebitda": None, "market_cap": None}
    try:
        prefix = f'hk{clean}' if m == 'hk' else f'us{clean}' if m == 'us' else f'sh{clean}' if clean.startswith('6') else f'sz{clean}'
        url = f'http://qt.gtimg.cn/q={prefix}'
        resp = urllib.request.urlopen(url, timeout=8)
        raw = resp.read().decode('gbk')
        parts = raw.split('=')[-1].strip().strip('"').split('~')
        if len(parts) > 45:
            def f(v):
                try: return float(v)
                except: return None
            pe = f(parts[39]) if len(parts) > 39 else None
            mc = f(parts[44]) if len(parts) > 44 else None
            if m == 'us':
                pb = f(parts[41]) if len(parts) > 41 else None
            elif m == 'hk':
                pb = f(parts[72]) if len(parts) > 72 else None
            else:
                pb = f(parts[46]) if len(parts) > 46 else None
            if mc:
                mc = mc * 1e8
            result = {"pe": pe, "pb": pb, "ps": None, "pcf": None, "ev": None, "ebitda": None, "ev_ebitda": None, "market_cap": mc}
            # Compute derived metrics from cached DuPont financial data
            loop = asyncio.get_running_loop()
            dupont_res = await loop.run_in_executor(executor, fetch, code, m)
            if dupont_res and dupont_res.years:
                y = dupont_res.years[-1]
                rev = y.revenue
                if rev and rev > 0 and mc:
                    result['ps'] = mc / rev
                ocf = y.operating_cash_flow
                if ocf and ocf > 0 and mc:
                    result['pcf'] = mc / ocf
                debt = y.total_debt or 0
                cash = y.cash_equivalents or 0
                op = y.operating_profit or 0
                da = y.depreciation_amortization or 0
                ev = mc + debt - cash
                ebitda_val = op + da
                result['ev'] = ev
                result['ebitda'] = ebitda_val
                if ebitda_val > 0:
                    result['ev_ebitda'] = ev / ebitda_val
    except Exception:
        pass
    return result

# ── frontend ────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>杜邦分析 v6.0</title>
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
  <h1>杜邦分析 <span style="font-size:11px;color:#8899b0;font-weight:400">v6.0</span></h1>
  <p>基于公开财报数据的杜邦分解（支持美股/港股/A股）</p>
</div>

<div class="container">
  <!-- ── input ── -->
<div class="search-card">
    <div class="card-body">
      <div class="search-bar">
        <input id="stockInput" type="text" placeholder="输入股票代码或名称，如 JD / 京东 / 00700 / 600519" onkeydown="if(event.key==='Enter') addStock()">
        <button class="btn btn-primary" onclick="addStock()">添加</button>
        <button class="btn btn-sm btn-outline" onclick="exportFullMd()">📄 导出全文</button>
        <button class="btn btn-sm btn-outline" id="exportCompareBtn" style="display:none" onclick="exportCompare()">📷 导出对比图</button>
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
    return alert(`未找到"${val}"，请尝试股票代码如 03690.HK（美团）、NVO（诺和诺德）`);
  }

  if (stocks.some(s => s.code.toUpperCase() === val.toUpperCase())) {
    const existing = stocks.find(s => s.code.toUpperCase() === val.toUpperCase());
    if (existing) selectStock(stocks.indexOf(existing));
    return;
  }
  addStockWithInfo(val, detectMarket(val), val);
}

function addStockWithInfo(code, market, name) {
  const idx = stocks.length;
  const cardId = `sc-${code}`;
  stocks.push({code, market, name, loading: true, error: false, errorMsg: '', cardId});
  renderTags();
  _showLoadingCard(cardId, name);

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
      stocks[idx] = {...stocks[idx], name: data.company.name, loading: false, error: false, errorMsg: '', data};
      renderTags();
      _appendStockCard(stocks[idx]);
      selectStock(idx);
      _toggleCompareBtn();
    })
    .catch(e => {
      clearTimeout(timeout);
      const msg = e.name === 'AbortError' ? '请求超时，请检查网络或稍后重试' : e.message;
      stocks[idx] = {...stocks[idx], loading: false, error: true, errorMsg: msg};
      renderTags();
      _showErrorCard(cardId, code, name, msg);
    });
}

function removeStock(idx) {
  const card = document.getElementById(stocks[idx]?.cardId);
  if (card) card.remove();
  stocks.splice(idx, 1);
  if (stocks.length === 0) {
    mainContent.innerHTML = EMPTY_HTML;
    activeIdx = -1;
  } else {
    const next = Math.min(idx, stocks.length - 1);
    selectStock(next);
  }
  renderTags();
  _toggleCompareBtn();
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

let activeIdx = -1;

function selectStock(idx) {
  activeIdx = idx;
  renderTags();
  stocks.forEach((s, i) => {
    const el = document.getElementById(s.cardId);
    if (el) el.style.display = i === idx ? '' : 'none';
  });
}

function renderTags() {
  stockTags.innerHTML = stocks.map((s, i) => {
    const isActive = i === activeIdx;
    const bg = s.error ? '#fef2f2' : (isActive ? '#dbe4ff' : '#e8edfd');
    const border = isActive ? '2px solid #4a6cf7' : '2px solid transparent';
    if (s.error && s.errorMsg) {
      return `<span class="stock-tag" onclick="selectStock(${i})" title="${s.errorMsg}" style="background:${bg};color:#dc2626;border:${border};cursor:pointer">⚠️ ${s.name} <span class="del" onclick="event.stopPropagation();removeStock(${i})">×</span></span>`;
    }
    return `<span class="stock-tag" onclick="selectStock(${i})" style="background:${bg};border:${border};cursor:pointer">${s.loading ? '⏳' : ''} ${s.name} <span class="del" onclick="event.stopPropagation();removeStock(${i})">×</span></span>`;
  }).join('');
}

function _appendStockCard(s) {
  const existing = document.getElementById(s.cardId);
  if (existing) existing.remove();
  // ensure wrapper exists
  let wrap = document.getElementById('stockViews');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'stockViews';
    mainContent.innerHTML = '';
    mainContent.appendChild(wrap);
  }
  const div = document.createElement('div');
  div.id = s.cardId;
  div.innerHTML = _buildStockCard(s);
  // hide all existing views, then append
  wrap.querySelectorAll('[id^="sc-"]').forEach(el => el.style.display = 'none');
  wrap.appendChild(div);
  div.style.display = '';
  _syncCompareTabs();
}

function _syncCompareTabs() {
  const ready = stocks.filter(s => s.data);
  if (ready.length < 2) return;
  for (const s of stocks) {
    if (!s.data || !s.cardId) continue;
    const card = document.getElementById(s.cardId);
    if (!card) continue;
    if (card.querySelector(`[data-tab="compare-${s.cardId}"]`)) continue;
    const tabBar = card.querySelector('.tab-bar');
    if (!tabBar) continue;
    const tabItem = document.createElement('div');
    tabItem.className = 'tab-item';
    tabItem.dataset.tab = `compare-${s.cardId}`;
    tabItem.textContent = '对比';
    tabItem.onclick = function() { switchTab2(this, `compare-${s.cardId}`); };
    tabBar.appendChild(tabItem);
    const contents = card.querySelectorAll('.tab-content');
    if (!contents.length) continue;
    const lastContent = contents[contents.length - 1];
    const compareDiv = document.createElement('div');
    compareDiv.className = 'tab-content';
    compareDiv.dataset.content = `compare-${s.cardId}`;
    compareDiv.innerHTML = `<div class="card-body">${renderCompareTable(ready)}<div class="chart-grid"><div class="chart-box"><canvas id="chartCompRoe-${s.cardId}"></canvas></div><div class="chart-box"><canvas id="chartCompNpm-${s.cardId}"></canvas></div></div></div>`;
    lastContent.parentNode.insertBefore(compareDiv, lastContent.nextSibling);
  }
}

function _showLoadingCard(cardId, name) {
  const existing = document.getElementById(cardId);
  if (existing) existing.remove();
  let wrap = document.getElementById('stockViews');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'stockViews';
    mainContent.innerHTML = '';
    mainContent.appendChild(wrap);
  }
  const div = document.createElement('div');
  div.id = cardId;
  div.innerHTML = `<div class="card"><div class="card-body"><div class="loading"><div class="spinner"></div><p style="margin-top:12px;font-size:15px;font-weight:500">正在获取 ${name} 的财务数据…</p><p style="margin-top:6px;font-size:13px;color:#8899b0">首次加载可能需 1-3 分钟（A股较慢），数据会缓存到本地</p></div></div></div>`;
  wrap.querySelectorAll('[id^="sc-"]').forEach(el => el.style.display = 'none');
  wrap.appendChild(div);
}

function _showErrorCard(cardId, code, name, msg) {
  const existing = document.getElementById(cardId);
  if (existing) existing.remove();
  let wrap = document.getElementById('stockViews');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'stockViews';
    mainContent.innerHTML = '';
    mainContent.appendChild(wrap);
  }
  const div = document.createElement('div');
  div.id = cardId;
  div.innerHTML = `<div class="card"><div class="card-body"><div class="error-box">⚠️ [${code}] ${name} — ${msg}</div></div></div>`;
  wrap.querySelectorAll('[id^="sc-"]').forEach(el => el.style.display = 'none');
  wrap.appendChild(div);
}

function _buildStockCard(s) {
  const d = s.data;
  const y = d.years;
  const by = d.best_year;
  const wy = d.worst_year;

  let html = `<div class="card"><div class="card-header">📊 ${d.company.name} <span style="font-weight:400;font-size:13px;color:#8899b0">(${d.company.code}) — 杜邦分析</span>`;
  html += `<button onclick="_refreshStock('${s.code}','${s.market}')" style="float:right;background:#f0f2f5;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;color:#4a5568" title="刷新该股票数据">🔄</button>`;
  html += `</div>`;

  html += `<div style="clear:both"></div>`;

  // Tab bar
  html += `<div class="tab-bar">`;
  html += `<div class="tab-item active" data-tab="dupont-${s.cardId}" onclick="switchTab2(this,'dupont-${s.cardId}')">杜邦分解</div>`;
  if (by && wy) html += `<div class="tab-item" data-tab="years-${s.cardId}" onclick="switchTab2(this,'years-${s.cardId}')">最优/最差年份</div>`;
  html += `<div class="tab-item" data-tab="chart-${s.cardId}" onclick="switchTab2(this,'chart-${s.cardId}')">趋势图</div>`;
  const ready = stocks.filter(ss => ss.data);
  if (ready.length > 1) html += `<div class="tab-item" data-tab="compare-${s.cardId}" onclick="switchTab2(this,'compare-${s.cardId}')">对比</div>`;
  html += `</div>`;

  // Tab content: dupont
  html += `<div class="tab-content active" data-content="dupont-${s.cardId}"><div class="card-body">`;
  html += renderProfile(d.profile);
  const markings = {best: by ? by.year : null, worst: wy ? wy.year : null};
  const cautionYears = (d.cautions || []).map(c => c.year);
  html += `<div style="display:flex;gap:12px;font-size:12px;color:#4a5568;margin-bottom:8px">
    <span>🏆 最优年份</span><span>⚠️ 最差年份</span><span>🔔 警惕年份</span>
  </div>`;
  html += dupontTable(y, markings, cautionYears);
  html += renderAnalysis(d);
  html += `<div style="text-align:right;margin-top:8px"><button onclick="exportDupont('${s.code}','${s.market}')" style="background:none;border:1px solid #d1d9e6;border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer;color:#6b7280" title="导出此区域为图片">📷</button></div>`;
  html += renderPegButton(s.code, s.market);
  html += `<div id="pegBox-${s.code}" style="margin-top:12px"></div>`;
  html += `</div></div>`;

  // Tab content: best/worst years
  if (by && wy) {
    html += `<div class="tab-content" data-content="years-${s.cardId}"><div class="card-body"><div class="year-compare">`;
    html += `<div class="year-card best"><h3>🏆 最优年份</h3><div class="val text-green">${by.year}年</div><div style="font-size:32px;font-weight:700;color:#10b981;margin:6px 0">ROE ${by.roe}%</div><div class="items"><div class="item"><div class="l">净利率</div><div class="v">${by.npm}%</div></div><div class="item"><div class="l">资产周转率</div><div class="v">${by.at}</div></div><div class="item"><div class="l">权益乘数</div><div class="v">${by.em}</div></div></div></div>`;
    html += `<div class="year-card worst"><h3>⚠️ 最差年份</h3><div class="val text-red">${wy.year}年</div><div style="font-size:32px;font-weight:700;color:#ef4444;margin:6px 0">ROE ${wy.roe}%</div><div class="items"><div class="item"><div class="l">净利率</div><div class="v">${wy.npm}%</div></div><div class="item"><div class="l">资产周转率</div><div class="v">${wy.at}</div></div><div class="item"><div class="l">权益乘数</div><div class="v">${wy.em}</div></div></div></div>`;
    html += `</div>`;
    const gapNpm = (by.npm - wy.npm).toFixed(2);
    const gapAt = (by.at - wy.at).toFixed(4);
    const gapEm = (by.em - wy.em).toFixed(4);
    html += `<div style="margin-top:20px;padding:16px;background:#f8fafc;border-radius:8px;font-size:13px;line-height:1.8">`;
    html += `<strong>差距分析：</strong>最优年份(${by.year}) vs 最差年份(${wy.year})<br>`;
    html += `净利率差距 ${gapNpm}pct，周转率差距 ${gapAt}，权益乘数差距 ${gapEm}<br>`;
    const npmEffect = ((by.npm-wy.npm)/100 * wy.at * wy.em * 100).toFixed(2);
    const atEffect = (by.npm/100 * (by.at-wy.at) * wy.em * 100).toFixed(2);
    const emEffect = (by.npm/100 * by.at * (by.em-wy.em) * 100).toFixed(2);
    html += `其中净利率变化贡献 ${npmEffect}pct，周转率贡献 ${atEffect}pct，杠杆贡献 ${emEffect}pct<br>`;
    const maxEff = Math.max(Math.abs(parseFloat(npmEffect)), Math.abs(parseFloat(atEffect)), Math.abs(parseFloat(emEffect)));
    let mainDriver = '';
    if (maxEff === Math.abs(parseFloat(npmEffect))) mainDriver = '净利率是主要驱动因素';
    else if (maxEff === Math.abs(parseFloat(atEffect))) mainDriver = '资产周转率是主要驱动因素';
    else mainDriver = '财务杠杆是主要驱动因素';
    html += `<strong>结论：${mainDriver}</strong></div>`;
    html += `<div style="text-align:right;margin-top:12px"><button onclick="exportYears('${s.code}','${s.market}')" style="background:none;border:1px solid #d1d9e6;border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer;color:#6b7280" title="导出此区域为图片">📷</button></div>`;
    html += `</div></div>`;
  }

  // Tab: chart
  html += `<div class="tab-content" data-content="chart-${s.cardId}"><div class="card-body">`;
  html += `<div class="chart-grid" data-export="${s.cardId}-charts">`;
  html += `<div class="chart-box"><canvas id="chartRoe-${s.cardId}"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartNpm-${s.cardId}"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartAt-${s.cardId}"></canvas></div>`;
  html += `<div class="chart-box"><canvas id="chartEm-${s.cardId}"></canvas></div>`;
  html += `</div>`;
  html += `<div style="text-align:right;margin-top:8px"><button onclick="exportCharts('${s.code}','${s.market}')" style="background:none;border:1px solid #d1d9e6;border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer;color:#6b7280" title="导出此区域为图片">📷</button></div></div></div>`;

  // Tab: compare
  const rdy = stocks.filter(ss => ss.data);
  if (rdy.length > 1) {
    html += `<div class="tab-content" data-content="compare-${s.cardId}"><div class="card-body">`;
    html += renderCompareTable(rdy);
    html += `<div class="chart-grid">`;
    html += `<div class="chart-box"><canvas id="chartCompRoe-${s.cardId}"></canvas></div>`;
    html += `<div class="chart-box"><canvas id="chartCompNpm-${s.cardId}"></canvas></div>`;
    html += `</div></div></div>`;
  }

  // Peers
  if (d.peers && d.peers.length > 0) {
    const isRef = d.peers.some(p => p.ref);
    html += `<div class="card" style="margin:16px"><div class="card-header">🏷️ ${isRef ? '同类参考标的' : '同类公司参考'} <span class="text-muted" style="font-weight:400">${isRef ? '暂无同行业标的，提供同市场知名公司作为参考' : '点击可加载其杜邦分析'}</span></div><div class="card-body">`;
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
  html += `<div style="padding:8px 0"></div>`;

  // Schedule chart drawing after append
  setTimeout(() => {
    _drawStockCharts(s);
    const rdy2 = stocks.filter(ss => ss.data);
    if (rdy2.length > 1) _drawCompareCharts(s);
  }, 80);

  return html;
}

function _refreshStock(code, market) {
  const idx = stocks.findIndex(s => s.code.toUpperCase() === code.toUpperCase());
  if (idx < 0) return;
  const s = stocks[idx];
  s.loading = true;
  s.data = null;
  renderTags();
  const card = document.getElementById(s.cardId);
  if (card) card.innerHTML = `<div class="card-body"><div class="loading"><div class="spinner"></div><p style="margin-top:12px">正在刷新 ${s.name} 的数据…</p></div></div>`;

  fetch(`/api/refresh?code=${encodeURIComponent(code)}&market=${encodeURIComponent(market)}`)
    .then(resp => { if (!resp.ok) throw new Error('刷新失败'); return resp.json(); })
    .then(data => {
      stocks[idx] = {...stocks[idx], name: data.company.name, loading: false, error: false, errorMsg: '', data};
      renderTags();
      _appendStockCard(stocks[idx]);
    })
    .catch(e => {
      stocks[idx] = {...stocks[idx], loading: false, error: true, errorMsg: e.message};
      renderTags();
      if (card) card.innerHTML = `<div class="card-body"><div class="error-box">⚠️ ${e.message}</div></div>`;
    });
}

function clickPeer(el) {
  const code = el.dataset.code;
  const market = el.dataset.market;
  const name = el.dataset.name;
  const idx = stocks.findIndex(s => s.code === code);
  if (idx >= 0) {
    document.getElementById(stocks[idx].cardId)?.scrollIntoView({behavior:'smooth',block:'start'});
    return;
  }
  addStockWithInfo(code, market, name);
}

function _drawStockCharts(s) {
  const d = s.data;
  if (!d || !d.years) return;
  const years = d.years.map(y=>y.year);
  const roes = d.years.map(y=>y.roe);
  const npms = d.years.map(y=>y.npm);
  const ats = d.years.map(y=>y.at);
  const ems = d.years.map(y=>y.em);
  const cid = s.cardId;
  makeBar('chartRoe-'+cid, `ROE 趋势 — ${d.company.name}`, years, roes, '#4a6cf7', '%');
  makeBar('chartNpm-'+cid, `净利率趋势 — ${d.company.name}`, years, npms, '#10b981', '%');
  makeLine('chartAt-'+cid, `资产周转率趋势 — ${d.company.name}`, years, ats, '#f59e0b', '次');
  makeLine('chartEm-'+cid, `权益乘数趋势 — ${d.company.name}`, years, ems, '#8b5cf6', '');
}

function _drawCompareCharts(s) {
  const ready = stocks.filter(ss => ss.data);
  if (ready.length < 2) return;
  const colors = ['#4a6cf7','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899'];
  const yearSets = ready.map(s => new Set(s.data.years.map(y=>y.year)));
  const common = [...yearSets[0]].filter(y => yearSets.every(ys => ys.has(y))).sort();
  const cid = s.cardId;

  ['chartCompRoe','chartCompNpm'].forEach((base, i) => {
    const ctx = document.getElementById(base+'-'+cid);
    if (!ctx) return;
    destroyChart(base+'-'+cid);
    const datasets = ready.map((ss, j) => {
      const vals = common.map(y => { const yr = ss.data.years.find(vy => vy.year===y); if (!yr) return null; return i===0 ? yr.roe : yr.npm; });
      return {label: ss.data.company.name, data: vals, borderColor: colors[j%colors.length], backgroundColor: colors[j%colors.length]+'20', fill: false, tension: .3, pointRadius: 3, borderWidth: 2, spanGaps: true};
    });
    const labels = ['ROE','净利率'];
    chartInstances[base+'-'+cid] = new Chart(ctx, {type:'line', data:{labels:common, datasets},
      options:{responsive:true,maintainAspectRatio:false,plugins:{title:{display:true,text:labels[i],font:{size:13}}},scales:{y:{beginAtZero:true,ticks:{callback:v=>v+(i===0?'%':'%')}}}}});
  });
}

function switchTab2(el, group) {
  const card = el.closest('.card');
  card.querySelectorAll('.tab-item').forEach(t => t.classList.remove('active'));
  card.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  const target = card.querySelector(`[data-content="${el.dataset.tab}"]`);
  if (target) target.classList.add('active');

  setTimeout(() => {
    // Find which stock this card belongs to
    const cid = group.replace(/^(dupont|years|chart|compare)-/,'');
    const s = stocks.find(ss => ss.cardId === cid);
    if (!s || !s.data) return;
    if (el.dataset.tab.startsWith('chart-'+cid)) _drawStockCharts(s);
    const ready = stocks.filter(ss => ss.data);
    if (el.dataset.tab.startsWith('compare-'+cid) && ready.length > 1) _drawCompareCharts(s);
  }, 80);
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

// ── 估值指标推荐体系 ────────────────────────────────────────────
// 五个核心指标：PE / PB / PS / PCF / EV/EBITDA
// 推荐级别：⭐⭐⭐ 最佳  ⭐⭐ 适用  ⭐ 可参考  — 不推荐
const RATINGS = {
  pe_ttm:     { label:'PE-TTM(市盈率)',  desc:'股价 ÷ 每股收益。衡量每1元利润的市价。' },
  pb:         { label:'PB(市净率)',      desc:'股价 ÷ 每股净资产。衡量净资产折溢价。' },
  ps:         { label:'PS(市销率)',      desc:'总市值 ÷ 营业收入。衡量每1元营收的市价。' },
  pcf:        { label:'PCF(市现率)',     desc:'股价 ÷ 每股经营现金流。衡量现金流质量。' },
  ev_ebitda:  { label:'EV/EBITDA(企业价值倍数)', desc:'企业价值 ÷ 息税折旧摊销前利润。去杠杆的估值指标，适合跨公司比较。' },
};
// scoring: ⭐⭐⭐ = 3, ⭐⭐ = 2, ⭐ = 1, — = 0
const PEG_RECOMMENDATIONS = {
  brand: {
    pe_ttm: { stars:3, rating:'⭐⭐⭐ 最佳', reason:'品牌型企业利润稳定、可预测性强，PE 是最直接有效的估值锚。茅台/可口可乐等溢价来自品牌定价权，PE 反映市场为品牌支付的溢价倍数。' },
    pb:     { stars:1, rating:'⭐ 可参考', reason:'品牌企业净资产偏轻（无形资产权重），PB 通常偏高但意义有限。仅在破产清算或极端低估时参考。' },
    ps:     { stars:2, rating:'⭐⭐ 适用',  reason:'销售是品牌变现能力的源头，PS 可衡量品牌溢价是否转化为营收增长。若 PS 极高但利润不跟随，则品牌溢价未兑现。' },
    pcf:    { stars:3, rating:'⭐⭐⭐ 最佳', reason:'品牌企业现金流通常稳定且高于净利润（折旧低、无大量资本开支），PCF 比 PE 更真实反映盈利能力。现金不骗人。' },
    ev_ebitda: { stars:2, rating:'⭐⭐ 适用', reason:'品牌企业折旧少、EBITDA 接近净利润，EV/EBITDA 可消除不同税率/资本结构的影响。适合跨市场跨行业品牌对标比较。' },
  },
  turnover: {
    pe_ttm: { stars:1, rating:'⭐ 可参考', reason:'周转型企业毛利率极低（如超市 1-3%），PE 对微小利润波动极度敏感，估值容易失真。' },
    pb:     { stars:2, rating:'⭐⭐ 适用',  reason:'重资产周转商的净资产（门店/仓储/物流）有一定参照意义，PB 可衡量资产折价空间。' },
    ps:     { stars:3, rating:'⭐⭐⭐ 最佳', reason:'周转型的核心逻辑是"薄利多销"——销售额才是规模能力的体现。PS 比 PE 更稳定，看营收增长看规模效应。' },
    pcf:    { stars:3, rating:'⭐⭐⭐ 最佳', reason:'周转型企业现金流周转快、库存周转产生大量经营现金流。PCF 反映真实的现金创造能力，是利润质量的最佳检验。' },
    ev_ebitda: { stars:2, rating:'⭐⭐ 适用', reason:'周转型企业通常有大量折旧（仓储物流设备），EBITDA 去除了折旧差异，能更干净地看运营效率。EV/EBITDA 是零售业国际通用估值基准。' },
  },
  leverage: {
    pe_ttm: { stars:1, rating:'⭐ 可参考', reason:'金融/银行利润受拨备、利率政策影响大，PE 波动剧烈且容易产生假象（如坏账少时PE低）。' },
    pb:     { stars:3, rating:'⭐⭐⭐ 最佳', reason:'银行及金融机构净资产（贷款、投资资产）就是经营的原材料。PB≈1 是市场信任中性线，破净（PB<1）意味着市场对其资产质量有疑虑。' },
    ps:     { stars:1, rating:'⭐ 可参考', reason:'金融不是传统收入模式，PS 的"销售额"是净利息/手续费，不如直接看 PB。' },
    pcf:    { stars:2, rating:'⭐⭐ 适用',  reason:'金融机构经营现金流受存货、贷款拨备影响，但长期现金流充足说明风险可控。用于检验银行的收入质量真实。' },
    ev_ebitda: { stars:2, rating:'⭐⭐ 适用', reason:'金融企业利息支出是经营成本而非融资成本，EV/EBITDA 在金融业需结合 PB 使用。适合比较不同杠杆率的金融机构。' },
  },
  network: {
    pe_ttm: { stars:3, rating:'⭐⭐⭐ 最佳', reason:'网络平台进入利润兑现期（如美团/京东转盈），高利润说明平台垄断价值。PE 是海外对标基准的核心指标。' },
    pb:     { stars:1, rating:'⭐ 可参考', reason:'平台资产偏轻（知识产权/工作站），PB 严重偏高。但在极端下跌时（如政策恐慌）PB 可作底部参照。' },
    ps:     { stars:2, rating:'⭐⭐ 适用',  reason:'若平台仍在投资期（利润为负或微利），PS 是主要的估值锚。PS 低反映市场认为营收可能见顶。' },
    pcf:    { stars:2, rating:'⭐⭐ 适用',  reason:'平台企业一旦盈利，现金流快速增长。PCF 是利润质量的检测工具——利润高但现金流差，是危险的信号。' },
    ev_ebitda: { stars:1, rating:'⭐ 可参考', reason:'平台企业折旧少、股权激励多，EBITDA 可能高估真实盈利。EV/EBITDA 可作为 PE 的补充，但优先级较靠后。' },
  },
  cyclical: {
    pe_ttm: { stars:0, rating:'— 慎用',   reason:'周期行业的利润随景气波动剧烈。景气高峰时 PE 极低（看起来便宜），景气低谷时 PE 极高（甚至为负），PE 会给出完全相反的信号。' },
    pb:     { stars:3, rating:'⭐⭐⭐ 最佳', reason:'周期企业重资产为主（厂矿、设备、里矿山），PB 相对稳定不随利润大幅波动。PB<1 时是行业底部的典型标志，PB 高表示景气高峰。' },
    ps:     { stars:2, rating:'⭐⭐ 适用',  reason:'周期低谷时利润可能为负，此时 PS 是唯一有意义的收入估值。但周期品售价波动影响 PS，稳定性次于 PB。' },
    pcf:    { stars:1, rating:'⭐ 可参考', reason:'周期企业现金流和利润同向波动，但现金更领先利润。PCF 低谷回升往往先于利润见底，可作景气拐点领先信号。' },
    ev_ebitda: { stars:1, rating:'⭐ 可参考', reason:'周期企业的 EBITDA 相对 PE 更稳定（去除了折旧波动），但仍受价格周期影响。EV/EBITDA 在景气低谷高于同行时可作底部信号参考。' },
  },
  lossmaking: {
    pe_ttm: { stars:0, rating:'— 不适用', reason:'亏损企业无正利润，PE 为负，没有任何估值意义。' },
    pb:     { stars:1, rating:'⭐ 可参考', reason:'亏损企业资产规模还有残值，净资产有限度有限。PB 低于0.5 以下才值得关注。' },
    ps:     { stars:3, rating:'⭐⭐⭐ 最佳', reason:'亏损阶段唯一有正向意义的指标——销售额证明市场需求真实。PS 越低，市场给每1元营收赋，越安全。' },
    pcf:    { stars:2, rating:'⭐⭐ 适用',  reason:'现金流比利润更可靠——亏损可能是折旧/GW减值造成的，但经营现金流为正是好信号。PCF 负，则经营在"烧钱"。' },
    ev_ebitda: { stars:1, rating:'⭐ 可参考', reason:'亏损企业的 EBITDA 可能仍为正（EBITDA 去除了折旧和摊销），可作为"接近利润"的替代指标。但需注意亏损根源——若 EBITDA 也很低或为负，说明经营面恶化。' },
  },
};

function metricStars(stars) {
  if (stars === 3) return '⭐⭐⭐';
  if (stars === 2) return '⭐⭐';
  if (stars === 1) return '⭐';
  return '—';
}

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
  const revenues   = years.map(y => ({year: y.year, np: y.revenue * 1e8}));
  const cagr3  = calcCagr(netProfits, 3);
  const cagr5  = calcCagr(netProfits, 5);
  const revCagr3 = calcCagr(revenues, 3);
  const revCagr5 = calcCagr(revenues, 5);
  const avgRoe = (years.reduce((a,y)=>a+y.roe,0)/years.length).toFixed(1);
  const npVal  = latest.net_profit.toFixed(2);
  const revVal = latest.revenue.toFixed(2);

  // Sort metrics by stars descending
  const metricKeys = ['pe_ttm','pb','ps','pcf','ev_ebitda'];
  const sorted = metricKeys.map(k => ({key:k, ...RATINGS[k], ...rec[k]})).sort((a,b) => b.stars - a.stars);

  const starColors = {3:'#10b981', 2:'#f59e0b', 1:'#8899b0', 0:'#dc2626'};
  const metricCard = (m, idx) => {
    const border = m.stars === 3 ? '2px solid #10b981' : m.stars === 2 ? '2px solid #f59e0b' : '1px solid #e2e8f0';
    const bg = m.stars === 3 ? '#ecfdf5' : m.stars === 2 ? '#fffbeb' : '#f8fafc';
    return `
      <div style="background:${bg};border:${border};border-radius:10px;padding:14px 16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <span style="font-weight:700;font-size:14px;color:#1a1a2e">${m.label}</span>
          <span style="font-size:12px;color:${starColors[m.stars]};font-weight:600">${m.rating}</span>
        </div>
        <div style="font-size:11px;color:#6b7280;line-height:1.6;margin-bottom:8px">${m.desc}</div>
        <div style="font-size:12px;color:#4a5568;line-height:1.7;padding:6px 10px;background:rgba(255,255,255,.7);border-radius:6px;margin-bottom:10px">
          <strong>原理：</strong>${m.reason}
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <button onclick="fetchSingleMetric(event,'${code}','${m.key}','${s.market}')" title="从网络获取实时 ${m.label}" style="background:#10b981;color:#fff;border:none;border-radius:6px;padding:7px 10px;font-size:11px;font-weight:600;cursor:pointer;white-space:nowrap" id="fetchBtn-${code}-${m.key}">获取</button>
          <input id="pegVal-${code}-${m.key}" type="number" step="0.01" placeholder="输入 ${m.label.split('(')[0]}" style="flex:1;padding:7px 10px;border:1px solid #d1d9e6;border-radius:6px;font-size:13px;outline:none" onkeydown="if(event.key==='Enter')calcMetric('${code}','${m.key}')">
          <button onclick="calcMetric('${code}','${m.key}')" style="background:#4a6cf7;color:#fff;border:none;border-radius:6px;padding:7px 14px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap">计算</button>
        </div>
        <div id="pegResult-${code}-${m.key}" style="margin-top:6px"></div>
      </div>`;
  };

  box.innerHTML = `
    <div style="margin-top:8px;display:flex;flex-direction:column;gap:12px">
      <div style="text-align:right;margin-bottom:4px"><button onclick="exportValuation('${code}','${s.market}')" style="background:#4a6cf7;color:#fff;border:none;border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer;font-weight:500">📷 导出估值卡片</button></div>

      <div style="padding:16px 20px;background:linear-gradient(135deg,#f9fafb,#f0f4ff);border:1px solid #dbe4ff;border-radius:10px" data-export="${code}-ref">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
          <span style="font-weight:600;font-size:14px;color:#1a1a2e">💹 估值参考（自动计算）</span>
          <span style="background:#f0f4ff;color:#4a6cf7;border-radius:6px;padding:3px 10px;font-size:11px;font-weight:500">本地计算</span>
          <span style="margin-left:auto"></span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-bottom:10px">
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">最新净利润</div><div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-top:4px">${npVal}亿</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">利润 CAGR(3Y)</div><div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-top:4px">${cagr3!==null?cagr3.toFixed(1)+'%':'—'}</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">利润 CAGR(5Y)</div><div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-top:4px">${cagr5!==null?cagr5.toFixed(1)+'%':'—'}</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">最新营收</div><div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-top:4px">${revVal}亿</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">营收 CAGR(3Y)</div><div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-top:4px">${revCagr3!==null?revCagr3.toFixed(1)+'%':'—'}</div></div>
          <div style="background:#fff;border-radius:8px;padding:10px 14px;text-align:center"><div style="font-size:11px;color:#8899b0">年均 ROE</div><div style="font-size:17px;font-weight:700;color:#1a1a2e;margin-top:4px">${avgRoe}%</div></div>
        </div>
        <div style="font-size:11px;color:#8899b0;line-height:1.7">
          上方数据由财报自动计算。利润 CAGR 反映盈利成长性，营收 CAGR 反映规模扩张速度，ROE 反映股东回报质量。<br>
          从券商软件查到 PE / PB / PS / PCF 后，填入下方对应计算器即可得到估值判断。
        </div>
      </div>

      <div style="padding:16px 20px;background:linear-gradient(135deg,#f9fafb,#f0f4ff);border:1px solid #dbe4ff;border-radius:10px" data-export="${code}-calc">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
          <span style="font-weight:600;font-size:14px;color:#1a1a2e">💹 估值计算器</span>
          ${analysis.model ? `<span style="background:#eef2ff;color:#4a6cf7;border-radius:6px;padding:3px 10px;font-size:11px;font-weight:500">🏗️ ${analysis.model.label||'通用'}</span>` : ''}
          <span style="font-size:11px;color:#8899b0">五大指标按该企业商业模式适配度排序，⭐越多越适合</span>
          <span style="margin-left:auto"></span>
        </div>
        <div style="margin-bottom:10px">
          <button onclick="fetchAllMetrics('${code}','${s.market}')" style="background:#4a6cf7;color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap">💹 全部获取</button>
          <span style="font-size:11px;color:#8899b0;margin-left:8px">一次获取5个估值指标</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px">
          ${sorted.map((m, i) => metricCard(m, i)).join('')}
        </div>
        <div id="pegSummary-${code}" style="margin-top:12px"></div>
      </div>
    </div>`;
}

// ── 各指标计算逻辑 ────────────────────────────────────────────
function calcMetric(code, metricKey) {
  const resultDiv = document.getElementById(`pegResult-${code}-${metricKey}`);
  if (!resultDiv) return;
  const s = stocks.find(s => s.code === code);
  if (!s || !s.data) { resultDiv.innerHTML = `<div style="color:#dc2626;font-size:12px">暂无数据</div>`; return; }

  const valStr = document.getElementById(`pegVal-${code}-${metricKey}`)?.value?.trim();
  if (!valStr || isNaN(parseFloat(valStr))) {
    resultDiv.innerHTML = `<div style="color:#dc2626;font-size:12px">请输入有效数值</div>`;
    return;
  }
  const val = parseFloat(valStr);
  const years = s.data.years;
  const latest = years[years.length - 1];
  const netProfits = years.map(y => ({year: y.year, np: y.net_profit * 1e8}));
  const revenues   = years.map(y => ({year: y.year, np: y.revenue * 1e8}));
  const profitCagr3 = calcCagr(netProfits, 3);
  const revCagr3    = calcCagr(revenues, 3);
  const avgRoe = years.reduce((a,y)=>a+y.roe,0) / years.length;
  const analysis = s.data.analysis || {};
  const model = (analysis.model && analysis.model.model) || 'brand';

  let lines = [];

  if (metricKey === 'pe_ttm') {
    lines.push(`<strong>📊 PE-TTM = ${val.toFixed(1)}x</strong>`);
    lines.push(`<span style="font-size:12px;color:#6b7280">原理：PEG = PE ÷ 利润CAGR(3年)。PEG<0.8 低估，0.8-1.5 合理，>1.5 偏高。衡量每单位成长性的价格。</span>`);
    if (profitCagr3 !== null && profitCagr3 > 0) {
      const peg = val / profitCagr3;
      lines.push(`PEG = ${val.toFixed(1)} ÷ ${profitCagr3.toFixed(1)}% = <strong>${peg.toFixed(2)}</strong>`);
      const zone = peg < 0.8 ? '🟢 低估区间（PEG < 0.8）' : peg <= 1.5 ? '🟡 合理区间（0.8 ≤ PEG ≤ 1.5）' : '🔴 高估区间（PEG > 1.5）';
      const zc = peg < 0.8 ? '#10b981' : peg <= 1.5 ? '#f59e0b' : '#ef4444';
      lines.push(`<span style="color:${zc};font-weight:600">${zone}</span>`);
    } else {
      lines.push(`<span style="color:#8899b0">利润 CAGR ≤ 0 或数据不足，PEG 无法计算。需要正增长才有意义。</span>`);
    }
    lines.push(`<span style="font-size:11px;color:#8899b0">参考：利润CAGR(3y) ${profitCagr3!==null?profitCagr3.toFixed(1)+'%':'—'} · 净利润 ${latest.net_profit.toFixed(2)}亿</span>`);

  } else if (metricKey === 'pb') {
    lines.push(`<strong>📊 PB = ${val.toFixed(2)}x</strong>`);
    lines.push(`<span style="font-size:12px;color:#6b7280">原理：PB 衡量净资产溢价。结合 ROE 判断——高 ROE + 低 PB = 被低估，低 ROE + 高 PB = 偏贵。PB/ROE 比值越低越有吸引力。</span>`);
    const pbRoeRatio = avgRoe > 0 ? (val / (avgRoe / 100)).toFixed(1) : null;
    lines.push(`年均 ROE: ${avgRoe.toFixed(1)}% → PB/ROE = ${pbRoeRatio !== null ? pbRoeRatio + 'x' : '—'}`);
    if (avgRoe > 0) {
      const fairPb = avgRoe / 100 * 10; // 简化：ROE 10% → PB≈1x
      if (val < fairPb * 0.7) {
        lines.push(`<span style="color:#10b981;font-weight:600">🟢 PB 明显低于 ROE 隐含价值，可能被低估</span>`);
      } else if (val < fairPb * 1.3) {
        lines.push(`<span style="color:#f59e0b;font-weight:600">🟡 PB 与 ROE 匹配度合理</span>`);
      } else {
        lines.push(`<span style="color:#ef4444;font-weight:600">🔴 PB 高于 ROE 隐含价值，估值偏高或市场给予成长溢价</span>`);
      }
    }
    if (val < 1) lines.push(`<span style="font-size:11px;color:#10b981">⚠️ PB < 1（破净）：市场认为净资产可能存在减值风险，但也可能是捡漏机会。</span>`);
    lines.push(`<span style="font-size:11px;color:#8899b0">参考：年均 ROE ${avgRoe.toFixed(1)}%</span>`);

  } else if (metricKey === 'ps') {
    lines.push(`<strong>📊 PS = ${val.toFixed(2)}x</strong>`);
    lines.push(`<span style="font-size:12px;color:#6b7280">原理：PSG = PS ÷ 营收CAGR(3年)。类似 PEG 的营收版本。PSG<1 说明市场为每1%营收增长支付的价格较低。适用于利润不稳定但营收稳定增长的公司。</span>`);
    if (revCagr3 !== null && revCagr3 > 0) {
      const psg = val / revCagr3;
      lines.push(`PSG = ${val.toFixed(2)} ÷ ${revCagr3.toFixed(1)}% = <strong>${psg.toFixed(2)}</strong>`);
      const zone = psg < 0.5 ? '🟢 低估区间（PSG < 0.5）' : psg <= 1.0 ? '🟡 合理区间（0.5 ≤ PSG ≤ 1.0）' : '🔴 偏高区间（PSG > 1.0）';
      const zc = psg < 0.5 ? '#10b981' : psg <= 1.0 ? '#f59e0b' : '#ef4444';
      lines.push(`<span style="color:${zc};font-weight:600">${zone}</span>`);
    } else {
      lines.push(`<span style="color:#8899b0">营收 CAGR ≤ 0 或数据不足，PSG 无法计算。</span>`);
    }
    // 净利率辅助
    const npm = latest.npm;
    if (npm > 0) {
      const impliedPe = val / (npm / 100);
      lines.push(`<span style="font-size:11px;color:#4a5568">💡 PS÷净利率 ≈ 隐含PE: ${val.toFixed(2)} ÷ ${npm.toFixed(1)}% ≈ ${impliedPe.toFixed(1)}x（可与实际PE交叉验证）</span>`);
    }
    lines.push(`<span style="font-size:11px;color:#8899b0">参考：营收CAGR(3y) ${revCagr3!==null?revCagr3.toFixed(1)+'%':'—'} · 营收 ${latest.revenue.toFixed(2)}亿</span>`);

  } else if (metricKey === 'pcf') {
    lines.push(`<strong>📊 PCF = ${val.toFixed(2)}x</strong>`);
    lines.push(`<span style="font-size:12px;color:#6b7280">原理：PCF = 股价 ÷ 每股经营现金流。现金流比利润更难造假，PCF 越低说明每单位现金流越便宜。结合 PE 交叉验证：PCF < PE 说明现金流质量高于账面利润（好信号），PCF > PE 说明利润中含大量应收/非现金项（需警惕）。</span>`);
    // PCF 区间判断
    const zone = val > 0 && val < 8 ? '🟢 现金流充裕区间（PCF < 8）' : val >= 8 && val <= 20 ? '🟡 合理区间（8 ≤ PCF ≤ 20）' : val > 20 ? '🔴 现金流偏弱或估值偏高（PCF > 20）' : '⚠️ PCF 为负（经营现金流出）';
    const zc = val > 0 && val < 8 ? '#10b981' : val >= 8 && val <= 20 ? '#f59e0b' : val > 20 ? '#ef4444' : '#dc2626';
    lines.push(`<span style="color:${zc};font-weight:600">${zone}</span>`);
    // 对比 PE 建议
    lines.push(`<span style="font-size:11px;color:#4a5568">💡 查看券商 PE-TTM，若 PCF < PE → 现金流质量优秀（利润含金量高）；若 PCF > PE → 应收账款较多或资本开支大，利润含金量存疑。</span>`);
    // 增长角度
    if (profitCagr3 !== null && profitCagr3 > 0) {
      const pcfg = val / profitCagr3;
      lines.push(`PCF/CAGR = ${val.toFixed(1)} ÷ ${profitCagr3.toFixed(1)}% = <strong>${pcfg.toFixed(2)}</strong> <span style="font-size:11px;color:#8899b0">（类PEG，衡量现金流的成长定价）</span>`);
    }
    lines.push(`<span style="font-size:11px;color:#8899b0">参考：利润CAGR(3y) ${profitCagr3!==null?profitCagr3.toFixed(1)+'%':'—'} · 净利润 ${latest.net_profit.toFixed(2)}亿</span>`);

  } else if (metricKey === 'ev_ebitda') {
    lines.push(`<strong>📊 EV/EBITDA = ${val.toFixed(1)}x</strong>`);
    lines.push(`<span style="font-size:12px;color:#6b7280">原理：EV/EBITDA 去除资本结构和折旧差异，反映企业整体经营价值的倍数。EV/EBITDA < 10x 通常偏低，10-15x 合理，>15x 偏贵（因行业差异较大，需对比同业）。比 PE 更稳定，适合跨市场/跨公司比较。</span>`);
    if (profitCagr3 !== null && profitCagr3 > 0) {
      const evGrowth = val / profitCagr3;
      lines.push(`EV/EBITDA ÷ 利润CAGR = ${val.toFixed(1)} ÷ ${profitCagr3.toFixed(1)}% = <strong>${evGrowth.toFixed(2)}</strong> <span style="font-size:11px;color:#8899b0">（类PEG，衡量企业价值相对成长性的定价）</span>`);
      if (evGrowth < 0.5) lines.push(`<span style="color:#10b981;font-weight:600">🟢 EV/EBITDA 相对成长性较低，估值有吸引力</span>`);
      else if (evGrowth <= 1.0) lines.push(`<span style="color:#f59e0b;font-weight:600">🟡 EV/EBITDA 与成长性基本匹配</span>`);
      else lines.push(`<span style="color:#ef4444;font-weight:600">🔴 EV/EBITDA 相对成长性偏高，需确认成长能否持续</span>`);
    }
    // 对比 PE
    lines.push(`<span style="font-size:11px;color:#4a5568">💡 对比PE：若 EV/EBITDA 显著低于 PE，说明公司折旧/摊销/财务费用高（重资产特征）；若EV/EBITDA > PE，说明非经营收入占比大或现金充裕。</span>`);
    // 行业区间参考
    const secRanges = {'brand':'10-18x', 'turnover':'6-12x', 'leverage':'8-15x', 'network':'12-25x', 'cyclical':'5-10x', 'lossmaking':'8-15x'};
    const modelLabels = {'brand':'品牌型', 'turnover':'周转型', 'leverage':'杠杆型', 'network':'平台型', 'cyclical':'周期型', 'lossmaking':'亏损型'};
    const secRange = secRanges[model] || '8-15x';
    const modelLabel = (analysis.model && analysis.model.label) || modelLabels[model] || model;
    lines.push(`<span style="font-size:11px;color:#8899b0">行业参考：${modelLabel} 通常 EV/EBITDA 在 ${secRange} 区间 · 利润CAGR(3y) ${profitCagr3!==null?profitCagr3.toFixed(1)+'%':'—'}</span>`);
  }

  resultDiv.innerHTML = `<div style="padding:10px 14px;background:#fff;border-radius:8px;border:1px solid #edf2f7;font-size:13px;color:#1a1a2e;line-height:1.8">${lines.join('<br>')}</div>`;
}

function calcCagr(vals, yearsBack) {
  if (vals.length < yearsBack + 1) return null;
  const ps = vals.slice(-yearsBack - 1);
  const start = ps[0].np;
  const end = ps[ps.length - 1].np;
  if (start <= 0 || end <= 0) return null;
  return (Math.pow(end / start, 1.0 / yearsBack) - 1) * 100;
}

const METRIC_API_KEY = {pe_ttm:'pe', pb:'pb', ps:'ps', pcf:'pcf', ev_ebitda:'ev_ebitda'};
const METRIC_LABEL = {pe_ttm:'PE', pb:'PB', ps:'PS', pcf:'PCF', ev_ebitda:'EV/EBITDA'};

let _h2cPromise = null;
function _ensureHtml2canvas() {
  if (_h2cPromise) return _h2cPromise;
  if (window.html2canvas) { _h2cPromise = Promise.resolve(); return _h2cPromise; }
  _h2cPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js';
    s.onload = resolve;
    s.onerror = () => { _h2cPromise = null; reject(new Error('加载渲染库失败')); };
    document.head.appendChild(s);
  });
  return _h2cPromise;
}

function _cardHeader(title, subtitle) {
  return `<div style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:24px 32px"><div style="font-size:20px;font-weight:700">${title}</div>${subtitle ? `<div style="font-size:12px;color:#8899b0;margin-top:4px">${subtitle}</div>` : ''}</div>`;
}
function _cardFooter() {
  return `<div style="padding:16px 32px;text-align:center;font-size:11px;color:#9ca3af;border-top:1px solid #edf2f7">由 杜邦分析 v6.0 生成</div>`;
}
function _downloadCard(wrap, name) {
  _ensureHtml2canvas().then(() => {
    html2canvas(wrap, {scale:2, useCORS:true, backgroundColor:'#fff'}).then(canvas => {
      canvas.toBlob(blob => {
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name + '.png'; a.click();
      });
      document.body.removeChild(wrap);
    });
  });
}

function _s(code) { return stocks.find(s => s.code.toUpperCase() === code.toUpperCase()); }

function _toggleCompareBtn() {
  const btn = document.getElementById('exportCompareBtn');
  if (!btn) return;
  const cnt = stocks.filter(s => s.data).length;
  btn.style.display = cnt >= 2 ? '' : 'none';
}

function _renderCompareChartsOffscreen() {
  const ready = stocks.filter(s => s.data);
  if (ready.length < 2) return null;
  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:fixed;left:-9999px;top:0;z-index:-1';
  document.body.appendChild(wrap);
  const colors = ['#4a6cf7','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899'];
  const yearSets = ready.map(s => new Set(s.data.years.map(y=>y.year)));
  const common = [...yearSets[0]].filter(y => yearSets.every(ys => ys.has(y))).sort();
  const chartKeys = [
    {id:'_tmpRoe', key:'roe', label:'ROE', suffix:'%'},
    {id:'_tmpNpm', key:'npm', label:'净利率', suffix:'%'},
    {id:'_tmpAt', key:'at', label:'周转率', suffix:''},
    {id:'_tmpEm', key:'em', label:'杠杆', suffix:''},
  ];
  const urls = {};
  for (const k of chartKeys) {
    const canvas = document.createElement('canvas');
    canvas.id = k.id; canvas.width = 380; canvas.height = 200;
    wrap.appendChild(canvas);
    const ctx2d = canvas.getContext('2d');
    ctx2d.fillStyle = '#fff'; ctx2d.fillRect(0, 0, 380, 200);
    const datasets = ready.map((ss, j) => ({
      label: ss.data.company.name,
      data: common.map(y => { const yr = ss.data.years.find(vy => vy.year === y); return yr ? yr[k.key] : null; }),
      borderColor: colors[j % colors.length],
      backgroundColor: colors[j % colors.length] + '20',
      fill: false, tension: .3, pointRadius: 3, borderWidth: 2, spanGaps: true,
    }));
    const chart = new Chart(canvas, {
      type: 'line',
      data: { labels: common, datasets },
      options: {
        responsive: false,
        plugins: { title: { display: true, text: k.label + '对比', font: { size: 13, weight: 'bold' } }, legend: { labels: { font: { size: 11 } } } },
        scales: { y: { beginAtZero: true, ticks: { callback: v => v + k.suffix } } },
      },
    });
    urls[k.key] = canvas.toDataURL('image/png');
    chart.destroy();
  }
  document.body.removeChild(wrap);
  return urls;
}

function exportFullMd() {
  try {
  const ready = stocks.filter(s => s.data);
  if (!ready.length) { alert('没有已加载的股票数据'); return; }
  const modelLabels = {brand:'品牌溢价型', turnover:'资产周转型', network:'网络平台型', leverage:'杠杆经营型', cyclical:'强周期型', lossmaking:'亏损型'};
  let md = `# 📊 杜邦分析报告\n\n`;
  md += `**生成时间：** ${new Date().toLocaleString('zh-CN')}  \n`;
  md += `**涵盖股票：** ${ready.map(s => s.data.company.name).join('、')}  \n\n`;
  md += `---\n\n`;

  for (const s of ready) {
    const d = s.data, yrs = d.years, last = yrs[yrs.length - 1];
    const p = d.profile || {}, a = d.analysis || {}, m = a.model || {};
    const cagr3 = calcCagr(yrs.map(y=>({np:y.net_profit*1e8})),3);
    const cagr5 = calcCagr(yrs.map(y=>({np:y.net_profit*1e8})),5);
    const rc3 = calcCagr(yrs.map(y=>({np:y.revenue*1e8})),3);
    const avgRoe = (yrs.reduce((a,y)=>a+y.roe,0)/yrs.length).toFixed(1);

    md += `## ${d.company.name}（${d.company.code}）\n\n`;
    md += `🏷️ **行业：** ${p.industry || '—'} ｜ **市场：** ${s.market.toUpperCase()}  \n\n`;

    if (p.desc) md += `${p.desc}\n\n`;
    if (p.biz) md += `🔹 **主营：** ${p.biz}  \n`;
    if (p.moat) md += `🔹 **护城河：** ${p.moat}  \n`;
    if (p.cycle) md += `🔹 **周期属性：** ${p.cycle}  \n\n`;

    // Key metrics
    md += `### 📈 关键指标\n\n`;
    md += `| 指标 | 数值 |\n|---|---|\n`;
    md += `| 最新净利润 | ${last.net_profit.toFixed(2)}亿 |\n`;
    md += `| 最新营收 | ${last.revenue.toFixed(2)}亿 |\n`;
    md += `| 最新 ROE | ${last.roe}% |\n`;
    md += `| 净利率 | ${last.npm}% |\n`;
    md += `| 资产周转率 | ${last.at} |\n`;
    md += `| 权益乘数 | ${last.em} |\n`;
    md += `| 利润 CAGR(3Y) | ${cagr3 !== null ? cagr3.toFixed(1)+'%' : '—'} |\n`;
    md += `| 利润 CAGR(5Y) | ${cagr5 !== null ? cagr5.toFixed(1)+'%' : '—'} |\n`;
    md += `| 营收 CAGR(3Y) | ${rc3 !== null ? rc3.toFixed(1)+'%' : '—'} |\n`;
    md += `| 年均 ROE | ${avgRoe}% |\n\n`;

    // DuPont history table
    md += `### 📅 历年杜邦分解\n\n`;
    md += `| 年份 | 营收(亿) | 净利润(亿) | 净利率 | 周转率 | 杠杆 | ROE |\n|---|---|---|---|---|---|---|\n`;
    for (const y of yrs) {
      const tag = y.year === (d.best_year?.year) ? ' 🏆' : y.year === (d.worst_year?.year) ? ' ⚠️' : '';
      md += `| ${y.year}${tag} | ${y.revenue.toFixed(2)} | ${y.net_profit.toFixed(2)} | ${y.npm}% | ${y.at} | ${y.em} | ${y.roe}% |\n`;
    }
    md += '\n';

    // Charts
    const cid = s.cardId;
    const chartKeys = [
      {id:`chartRoe-${cid}`, label:'ROE 趋势'},
      {id:`chartNpm-${cid}`, label:'净利率趋势'},
      {id:`chartAt-${cid}`, label:'周转率趋势'},
      {id:`chartEm-${cid}`, label:'杠杆趋势'},
    ];
    const chartUrls = chartKeys.map(c => {
      const el = document.getElementById(c.id);
      return el ? el.toDataURL('image/png') : null;
    });
    if (chartUrls.some(u => u)) {
      md += `### 📈 趋势图\n\n`;
      for (let i = 0; i < chartKeys.length; i++) {
        if (chartUrls[i]) {
          md += `**${chartKeys[i].label}**\n\n`;
          md += `![${chartKeys[i].label}](${chartUrls[i]})\n\n`;
        }
      }
    }

    // Best/worst years
    if (d.best_year && d.worst_year) {
      const by = d.best_year, wy = d.worst_year;
      md += `### 🏆 最优年份 vs ⚠️ 最差年份\n\n`;
      md += `- **最优：** ${by.year}年 ROE ${by.roe}%（净利率 ${by.npm}%，周转率 ${by.at}，杠杆 ${by.em}）\n`;
      md += `- **最差：** ${wy.year}年 ROE ${wy.roe}%（净利率 ${wy.npm}%，周转率 ${wy.at}，杠杆 ${wy.em}）\n\n`;
      const npmEffect = ((by.npm-wy.npm)/100 * wy.at * wy.em * 100).toFixed(2);
      const atEffect = (by.npm/100 * (by.at-wy.at) * wy.em * 100).toFixed(2);
      const emEffect = (by.npm/100 * by.at * (by.em-wy.em) * 100).toFixed(2);
      const maxEff = Math.max(Math.abs(parseFloat(npmEffect)), Math.abs(parseFloat(atEffect)), Math.abs(parseFloat(emEffect)));
      const driver = maxEff === Math.abs(parseFloat(npmEffect)) ? '净利率' : maxEff === Math.abs(parseFloat(atEffect)) ? '资产周转率' : '财务杠杆';
      md += `**差距分析：** 净利率贡献 ${npmEffect}pct，周转率贡献 ${atEffect}pct，杠杆贡献 ${emEffect}pct  \n`;
      md += `**结论：** ${driver}是主要驱动因素\n\n`;
    }

    // Business model
    if (m.model) {
      md += `### 🏗️ 商业模式：${modelLabels[m.model] || m.model}\n\n`;
      if (m.desc) md += `${m.desc}\n\n`;
      if (a.drivers && a.drivers.length) md += a.drivers.map(d => `- ${d}`).join('\n') + '\n\n';
      if (a.summary) md += `${a.summary}\n\n`;
      if (a.advice) md += `💡 **选股建议：** ${a.advice}\n\n`;
    }

    // Cautions
    const cautions = d.cautions || [];
    if (cautions.length) {
      md += `### 🔔 警惕年份\n\n`;
      for (const c of cautions) md += `- **${c.year}年**（ROE ${c.roe}%）— ${c.reasons.join('；')}\n`;
      md += '\n';
    }

    // Valuation inputs & results
    const mkeys = ['pe_ttm','pb','ps','pcf','ev_ebitda'];
    const model = (a.model && a.model.model) || 'brand';
    const rec = PEG_RECOMMENDATIONS[model] || PEG_RECOMMENDATIONS.brand;
    md += `### 💹 估值计算\n\n`;
    md += `| 指标 | 推荐度 | 输入值 | 计算结果 |\n|---|---|---|---|\n`;
    for (const k of mkeys) {
      const r = rec[k] || {};
      const inp = document.getElementById(`pegVal-${s.code}-${k}`);
      const res = document.getElementById(`pegResult-${s.code}-${k}`);
      const v = inp?.value?.trim() || '';
      const rt = res?.innerText?.replace(/\n/g, ' ').trim() || '';
      const star = r.stars === 3 ? '⭐⭐⭐' : r.stars === 2 ? '⭐⭐' : r.stars === 1 ? '⭐' : '—';
      md += `| ${METRIC_LABEL[k]} | ${star} | ${v || '—'} | ${rt || '—'} |\n`;
    }
    md += '\n';

    md += `---\n\n`;
  }

  // ── Multi-stock comparison section ──
  if (ready.length > 1) {
    const yearSets = ready.map(s => new Set(s.data.years.map(y=>y.year)));
    const common = [...yearSets[0]].filter(y => yearSets.every(ys => ys.has(y))).sort();

    md += `## 🔄 多股对比\n\n`;

    // Comparison table
    md += `### 📊 关键指标对比\n\n`;
    md += `<table>\n<tr><th>年份</th>`;
    ready.forEach(s => { md += `<th colspan="4">${s.data.company.name}</th>`; });
    md += `</tr>\n<tr><th></th>`;
    ready.forEach(() => { md += `<th>净利率</th><th>周转率</th><th>杠杆</th><th>ROE</th>`; });
    md += `</tr>\n`;
    for (const yr of common) {
      md += `<tr><td>${yr}</td>`;
      for (const s of ready) {
        const y = s.data.years.find(vy => vy.year === yr);
        if (y) md += `<td>${y.npm}%</td><td>${y.at}</td><td>${y.em}</td><td>${y.roe}%</td>`;
        else md += `<td>—</td><td>—</td><td>—</td><td>—</td>`;
      }
      md += `</tr>\n`;
    }
    md += `</table>\n\n`;

    // Comparison charts (rendered off-screen to avoid blank images)
    const chartUrls = _renderCompareChartsOffscreen();
    if (chartUrls) {
      md += `### 📈 趋势对比图\n\n`;
      const labels = {roe:'ROE', npm:'净利率', at:'周转率', em:'杠杆'};
      for (const [key, label] of Object.entries(labels)) {
        if (chartUrls[key]) {
          md += `**${label}对比**\n\n`;
          md += `![${label}对比](${chartUrls[key]})\n\n`;
        }
      }
    }
  }

  md += `*由 杜邦分析 v6.0 生成*`;

  // Download as .md file
  const blob = new Blob([md], {type:'text/markdown;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const dateStr = new Date().toISOString().slice(0,10);
  a.download = `杜邦分析报告_${dateStr}.md`;
  a.click();
  URL.revokeObjectURL(url);
  const btn = document.querySelector('[onclick="exportFullMd()"]');
  if (btn) { const t = btn.textContent; btn.textContent = '✅ 已导出'; setTimeout(() => btn.textContent = t, 2000); }
  } catch(e) { alert('导出失败: ' + e.message + '\\n' + e.stack); }
}

function exportDupont(code, market) {
  const s = _s(code); if (!s || !s.data) return;
  const d = s.data, p = d.profile || {}, a = d.analysis || {}, m = a.model || {};
  const years = d.years, last = years[years.length - 1];
  const modelLabels = {brand:'品牌溢价型', turnover:'资产周转型', network:'网络平台型', leverage:'杠杆经营型', cyclical:'强周期型', lossmaking:'亏损型'};
  let h = _cardHeader(`📊 杜邦分解 — ${d.company.name}`, `${d.company.code} ｜ ${s.market.toUpperCase()} ｜ ${p.industry || ''}`);
  h += `<div style="padding:24px 32px">`;
  if (p.desc) h += `<div style="font-size:13px;color:#1a1a2e;margin-bottom:12px;line-height:1.7">${p.desc}</div>`;
  if (p.biz) h += `<div style="font-size:12px;color:#4a5568;margin-bottom:6px">🔹 <strong>主营：</strong>${p.biz}</div>`;
  if (p.moat) h += `<div style="font-size:12px;color:#4a5568;margin-bottom:6px">🔹 <strong>护城河：</strong>${p.moat}</div>`;
  if (p.cycle) h += `<div style="font-size:12px;color:#4a5568;margin-bottom:16px">🔹 <strong>周期属性：</strong>${p.cycle}</div>`;
  h += `<table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:16px">`;
  h += `<tr style="background:#f0f4ff"><th style="padding:8px 10px;text-align:left;border-bottom:2px solid #dbe4ff">年份</th><th style="padding:8px 10px;text-align:right;border-bottom:2px solid #dbe4ff">营收(亿)</th><th style="padding:8px 10px;text-align:right;border-bottom:2px solid #dbe4ff">净利(亿)</th><th style="padding:8px 10px;text-align:right;border-bottom:2px solid #dbe4ff">净利率</th><th style="padding:8px 10px;text-align:right;border-bottom:2px solid #dbe4ff">周转率</th><th style="padding:8px 10px;text-align:right;border-bottom:2px solid #dbe4ff">杠杆</th><th style="padding:8px 10px;text-align:right;border-bottom:2px solid #dbe4ff">ROE</th></tr>`;
  for (const y of years) {
    const r = y.year === (d.best_year?.year) ? 'background:#ecfdf5' : y.year === (d.worst_year?.year) ? 'background:#fef2f2' : '';
    h += `<tr style="border-bottom:1px solid #edf2f7;${r}"><td style="padding:6px 10px;font-weight:500">${y.year}</td><td style="padding:6px 10px;text-align:right">${y.revenue.toFixed(2)}</td><td style="padding:6px 10px;text-align:right">${y.net_profit.toFixed(2)}</td><td style="padding:6px 10px;text-align:right">${y.npm}%</td><td style="padding:6px 10px;text-align:right">${y.at}</td><td style="padding:6px 10px;text-align:right">${y.em}</td><td style="padding:6px 10px;text-align:right;font-weight:600">${y.roe}%</td></tr>`;
  }
  h += `</table>`;
  if (m.model) {
    h += `<div style="background:#f0f4ff;border-radius:8px;padding:16px;margin-bottom:12px">`;
    h += `<div style="font-size:13px;font-weight:600;color:#4a6cf7;margin-bottom:6px">🏗️ ${modelLabels[m.model]||m.model}</div>`;
    if (m.desc) h += `<div style="font-size:12px;color:#4a5568;line-height:1.7;margin-bottom:6px">${m.desc}</div>`;
    if (a.summary) h += `<div style="font-size:12px;color:#4a5568;line-height:1.7">${a.summary}</div>`;
    h += `</div>`;
  }
  const cautions = d.cautions || [];
  if (cautions.length) {
    h += `<div style="background:#fff7ed;border-radius:8px;padding:14px;margin-bottom:12px">`;
    h += `<div style="font-size:13px;font-weight:600;color:#9a3412;margin-bottom:8px">🔔 警惕年份</div>`;
    for (const c of cautions) h += `<div style="font-size:12px;color:#9a3412;margin-bottom:4px">▸ ${c.year}年（ROE ${c.roe}%）— ${c.reasons.join('；')}</div>`;
    h += `</div>`;
  }
  h += `</div>${_cardFooter()}`;
  const wrap = document.createElement('div'); wrap.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;background:#fff;font-family:-apple-system,sans-serif;z-index:-1'; wrap.innerHTML = h;
  document.body.appendChild(wrap); _downloadCard(wrap, `${code}_杜邦分解`);
}

function exportYears(code, market) {
  const s = _s(code); if (!s || !s.data) return;
  const d = s.data, by = d.best_year, wy = d.worst_year;
  if (!by || !wy) return;
  let h = _cardHeader(`📊 ${d.company.name} — 最优 vs 最差年份`, `${d.company.code}`);
  h += `<div style="padding:24px 32px">`;
  h += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px">`;
  h += `<div style="background:#ecfdf5;border:2px solid #6ee7b7;border-radius:12px;padding:20px;text-align:center"><div style="font-size:14px;font-weight:600;color:#047857;margin-bottom:4px">🏆 最优年份</div><div style="font-size:36px;font-weight:700;color:#059669">${by.year}年</div><div style="font-size:40px;font-weight:800;color:#10b981;margin:10px 0">ROE ${by.roe}%</div><div style="font-size:12px;color:#6b7280;line-height:1.8">净利率 ${by.npm}% ｜ 周转率 ${by.at} ｜ 杠杆 ${by.em}</div></div>`;
  h += `<div style="background:#fef2f2;border:2px solid #fca5a5;border-radius:12px;padding:20px;text-align:center"><div style="font-size:14px;font-weight:600;color:#b91c1c;margin-bottom:4px">⚠️ 最差年份</div><div style="font-size:36px;font-weight:700;color:#dc2626">${wy.year}年</div><div style="font-size:40px;font-weight:800;color:#ef4444;margin:10px 0">ROE ${wy.roe}%</div><div style="font-size:12px;color:#6b7280;line-height:1.8">净利率 ${wy.npm}% ｜ 周转率 ${wy.at} ｜ 杠杆 ${wy.em}</div></div>`;
  h += `</div>`;
  h += `<div style="background:#f8fafc;border-radius:10px;padding:18px;font-size:13px;line-height:1.8">`;
  const npmEffect = ((by.npm-wy.npm)/100 * wy.at * wy.em * 100).toFixed(2);
  const atEffect = (by.npm/100 * (by.at-wy.at) * wy.em * 100).toFixed(2);
  const emEffect = (by.npm/100 * by.at * (by.em-wy.em) * 100).toFixed(2);
  h += `<strong>差距分析：</strong><br>`;
  h += `净利率差距 ${(by.npm-wy.npm).toFixed(2)}pct（贡献 ${npmEffect}pct）<br>`;
  h += `周转率差距 ${(by.at-wy.at).toFixed(4)}（贡献 ${atEffect}pct）<br>`;
  h += `杠杆差距 ${(by.em-wy.em).toFixed(4)}（贡献 ${emEffect}pct）<br>`;
  const maxEff = Math.max(Math.abs(parseFloat(npmEffect)), Math.abs(parseFloat(atEffect)), Math.abs(parseFloat(emEffect)));
  const driver = maxEff === Math.abs(parseFloat(npmEffect)) ? '净利率' : maxEff === Math.abs(parseFloat(atEffect)) ? '资产周转率' : '财务杠杆';
  h += `<strong>结论：${driver}是主要驱动因素</strong>`;
  h += `</div></div>${_cardFooter()}`;
  const wrap = document.createElement('div'); wrap.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;background:#fff;font-family:-apple-system,sans-serif;z-index:-1'; wrap.innerHTML = h;
  document.body.appendChild(wrap); _downloadCard(wrap, `${code}_最优最差年份`);
}

function exportCharts(code, market) {
  const s = _s(code); if (!s || !s.data) return;
  const d = s.data;
  const cid = s.cardId;
  const chartIds = {chartRoe:null, chartNpm:null, chartAt:null, chartEm:null};
  const labels = {chartRoe:'ROE趋势', chartNpm:'净利率趋势', chartAt:'周转率趋势', chartEm:'杠杆趋势'};
  let allReady = true;
  for (const k of Object.keys(chartIds)) {
    const c = document.getElementById(`${k}-${cid}`);
    if (c) chartIds[k] = c.toDataURL('image/png');
    else allReady = false;
  }
  if (!allReady) { alert('图表尚未渲染完成，请稍后再试'); return; }
  let h = _cardHeader(`📈 ${d.company.name} — 趋势图`, `${d.company.code} ｜ ${s.market.toUpperCase()}`);
  h += `<div style="padding:24px 32px">`;
  h += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">`;
  for (const k of Object.keys(chartIds)) {
    h += `<div style="border:1px solid #edf2f7;border-radius:10px;padding:12px;background:#fafbfc"><div style="font-size:12px;color:#4a5568;margin-bottom:8px;font-weight:500">${labels[k]}</div><img src="${chartIds[k]}" style="width:100%;display:block"></div>`;
  }
  h += `</div></div>${_cardFooter()}`;
  const wrap = document.createElement('div'); wrap.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;background:#fff;font-family:-apple-system,sans-serif;z-index:-1'; wrap.innerHTML = h;
  document.body.appendChild(wrap); _downloadCard(wrap, `${code}_趋势图`);
}

function exportValuation(code, market) {
  const s = _s(code); if (!s || !s.data) return;
  const d = s.data, years = d.years, last = years[years.length - 1];
  const analysis = d.analysis || {}, model = (analysis.model && analysis.model.model) || 'brand';
  const rec = PEG_RECOMMENDATIONS[model] || PEG_RECOMMENDATIONS.brand;
  const metricKeys = ['pe_ttm','pb','ps','pcf','ev_ebitda'];
  const starColors = {3:'#10b981',2:'#f59e0b',1:'#8899b0',0:'#dc2626'};
  const modelLabels = {brand:'品牌溢价型', turnover:'资产周转型', network:'网络平台型', leverage:'杠杆经营型', cyclical:'强周期型', lossmaking:'亏损型'};
  const np = last.net_profit.toFixed(1), rev = last.revenue.toFixed(1);
  const cagr3 = calcCagr(years.map(y=>({np:y.net_profit*1e8})),3);
  const cagr5 = calcCagr(years.map(y=>({np:y.net_profit*1e8})),5);
  const rc3 = calcCagr(years.map(y=>({np:y.revenue*1e8})),3);
  const avgRoe = (years.reduce((a,y)=>a+y.roe,0)/years.length).toFixed(1);
  const mk = v => v !== null ? v.toFixed(1)+'%' : '—';
  let h = _cardHeader(`💹 估值分析 — ${d.company.name}`, `${d.company.code} ｜ ${s.market.toUpperCase()} ｜ ${modelLabels[model]||model}`);
  h += `<div style="padding:20px 32px">`;

  // valuation ref section
  h += `<div style="font-size:14px;font-weight:600;color:#1a1a2e;margin-bottom:12px">📊 自动计算参考</div>`;
  h += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px">`;
  const refItems = [
    {l:'最新净利润', v:np+'亿'}, {l:'利润 CAGR(3Y)', v:mk(cagr3)}, {l:'利润 CAGR(5Y)', v:mk(cagr5)},
    {l:'最新营收', v:rev+'亿'}, {l:'营收 CAGR(3Y)', v:mk(rc3)}, {l:'年均 ROE', v:avgRoe+'%'},
  ];
  for (const x of refItems) {
    h += `<div style="background:#f0f4ff;border-radius:10px;padding:14px;text-align:center"><div style="font-size:11px;color:#8899b0;margin-bottom:4px">${x.l}</div><div style="font-size:20px;font-weight:700;color:#1a1a2e">${x.v}</div></div>`;
  }
  h += `</div>`;

  // valuation calc section
  h += `<div style="font-size:14px;font-weight:600;color:#1a1a2e;margin-bottom:12px">💹 五大指标计算器</div>`;
  h += `<div style="display:grid;grid-template-columns:1fr;gap:12px">`;
  const sorted = metricKeys.map(k => ({key:k, ...RATINGS[k], ...rec[k]})).sort((a,b) => b.stars - a.stars);
  for (const m of sorted) {
    const inp = document.getElementById(`pegVal-${code}-${m.key}`);
    const val = inp?.value;
    const bg = m.stars===3?'#ecfdf5':m.stars===2?'#fffbeb':'#f8fafc';
    const border = m.stars===3?'2px solid #6ee7b7':m.stars===2?'2px solid #fcd34d':'1px solid #e2e8f0';
    h += `<div style="border:${border};border-radius:10px;padding:14px;background:${bg}">`;
    h += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-weight:700;font-size:13px;color:#1a1a2e">${m.label}</span><span style="font-size:11px;color:${starColors[m.stars]};font-weight:600">${m.rating}</span></div>`;
    h += `<div style="font-size:11px;color:#6b7280;margin-bottom:6px">${m.desc}</div>`;
    h += `<div style="font-size:11px;color:#4a5568;padding:6px 10px;background:rgba(255,255,255,.7);border-radius:6px">${m.reason}</div>`;
    if (val) {
      h += `<div style="margin-top:8px;display:inline-block;background:#4a6cf7;color:#fff;border-radius:6px;padding:4px 14px;font-size:13px;font-weight:600">输入值：${val}</div>`;
      const res = document.getElementById(`pegResult-${code}-${m.key}`);
      if (res && res.innerText.trim()) h += `<div style="margin-top:6px;font-size:12px;color:#4a5568;background:#fff;border-radius:6px;padding:8px 12px;border:1px solid #e2e8f0">${res.innerText}</div>`;
    }
    h += `</div>`;
  }
  h += `</div></div>${_cardFooter()}`;
  const wrap = document.createElement('div'); wrap.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;background:#fff;font-family:-apple-system,sans-serif;z-index:-1'; wrap.innerHTML = h;
  document.body.appendChild(wrap); _downloadCard(wrap, `${code}_估值分析`);
}

async function fetchAllMetrics(code, market) {
  try {
    const resp = await fetch(`/api/valuation?code=${encodeURIComponent(code)}&market=${encodeURIComponent(market)}`);
    if (!resp.ok) throw new Error('请求失败');
    const data = await resp.json();
    for (const [key, apiKey] of Object.entries(METRIC_API_KEY)) {
      const val = data[apiKey];
      const inp = document.getElementById(`pegVal-${code}-${key}`);
      if (inp && val !== null && val !== undefined) {
        inp.value = val.toFixed(2);
      }
      const btn = document.getElementById(`fetchBtn-${code}-${key}`);
      if (btn && val !== null && val !== undefined) {
        btn.textContent = '✓ ' + METRIC_LABEL[key];
        btn.style.background = '#059669';
        btn.dataset.fetched = '1';
        btn.style.cursor = 'default';
      } else if (btn) {
        btn.textContent = '⚠️ 无数据';
        btn.style.background = '#f59e0b';
      }
    }
  } catch(e) {
    alert('获取估值失败: ' + e.message);
  }
}

async function fetchSingleMetric(ev, code, metricKey, market) {
  const btn = ev.target;
  if (btn.dataset.fetched) return;
  const apiKey = METRIC_API_KEY[metricKey];
  if (!apiKey) {
    btn.textContent = '⚠️ 无API';
    btn.style.background = '#f59e0b';
    return;
  }
  btn.textContent = '...';
  btn.disabled = true;
  try {
    const resp = await fetch(`/api/valuation?code=${encodeURIComponent(code)}&market=${encodeURIComponent(market)}`);
    if (!resp.ok) throw new Error('err');
    const data = await resp.json();
    const val = data[apiKey];
    const inp = document.getElementById(`pegVal-${code}-${metricKey}`);
    if (inp && val !== null && val !== undefined) {
      inp.value = val.toFixed(2);
      btn.textContent = '✓ ' + METRIC_LABEL[metricKey];
      btn.style.background = '#059669';
      btn.dataset.fetched = '1';
      btn.style.cursor = 'default';
    } else {
      btn.textContent = '⚠️ 无数据';
      btn.style.background = '#f59e0b';
    }
  } catch(e) {
    btn.textContent = '⚠️ 失败';
    btn.style.background = '#ef4444';
  }
  setTimeout(() => {
    if (!btn.dataset.fetched) {
      btn.textContent = '获取';
      btn.disabled = false;
      btn.style.background = '#10b981';
    }
  }, 3000);
}

function renderPegButton(code, market) {
  return `<button onclick="loadPeg('${code}','${market}')" style="display:inline-flex;align-items:center;gap:6px;margin-top:10px;background:linear-gradient(135deg,#4a6cf7,#6d8aff);color:#fff;border:none;border-radius:8px;padding:10px 18px;font-size:13px;font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(74,108,247,.25);transition:all .2s" onmouseover="this.style.transform='translateY(-1px)';this.style.boxShadow='0 4px 12px rgba(74,108,247,.35)'" onmouseout="this.style.transform='';this.style.boxShadow='0 2px 8px rgba(74,108,247,.25)'">💹 估值参考 <span style="font-size:10px;font-weight:400;opacity:.85"> 五大指标</span></button>`;
}




function exportCompare() {
  const ready = stocks.filter(s => s.data);
  if (ready.length < 2) { alert('需要至少 2 只已加载股票'); return; }
  const colors = ['#4a6cf7','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899'];
  const yearSets = ready.map(s => new Set(s.data.years.map(y=>y.year)));
  const common = [...yearSets[0]].filter(y => yearSets.every(ys => ys.has(y))).sort();
  const chartUrls = _renderCompareChartsOffscreen();

  let h = _cardHeader('📊 多股对比分析', `${ready.map(s=>s.data.company.name).join(' vs ')}`);
  h += `<div style="padding:24px 32px">`;

  // Comparison table
  h += `<div style="font-size:14px;font-weight:600;color:#1a1a2e;margin-bottom:12px">📊 关键指标对比</div>`;
  h += `<div style="overflow-x:auto;margin-bottom:24px"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:600px">`;
  h += `<tr style="background:#f0f4ff"><th style="padding:8px 10px;text-align:left;border-bottom:2px solid #dbe4ff">年份</th>`;
  ready.forEach((s,i) => { h += `<th colspan="4" style="padding:8px 10px;text-align:center;border-bottom:2px solid #dbe4ff;color:${colors[i%colors.length]};font-weight:600">${s.data.company.name}</th>`; });
  h += `</tr><tr style="background:#f8faff"><th style="padding:6px 10px;border-bottom:1px solid #e2e8f0"></th>`;
  ready.forEach(() => { h += `<th style="padding:6px 10px;text-align:center;border-bottom:1px solid #e2e8f0;font-size:11px;color:#8899b0">净利率</th><th style="padding:6px 10px;text-align:center;border-bottom:1px solid #e2e8f0;font-size:11px;color:#8899b0">周转率</th><th style="padding:6px 10px;text-align:center;border-bottom:1px solid #e2e8f0;font-size:11px;color:#8899b0">杠杆</th><th style="padding:6px 10px;text-align:center;border-bottom:1px solid #e2e8f0;font-size:11px;color:#8899b0">ROE</th>`; });
  h += `</tr>`;
  for (const yr of common) {
    h += `<tr style="border-bottom:1px solid #edf2f7"><td style="padding:6px 10px;font-weight:500">${yr}</td>`;
    for (const s of ready) {
      const y = s.data.years.find(vy => vy.year === yr);
      if (y) {
        const cls = y.roe > 15 ? 'color:#10b981;font-weight:600' : y.roe < 5 ? 'color:#ef4444;font-weight:600' : '';
        h += `<td style="padding:6px 10px;text-align:center">${y.npm}%</td><td style="padding:6px 10px;text-align:center">${y.at}</td><td style="padding:6px 10px;text-align:center">${y.em}</td><td style="padding:6px 10px;text-align:center;${cls}">${y.roe}%</td>`;
      } else {
        h += `<td style="padding:6px 10px;text-align:center;color:#cbd5e1">—</td><td style="padding:6px 10px;text-align:center;color:#cbd5e1">—</td><td style="padding:6px 10px;text-align:center;color:#cbd5e1">—</td><td style="padding:6px 10px;text-align:center;color:#cbd5e1">—</td>`;
      }
    }
    h += `</tr>`;
  }
  h += `</table></div>`;

  // Comparison charts
  if (chartUrls) {
    h += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">`;
    const chartLabels = {roe:'ROE', npm:'净利率', at:'周转率', em:'杠杆'};
    for (const [key, label] of Object.entries(chartLabels)) {
      if (chartUrls[key]) {
        h += `<div style="border:1px solid #edf2f7;border-radius:10px;padding:10px;background:#fafbfc"><div style="font-size:11px;color:#4a5568;margin-bottom:6px;font-weight:500">${label}对比</div><img src="${chartUrls[key]}" style="width:100%;display:block"></div>`;
      }
    }
    h += `</div>`;
  }

  h += `</div>${_cardFooter()}`;
  const wrap = document.createElement('div'); wrap.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;background:#fff;font-family:-apple-system,sans-serif;z-index:-1'; wrap.innerHTML = h;
  document.body.appendChild(wrap); _downloadCard(wrap, `多股对比`);
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

// ── chart helpers ──────────────────────────────────────────────
const chartInstances = {};

function destroyChart(id) {
  if (chartInstances[id]) { chartInstances[id].destroy(); delete chartInstances[id]; }
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
  const fill = id.includes('Em') ? false : id.includes('At') ? {target:'origin',above:color+'15'} : false;
  chartInstances[id] = new Chart(ctx, {type:'line', data:{labels, datasets:[{label, data, borderColor:color, backgroundColor:color+'20', fill, tension:.3, pointRadius:4, pointBackgroundColor:color, borderWidth:2}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,ticks:{callback:v=>v+suffix}}}}});
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
