import akshare as ak
import pandas as pd
import numpy as np
import os
import pickle
import time
from typing import Optional

pd.set_option('display.float_format', lambda x: f'{x:,.2f}'.replace(',', ','))

MARKET_US = 'us'
MARKET_HK = 'hk'
MARKET_A = 'a'
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
CACHE_TTL = 86400  # 24h

os.makedirs(CACHE_DIR, exist_ok=True)

# ── cache ───────────────────────────────────────────────────────
def _cache_key(code, market):
    return f"{market}_{code}".replace('.', '_')

def _cache_get(code, market):
    path = os.path.join(CACHE_DIR, _cache_key(code, market))
    if not os.path.exists(path): return None
    if time.time() - os.path.getmtime(path) > CACHE_TTL: return None
    try:
        with open(path, 'rb') as f: return pickle.load(f)
    except: return None

def _cache_set(code, market, data):
    path = os.path.join(CACHE_DIR, _cache_key(code, market))
    try:
        with open(path, 'wb') as f: pickle.dump(data, f)
    except: pass

# ── data model ──────────────────────────────────────────────────
class CompanyInfo:
    def __init__(self, code: str, market: str, name: str = ""):
        self.code = code
        self.market = market
        self.name = name

class DupontYear:
    def __init__(self, year: int, revenue: float, net_profit: float,
                 total_assets: float, equity: float, npm: float, at: float, em: float, roe: float):
        self.year = year
        self.revenue = revenue
        self.net_profit = net_profit
        self.total_assets = total_assets
        self.equity = equity
        self.npm = npm
        self.at = at
        self.em = em
        self.roe = roe

class DupontResult:
    def __init__(self, company: CompanyInfo, years: list[DupontYear]):
        self.company = company
        self.years = years
        self.best_year = None
        self.worst_year = None

# ── data fetch ───────────────────────────────────────────────────
def _getv_us(pt, name, yr):
    try:
        v = pt.loc[pt.index.get_level_values('ITEM_NAME') == name, yr]
        return float(v.iloc[0]) if not v.empty else np.nan
    except:
        return np.nan

def _getv_bs_us(pt, names, yr):
    for name in names:
        v = _getv_us(pt, name, yr)
        if not np.isnan(v): return v
    return np.nan

def fetch_us(code: str) -> Optional[DupontResult]:
    cached = _cache_get(code, MARKET_US)
    if cached: return cached
    try:
        bs = ak.stock_financial_us_report_em(stock=code, symbol='资产负债表', indicator='年报')
        inc = ak.stock_financial_us_report_em(stock=code, symbol='综合损益表', indicator='年报')
        name = bs['SECURITY_NAME_ABBR'].iloc[0] if not bs.empty else code

        def pt(df):
            d = df[['REPORT_DATE', 'STD_ITEM_CODE', 'ITEM_NAME', 'AMOUNT']].copy()
            d['YEAR'] = pd.to_datetime(d['REPORT_DATE']).dt.year
            return d.pivot_table(index=['STD_ITEM_CODE', 'ITEM_NAME'], columns='YEAR', values='AMOUNT', aggfunc='first')

        bsp, icp = pt(bs), pt(inc)
        years = sorted(set(int(c) for c in bsp.columns if str(c).isdigit()) & set(int(c) for c in icp.columns if str(c).isdigit()))
        years = [y for y in years if 2018 <= y <= 2026]

        items = []
        for yr in years:
            rev = _getv_us(icp, '营业收入', yr)
            npv = _getv_bs_us(icp, ['归属于母公司股东净利润', '净利润'], yr)
            ac = _getv_us(bsp, '总资产', yr)
            ap = _getv_us(bsp, '总资产', yr-1)
            ec = _getv_bs_us(bsp, ['归属于母公司股东权益', '股东权益合计'], yr)
            ep = _getv_bs_us(bsp, ['归属于母公司股东权益', '股东权益合计'], yr-1)
            aa = (ac + ap) / 2 if not (np.isnan(ac) or np.isnan(ap)) else ac
            ae = (ec + ep) / 2 if not (np.isnan(ec) or np.isnan(ep)) else ec
            if not any(np.isnan(x) for x in [rev, npv, aa, ae]) and rev > 0 and aa > 0 and ae > 0:
                items.append(DupontYear(yr, rev, npv, aa, ae, npv/rev, rev/aa, aa/ae, npv/rev * rev/aa * aa/ae))

        ci = CompanyInfo(code, MARKET_US, name)
        res = _finalize(DupontResult(ci, items))
        _cache_set(code, MARKET_US, res)
        return res
    except:
        return None

def _hk_pivot(df):
    d = df[df['DATE_TYPE_CODE'] == '001'].copy()
    d['YEAR'] = pd.to_datetime(d['REPORT_DATE']).dt.year
    d = d[['YEAR', 'STD_ITEM_CODE', 'STD_ITEM_NAME', 'AMOUNT']].dropna(subset=['AMOUNT'])
    return d.pivot_table(index=['STD_ITEM_CODE', 'STD_ITEM_NAME'], columns='YEAR', values='AMOUNT', aggfunc='first')

def _getv_hk(pt, names, yr):
    for name in names:
        try:
            v = pt.loc[pt.index.get_level_values('STD_ITEM_NAME') == name, yr]
            if not v.empty: return float(v.iloc[0])
        except: pass
    return np.nan

def fetch_hk(code: str) -> Optional[DupontResult]:
    code_s = code.replace('.HK', '').replace('.hk', '')
    cached = _cache_get(code_s, MARKET_HK)
    if cached: return cached
    try:
        bs = ak.stock_financial_hk_report_em(stock=code_s, symbol='资产负债表', indicator='年度')
        inc = ak.stock_financial_hk_report_em(stock=code_s, symbol='利润表', indicator='年度')
        name = bs['SECURITY_NAME_ABBR'].iloc[0] if not bs.empty else code

        bsp, icp = _hk_pivot(bs), _hk_pivot(inc)
        years = sorted(set(int(c) for c in bsp.columns if str(c).isdigit()) & set(int(c) for c in icp.columns if str(c).isdigit()))
        years = [y for y in years if 2018 <= y <= 2026]

        items = []
        for yr in years:
            rev = _getv_hk(icp, ['营业额', '营业收入', '营运收入'], yr)
            npv = _getv_hk(icp, ['股东应占溢利', '本公司拥有人应占净利润', '净利润'], yr)
            ac = _getv_hk(bsp, ['总资产'], yr)
            ap = _getv_hk(bsp, ['总资产'], yr-1)
            ec = _getv_hk(bsp, ['股东权益', '总权益', '本公司拥有人应占权益'], yr)
            ep = _getv_hk(bsp, ['股东权益', '总权益', '本公司拥有人应占权益'], yr-1)
            aa = (ac + ap) / 2 if not (np.isnan(ac) or np.isnan(ap)) else ac
            ae = (ec + ep) / 2 if not (np.isnan(ec) or np.isnan(ep)) else ec
            if not any(np.isnan(x) for x in [rev, npv, aa, ae]) and rev > 0 and aa > 0 and ae > 0:
                items.append(DupontYear(yr, rev, npv, aa, ae, npv/rev, rev/aa, aa/ae, npv/rev * rev/aa * aa/ae))
        ci = CompanyInfo(code, MARKET_HK, name)
        res = _finalize(DupontResult(ci, items))
        _cache_set(code_s, MARKET_HK, res)
        return res
    except:
        return None

def _getv_a(df_annual, col, yr):
    try:
        d = df_annual[df_annual['YEAR'] == yr]
        return float(d[col].iloc[0]) if not d.empty and col in d.columns else np.nan
    except:
        return np.nan

def _to_symbol(code):
    c = code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
    if code.upper().endswith('.SH'): return f'SH{c}'
    if code.upper().endswith('.SZ'): return f'SZ{c}'
    if code.upper().endswith('.BJ'): return f'BJ{c}'
    return f'SH{c}' if c.isdigit() and c[0] in '56' else f'SZ{c}'

def fetch_a(code: str) -> Optional[DupontResult]:
    code_s = code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
    cached = _cache_get(code_s, MARKET_A)
    if cached: return cached
    try:
        sym = _to_symbol(code)
        bs = ak.stock_balance_sheet_by_report_em(symbol=sym)
        inc = ak.stock_profit_sheet_by_report_em(symbol=sym)
        name = bs['SECURITY_NAME_ABBR'].iloc[0] if not bs.empty else code

        bs_y = bs[bs['REPORT_TYPE'] == '年报'].copy()
        inc_y = inc[inc['REPORT_TYPE'] == '年报'].copy()
        bs_y['YEAR'] = pd.to_datetime(bs_y['REPORT_DATE']).dt.year
        inc_y['YEAR'] = pd.to_datetime(inc_y['REPORT_DATE']).dt.year
        years = sorted(set(bs_y['YEAR'].unique()) & set(inc_y['YEAR'].unique()))
        years = [y for y in years if 2018 <= y <= 2026]

        items = []
        for yr in years:
            rev = _getv_a(inc_y, 'TOTAL_OPERATE_INCOME', yr)
            if np.isnan(rev): rev = _getv_a(inc_y, 'OPERATE_INCOME', yr)
            npv = _getv_a(inc_y, 'PARENT_NETPROFIT', yr)
            if np.isnan(npv): npv = _getv_a(inc_y, 'NETPROFIT', yr)
            ac = _getv_a(bs_y, 'TOTAL_ASSETS', yr)
            ap = _getv_a(bs_y, 'TOTAL_ASSETS', yr-1)
            ec = _getv_a(bs_y, 'TOTAL_PARENT_EQUITY', yr)
            if np.isnan(ec): ec = _getv_a(bs_y, 'TOTAL_EQUITY', yr)
            ep = _getv_a(bs_y, 'TOTAL_PARENT_EQUITY', yr-1)
            if np.isnan(ep): ep = _getv_a(bs_y, 'TOTAL_EQUITY', yr-1)
            aa = (ac + ap) / 2 if not (np.isnan(ac) or np.isnan(ap)) else ac
            ae = (ec + ep) / 2 if not (np.isnan(ec) or np.isnan(ep)) else ec
            if not any(np.isnan(x) for x in [rev, npv, aa, ae]) and rev > 0 and aa > 0 and ae > 0:
                items.append(DupontYear(yr, rev, npv, aa, ae, npv/rev, rev/aa, aa/ae, npv/rev * rev/aa * aa/ae))
        ci = CompanyInfo(code, MARKET_A, name)
        res = _finalize(DupontResult(ci, items))
        _cache_set(code_s, MARKET_A, res)
        return res
    except:
        return None

def _finalize(res: DupontResult) -> DupontResult:
    if not res.years: return res
    res.years.sort(key=lambda y: y.year)
    valid = [y for y in res.years if y.roe > 0 and y.net_profit > 0]
    if valid:
        res.best_year = max(valid, key=lambda y: y.roe)
        res.worst_year = min(valid, key=lambda y: y.roe)
    return res

# ── public API ──────────────────────────────────────────────────
def fetch(code: str, market: str = MARKET_US) -> Optional[DupontResult]:
    market = market.lower()
    if market == MARKET_US: return fetch_us(code)
    if market == MARKET_HK: return fetch_hk(code)
    if market == MARKET_A: return fetch_a(code)
    return None

def detect_market(code: str) -> str:
    c = code.upper()
    if c.endswith('.HK'): return MARKET_HK
    if c.endswith('.SH') or c.endswith('.SZ') or c.endswith('.BJ'): return MARKET_A
    if c.endswith('.O') or c.endswith('.N') or len(c) <= 5 and c.isalpha(): return MARKET_US
    return MARKET_HK if c.isdigit() else MARKET_US

# ── search ──────────────────────────────────────────────────────
COMMON_STOCKS = [
    ('JD', '京东', 'us'), ('BABA', '阿里巴巴', 'us'), ('PDD', '拼多多', 'us'),
    ('AMZN', '亚马逊', 'us'), ('AAPL', '苹果', 'us'), ('GOOGL', '谷歌', 'us'),
    ('GOOG', '谷歌', 'us'), ('MSFT', '微软', 'us'), ('META', 'Meta', 'us'),
    ('TSLA', '特斯拉', 'us'), ('NVDA', '英伟达', 'us'), ('NFLX', '奈飞', 'us'),
    ('NVO', '诺和诺德', 'us'), ('NVS', '诺华', 'us'), ('AZN', '阿斯利康', 'us'),
    ('LLY', '礼来', 'us'), ('UNH', '联合健康', 'us'), ('JPM', '摩根大通', 'us'),
    ('V', 'Visa', 'us'), ('MA', '万事达', 'us'), ('KO', '可口可乐', 'us'),
    ('XOM', '埃克森美孚', 'us'), ('WMT', '沃尔玛', 'us'), ('COST', '好市多', 'us'),
    ('AMD', '超威半导体', 'us'), ('INTC', '英特尔', 'us'), ('AVGO', '博通', 'us'),
    ('RIVN', 'Rivian', 'us'), ('LI', '理想汽车', 'us'), ('XPEV', '小鹏汽车', 'us'),
    ('TGT', '塔吉特', 'us'),
    ('00700.HK', '腾讯控股', 'hk'), ('00700.HK', '腾讯', 'hk'),
    ('03690.HK', '美团', 'hk'), ('03690.HK', '美团点评', 'hk'),
    ('09988.HK', '阿里巴巴', 'hk'), ('09988.HK', '阿里', 'hk'),
    ('09999.HK', '网易', 'hk'), ('09618.HK', '京东', 'hk'),
    ('09888.HK', '百度', 'hk'), ('02015.HK', '理想汽车', 'hk'),
    ('09866.HK', '蔚来', 'hk'), ('09868.HK', '小鹏汽车', 'hk'),
    ('01810.HK', '小米集团', 'hk'), ('01810.HK', '小米', 'hk'),
    ('00388.HK', '港交所', 'hk'), ('01299.HK', '友邦保险', 'hk'),
    ('02318.HK', '中国平安', 'hk'), ('01398.HK', '工商银行', 'hk'),
    ('03988.HK', '中国银行', 'hk'), ('00941.HK', '中国移动', 'hk'),
    ('00883.HK', '中国海洋石油', 'hk'),
    ('600519.SH', '贵州茅台', 'a'), ('600519.SH', '茅台', 'a'),
    ('000858.SZ', '五粮液', 'a'), ('000568.SZ', '泸州老窖', 'a'),
    ('600887.SH', '伊利股份', 'a'), ('600036.SH', '招商银行', 'a'),
    ('601318.SH', '中国平安', 'a'), ('000333.SZ', '美的集团', 'a'),
    ('600900.SH', '长江电力', 'a'), ('600276.SH', '恒瑞医药', 'a'),
    ('002415.SZ', '海康威视', 'a'), ('300750.SZ', '宁德时代', 'a'),
    ('000002.SZ', '万科A', 'a'), ('600030.SH', '中信证券', 'a'),
    ('601398.SH', '工商银行', 'a'), ('601939.SH', '建设银行', 'a'),
    ('600941.SH', '中国移动', 'a'), ('600809.SH', '山西汾酒', 'a'),
    ('002594.SZ', '比亚迪', 'a'), ('601012.SH', '隆基绿能', 'a'),
    ('300274.SZ', '阳光电源', 'a'),
]

def search_stocks(query: str) -> list[dict]:
    results = []
    q = query.lower().strip()
    for code, name, market in COMMON_STOCKS:
        if q == name.lower() or q == code.lower():
            results.append({'code': code, 'name': name, 'market': market})
    if results: return results[:20]
    for code, name, market in COMMON_STOCKS:
        if q in name.lower() or q in code.lower():
            results.append({'code': code, 'name': name, 'market': market})
    return results[:20]

# ── peers ────────────────────────────────────────────────────────
PEER_GROUPS = {
    'JD':        [('BABA', '阿里巴巴', 'us'), ('PDD', '拼多多', 'us'), ('AMZN', '亚马逊', 'us')],
    'BABA':      [('JD', '京东', 'us'), ('PDD', '拼多多', 'us'), ('AMZN', '亚马逊', 'us')],
    'PDD':       [('JD', '京东', 'us'), ('BABA', '阿里巴巴', 'us'), ('AMZN', '亚马逊', 'us')],
    'AMZN':      [('JD', '京东', 'us'), ('BABA', '阿里巴巴', 'us'), ('WMT', '沃尔玛', 'us')],
    'WMT':       [('COST', '好市多', 'us'), ('AMZN', '亚马逊', 'us'), ('TGT', '塔吉特', 'us')],
    'AAPL':      [('MSFT', '微软', 'us'), ('GOOGL', '谷歌', 'us'), ('META', 'Meta', 'us')],
    'MSFT':      [('AAPL', '苹果', 'us'), ('GOOGL', '谷歌', 'us'), ('META', 'Meta', 'us')],
    'GOOGL':     [('AAPL', '苹果', 'us'), ('MSFT', '微软', 'us'), ('META', 'Meta', 'us')],
    'META':      [('AAPL', '苹果', 'us'), ('GOOGL', '谷歌', 'us'), ('MSFT', '微软', 'us')],
    'NVDA':      [('AMD', '超威半导体', 'us'), ('INTC', '英特尔', 'us'), ('AVGO', '博通', 'us')],
    'TSLA':      [('RIVN', 'Rivian', 'us'), ('LI', '理想汽车', 'us'), ('XPEV', '小鹏汽车', 'us')],
    'NVO':       [('LLY', '礼来', 'us'), ('AZN', '阿斯利康', 'us'), ('NVS', '诺华', 'us')],
    'LLY':       [('NVO', '诺和诺德', 'us'), ('AZN', '阿斯利康', 'us'), ('UNH', '联合健康', 'us')],
    '00700.HK':  [('03690.HK', '美团', 'hk'), ('09988.HK', '阿里巴巴', 'hk'), ('09999.HK', '网易', 'hk')],
    '03690.HK':  [('00700.HK', '腾讯控股', 'hk'), ('09988.HK', '阿里巴巴', 'hk'), ('01810.HK', '小米集团', 'hk')],
    '09988.HK':  [('00700.HK', '腾讯控股', 'hk'), ('03690.HK', '美团', 'hk'), ('09618.HK', '京东', 'hk')],
    '01810.HK':  [('00700.HK', '腾讯控股', 'hk'), ('03690.HK', '美团', 'hk'), ('09988.HK', '阿里巴巴', 'hk')],
    '09618.HK':  [('BABA', '阿里巴巴', 'us'), ('JD', '京东', 'us'), ('PDD', '拼多多', 'us')],
    '600519.SH': [('000858.SZ', '五粮液', 'a'), ('000568.SZ', '泸州老窖', 'a'), ('600809.SH', '山西汾酒', 'a')],
    '000858.SZ': [('600519.SH', '贵州茅台', 'a'), ('000568.SZ', '泸州老窖', 'a'), ('600809.SH', '山西汾酒', 'a')],
    '000568.SZ': [('600519.SH', '贵州茅台', 'a'), ('000858.SZ', '五粮液', 'a'), ('600809.SH', '山西汾酒', 'a')],
    '600036.SH': [('601398.SH', '工商银行', 'a'), ('601939.SH', '建设银行', 'a'), ('601318.SH', '中国平安', 'a')],
    '601318.SH': [('600036.SH', '招商银行', 'a'), ('601398.SH', '工商银行', 'a'), ('601939.SH', '建设银行', 'a')],
    '300750.SZ': [('002594.SZ', '比亚迪', 'a'), ('601012.SH', '隆基绿能', 'a'), ('300274.SZ', '阳光电源', 'a')],
}

def get_peers(code: str) -> list[dict]:
    c = code.upper()
    if c in PEER_GROUPS:
        return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in PEER_GROUPS[c]]
    return []

# ── serialization ───────────────────────────────────────────────
def result_to_dict(res: DupontResult) -> dict:
    return {
        'company': {'code': res.company.code, 'market': res.company.market, 'name': res.company.name},
        'years': [{
            'year': y.year, 'revenue': round(y.revenue/1e8, 2), 'net_profit': round(y.net_profit/1e8, 2),
            'total_assets': round(y.total_assets/1e8, 2), 'equity': round(y.equity/1e8, 2),
            'npm': round(y.npm*100, 2), 'at': round(y.at, 4), 'em': round(y.em, 4), 'roe': round(y.roe*100, 2)
        } for y in res.years],
        'best_year': {
            'year': res.best_year.year, 'roe': round(res.best_year.roe*100, 2),
            'npm': round(res.best_year.npm*100, 2), 'at': round(res.best_year.at, 4), 'em': round(res.best_year.em, 4)
        } if res.best_year else None,
        'worst_year': {
            'year': res.worst_year.year, 'roe': round(res.worst_year.roe*100, 2),
            'npm': round(res.worst_year.npm*100, 2), 'at': round(res.worst_year.at, 4), 'em': round(res.worst_year.em, 4)
        } if res.worst_year else None,
        'analysis': _generate_analysis(res),
        'peers': get_peers(res.company.code)
    }

def _generate_analysis(res: DupontResult) -> dict:
    if len(res.years) < 2:
        return {'summary': '数据不足，至少需要2年数据进行分析', 'trend': '', 'drivers': []}
    last = res.years[-1]
    prev = res.years[-2]
    trend = '上升' if last.roe > prev.roe else '下降'
    lines = []
    lines.append(f"近年来ROE呈{trend}趋势，最新{last.year}年为{last.roe*100:.2f}%。")
    npm_t = '盈利能力稳健' if last.npm > 0.03 else '净利率偏低，符合行业特征'
    lines.append(f"净利润率{last.npm*100:.2f}%，{npm_t}。")
    at_t = '高周转模式' if last.at > 1.8 else '周转效率中等'
    lines.append(f"资产周转率{last.at:.4f}次，{at_t}。")
    em_t = '杠杆合理' if last.em < 3 else '杠杆偏高，需关注偿债风险'
    lines.append(f"权益乘数{last.em:.4f}，{em_t}。")
    if res.best_year and res.worst_year:
        lines.append(f"最优年份：{res.best_year.year}年（ROE {res.best_year.roe*100:.2f}%），"
                     f"净利率{res.best_year.npm*100:.2f}%，周转率{res.best_year.at:.4f}，杠杆{res.best_year.em:.4f}。")
        lines.append(f"最差年份：{res.worst_year.year}年（ROE {res.worst_year.roe*100:.2f}%），"
                     f"净利率{res.worst_year.npm*100:.2f}%，周转率{res.worst_year.at:.4f}，杠杆{res.worst_year.em:.4f}。")
    return {'summary': '; '.join(lines), 'trend': trend, 'drivers': []}
