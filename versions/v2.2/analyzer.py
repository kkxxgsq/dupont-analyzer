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

# ── company profiles (qualitative) ──────────────────────────────
COMPANY_PROFILES = {
    'JD':           {'industry': '电商零售', 'desc': '中国自营 B2C 电商龙头，自建物流体系覆盖全国，',
                     'biz':     '京东商城、京东物流、京东健康、京东产发、海外业务（Ochama/Joybuy）。',
                     'moat':    '自营供应链 + 物流一体化，规模效应显著；POP 第三方平台与自营双轮驱动。',
                     'cycle':   '弱周期，受消费大盘与电商渗透率影响。'},
    'BABA':         {'industry': '电商零售', 'desc': '中国电商与云计算双轮驱动的平台型公司，',
                     'biz':     '淘天电商（中国零售/国际站/1688）、阿里云、菜鸟、本地生活（饿了么/高德）、海外（速卖通/Lazada）。',
                     'moat':    '电商网络效应 + 公有云规模；组织从集团向「1+6+N」拆分释放各业务价值。',
                     'cycle':   '弱周期，受消费与云计算资本开支影响。'},
    'PDD':          {'industry': '电商零售', 'desc': '社交电商下沉市场龙头，',
                     'biz':     '国内主站（拼多多）、Temu 海外多多跨境。',
                     'moat':    '极致低价供应链 + 厂家直供，海外以 Temu 复制低价打法。',
                     'cycle':   '弱周期，受国内外消费市况影响。'},
    'AMZN':         {'industry': '电商零售', 'desc': '全球电商 + 公有云双龙头，',
                     'biz':     'AWS（云计算）、电商（PDEX/Prime）、广告、订阅、Whole Foods 线下零售。',
                     'moat':    'AWS 规模优势 + 飞轮效应（电商/会员/广告）。',
                     'cycle':   '电商偏弱周期，云计算与 AI 资本开支驱动。'},
    'WMT':          {'industry': '商超零售', 'desc': '全球最大连锁商超，',
                     'biz':     'Walmart 美国门店、Sam\'s Club、山姆/沃尔玛国际、电商、广告业务。',
                     'moat':    '供应链规模 + 自有品牌渗透 + 门店密度。',
                     'cycle':   '弱消费周期，工资上涨压力敏感。'},
    'AAPL':         {'industry': '消费电子', 'desc': '全球消费电子与软件服务龙头，',
                     'biz':     'iPhone、Mac/iPad、可穿戴（Apple Watch/AirPods）、服务（App Store/iCloud）。',
                     'moat':    '硬件+生态闭环，毛利率领先同业。',
                     'cycle':   '产品周期明显，新机发布驱动。'},
    'MSFT':         {'industry': '企业软件', 'desc': '全球 SaaS + 公有云龙头，',
                     'biz':     'Azure 云、Office 365、GitHub、LinkedIn、Xbox、OpenAI 合作。',
                     'moat':    '企业客户基础 + 商业 AI 渗透（Coplilot）。',
                     'cycle':   '弱周期，IT 开支与 AI 趋势相关。'},
    'GOOGL':        {'industry': '互联网平台', 'desc': '全球搜索 + 广告 + 云龙头，',
                     'biz':     'Google 搜索、YouTube、Android、Cloud、Gemini AI、Pixel。',
                     'moat':    '搜索份额 + 数据/广告主规模。',
                     'cycle':   '广告具有明显周期性。'},
    'META':         {'industry': '互联网平台', 'desc': '全球社交 + 广告龙头，',
                     'biz':     'Facebook / Instagram / WhatsApp 广告、Reality Labs（AR/VR）、AI。',
                     'moat':    '全球用户规模 + 广告投放体系。',
                     'cycle':   '广告具有周期性。'},
    'NVDA':         {'industry': '半导体', 'desc': 'AI GPU 与加速计算龙头，',
                     'biz':     '数据中心 GPU（H100/Blackwell）、游戏显卡、汽车 AI、专业可视化、CUDA 软件生态。',
                     'moat':    'CUDA 软件壁垒 + AI 算力领先。',
                     'cycle':   '强周期，受云厂商资本开支与游戏 PC 周期影响。'},
    'TSLA':         {'industry': '新能源车', 'desc': '全球电动车标杆企业，',
                     'biz':     'Model 3/Y/（S/X/Cybertruck）、FSD 自动驾驶、储能、能源生成。',
                     'moat':    '电池成本控制 + 软件/FSD 长期叙事。',
                     'cycle':   '需求周期 + 价格战敏感。'},
    'NVO':          {'industry': '医药', 'desc': '全球糖尿病与减重新药龙头，',
                     'biz':     'GLP-1 类降糖/减重药（Ozempic/Wegovy/Mounjaro）系列、罕见病胰岛素。',
                     'moat':    'GLP-1 通路专利与先发优势。',
                     'cycle':   '医药弱周期，受 IRA 药价谈判压力。'},
    'LLY':          {'industry': '医药', 'desc': '全球糖尿病/减重新药 + 阿兹海默药标的，',
                     'biz':     'Mounjaro/Zepbound（GLP-1）、Donanemab（阿兹海默）、肿瘤/免疫管线。',
                     'moat':    '在 GLP-1 与阿兹海默领域快速跟进并商业化。',
                     'cycle':   '医药弱周期，研发管线敏感。'},
    '00700.HK':     {'industry': '互联网平台', 'desc': '中国最大互联网综合服务商，',
                     'biz':     '游戏（王者荣耀/和平精英）、社交（微信）、广告、金融科技、云、企业服务、视频号。',
                     'moat':    '微信生态 + 游戏长尾 + 广告/支付场景。',
                     'cycle':   '弱周期，受消费/广告/监管影响。'},
    '03690.HK':     {'industry': '本地生活', 'desc': '中国本地生活与外卖龙头，',
                     'biz':     '美团外卖、美团闪购、到店酒旅、美团优选、Keeta 海外外卖。',
                     'moat':    '配送网络规模 + 履约算法。',
                     'cycle':   '弱周期，与消费场景强相关。'},
    '09988.HK':     {'industry': '电商零售', 'desc': '中国电商与云计算综合服务港股上市主体，',
                     'biz':     '淘天电商、阿里云、菜鸟、海外电商。',
                     'moat':    '电商规模 + 阿里云领先。',
                     'cycle':   '弱周期。'},
    '09618.HK':     {'industry': '电商零售', 'desc': '京东集团港股双重主要上市，',
                     'biz':     '与美股京东 (JD) 同一集团；电商、物流、健康、产发。',
                     'moat':    '自营供应链与物流。',
                     'cycle':   '弱周期。'},
    '01810.HK':     {'industry': '消费电子', 'desc': '小米集团港股上市主体，',
                     'biz':     '手机、IoT 与生活消费品、互联网服务、汽车 SU7/Ultra。',
                     'moat':    '性价比 + 互联网用户群。',
                     'cycle':   '手机消费周期 + 新车节奏。'},
    '600519.SH':    {'industry': '白酒', 'desc': '中国高端白酒龙头，',
                     'biz':     '贵州茅台酒、系列酒（茅台王子酒/茅台迎宾酒等）、出口与电商、冰淇淋/酱香拿铁等跨界。',
                     'moat':    '品牌护城河极深 + 强定价权 + 金融属性。',
                     'cycle':   '弱消费周期，但高估值。'},
    '000858.SZ':    {'industry': '白酒', 'desc': '中国白酒第二梯队龙头，',
                     'biz':     '五粮液系列、浓香型白酒生产销售。',
                     'moat':    '品牌 + 渠道力，是高端商务消费标志之一。',
                     'cycle':   '弱消费周期。'},
    '000568.SZ':    {'industry': '白酒', 'desc': '白酒次高端龙头，',
                     'biz':     '国窖 1573、泸州老窖特曲等系列。',
                     'moat':    '品牌 + 中端/次高端结构。',
                     'cycle':   '弱消费周期。'},
    '600036.SH':    {'industry': '银行', 'desc': '中国股份制银行龙头之一，',
                     'biz':     '公司金融、零售金融、同业金融（招行有个体优势）。',
                     'moat':    '零售客户结构（招行）；风控稳健 ROE 质量。',
                     'cycle':   '受息差与不良周期影响。'},
    '601318.SH':    {'industry': '保险', 'desc': '中国综合金融保险龙头之一，',
                     'biz':     '寿险、产险、银行、资管、保险科技。',
                     'moat':    '线下代理人规模 + 综合金融生态。',
                     'cycle':   '利率与资本市场周期敏感。'},
    '300750.SZ':    {'industry': '新能源', 'desc': '全球动力电池龙头，',
                     'biz':     '动力电池、储能电池、电池材料及回收、电池矿资源。',
                     'moat':    '规模制造 + 研发 + 上游一体化。',
                     'cycle':   '受新能源车销量、储能项目周期影响。'},
    '600150.SH':    {'industry': '船舶制造', 'desc': '中国船舶集团控股的造船与海上装备龙头，',
                     'biz':     '集装箱船、油轮 / LNG 散货船、修船、海工装备、船舶配套设备。',
                     'moat':    '整合后中国船舶集团平台地位 + 船型大型化能力。',
                     'cycle':   '强航运周期，与全球船队升级换代相关。'},
    '688981.SH':    {'industry': '半导体', 'desc': '中国大陆规模最大的晶圆代工企业，',
                     'biz':     '先进工艺（FinFET/N+1/N+2）与成熟制程晶圆代工、模拟/射频/嵌入式非易失性存储、12 英寸产线。',
                     'moat':    '国内制程领先 + 国产替代政策红利 + 产能扩张。',
                     'cycle':   '半导体周期强敏感，受下游消费电子 / AI / 车规需求波动。'},
    '600900.SH':    {'industry': '电力', 'desc': '中国最大的水电上市公司，',
                     'biz':     '长江流域梯级水电站（三峡/溪洛渡/向家坝等）发电与售电、配电、海外水电项目。',
                     'moat':    '水资源垄断 + 低成本水电资产 + 稳定分红。',
                     'cycle':   '弱周期，受降雨量 / 电价政策轻微影响。'},
    '002415.SZ':    {'industry': '安防', 'desc': '全球安防与智能物联网解决方案龙头，',
                     'biz':     '视频监控、AI 视觉、热成像、机器视觉、智能家居萤石品牌。',
                     'moat':    '全球市场份额 + AI 算法积累 + 供应链垂直整合。',
                     'cycle':   '弱周期，与政府/企业安防预算相关。'},
    '600104.SH':    {'industry': '汽车', 'desc': '中国最大的汽车集团（上汽集团），',
                     'biz':     '上汽大众、上汽通用、上汽乘用车（荣威/MG）、华域汽车零部件、上汽通用五菱。',
                     'moat':    '合资品牌规模 + 自主品牌转型 + 零部件体系。',
                     'cycle':   '强烈汽车消费周期，新能源转型压力。'},
}

def get_company_profile(code: str) -> dict:
    c = code.upper()
    if c in COMPANY_PROFILES:
        return COMPANY_PROFILES[c]
    code_stripped = c.replace('.HK', '').replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('.O', '').replace('.N', '')
    for k, v in COMPANY_PROFILES.items():
        k_stripped = k.replace('.HK', '').replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('.O', '').replace('.N', '')
        if k_stripped == code_stripped:
            return v
    if code_stripped in COMPANY_PROFILES:
        return COMPANY_PROFILES[code_stripped]
    return None

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
    # more commonly queried A shares
    ('688981.SH', '中芯国际', 'a'), ('688981.SH', '中芯', 'a'),
    ('06160.HK', '百济神州', 'hk'),
    ('09961.HK', '携程', 'hk'), ('02020.HK', '安踏', 'hk'),
    ('09633.HK', '农夫山泉', 'hk'),
    ('06969.HK', '思摩尔', 'hk'),
    ('600104.SH', '上汽集团', 'a'), ('601857.SH', '中国石油', 'a'),
    ('601088.SH', '中国神华', 'a'), ('601288.SH', '农业银行', 'a'),
    ('601668.SH', '中国建筑', 'a'), ('300124.SZ', '汇川技术', 'a'),
    ('300760.SZ', '迈瑞医疗', 'a'), ('002352.SZ', '顺丰控股', 'a'),
    ('000725.SZ', '京东方A', 'a'), ('600585.SH', '海螺水泥', 'a'),
    ('300014.SZ', '亿纬锂能', 'a'), ('002230.SZ', '科大讯飞', 'a'),
    ('300059.SZ', '东方财富', 'a'),
]

_A_STOCK_MAP = None

def _a_stock_map():
    global _A_STOCK_MAP
    if _A_STOCK_MAP is not None:
        return _A_STOCK_MAP
    # try loading from cache
    path = os.path.join(CACHE_DIR, '_a_stock_map.pkl')
    try:
        with open(path, 'rb') as f:
            _A_STOCK_MAP = pickle.load(f)
        if _A_STOCK_MAP and len(_A_STOCK_MAP) > 3000:
            return _A_STOCK_MAP
    except: pass
    # fetch from akshare
    try:
        df = ak.stock_info_a_code_name()
        _A_STOCK_MAP = {}
        for _, row in df.iterrows():
            code = str(row['code'])
            name = str(row['name'])
            suffix = 'SH' if code.startswith(('6', '8')) else 'SZ' if code.startswith(('0', '3')) else f'BJ{code}' if code.startswith('4') else 'SH'
            full = f"{code}.{suffix}" if not code.startswith('4') else f"{code}.BJ"
            _A_STOCK_MAP[name] = full
        with open(path, 'wb') as f:
            pickle.dump(_A_STOCK_MAP, f)
    except:
        _A_STOCK_MAP = {}
    return _A_STOCK_MAP

def search_stocks(query: str) -> list[dict]:
    q = query.lower().strip()
    if not q:
        return []
    seen = set()  
    results = []
    priority = []  # exact match in COMMON_STOCKS (priority up)
    fuzzy = []     # contains match in COMMON_STOCKS
    a_fuzzy = []   # contains match in A-stock map

    for code, name, market in COMMON_STOCKS:
        key = (code, name, market)
        if key in seen:
            continue
        seen.add(key)
        if q == name.lower() or q == code.lower():
            priority.append({'code': code, 'name': name, 'market': market})
        elif q in name.lower() or q in code.lower():
            fuzzy.append({'code': code, 'name': name, 'market': market})

    # also search A stock name map (a-stock list)
    amap = _a_stock_map()
    for name, code in amap.items():
        key = (code, name, 'a')
        if key in seen:
            continue
        seen.add(key)
        if q == name.lower():
            priority.append({'code': code, 'name': name, 'market': 'a'})
        elif q in name.lower():
            a_fuzzy.append({'code': code, 'name': name, 'market': 'a'})

    results = priority + fuzzy + a_fuzzy
    return results[:25]

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
    # semiconductor/chips
    '688981.SH': [('002415.SZ', '海康威视', 'a'), ('002230.SZ', '科大讯飞', 'a'), ('300059.SZ', '东方财富', 'a')],
    # banking
    '601398.SH': [('600036.SH', '招商银行', 'a'), ('601939.SH', '建设银行', 'a'), ('601288.SH', '农业银行', 'a')],
    '601939.SH': [('600036.SH', '招商银行', 'a'), ('601398.SH', '工商银行', 'a'), ('601288.SH', '农业银行', 'a')],
    # retail
    '000333.SZ': [('002352.SZ', '顺丰控股', 'a'), ('000725.SZ', '京东方A', 'a'), ('300760.SZ', '迈瑞医疗', 'a')],
    # building/construction
    '601668.SH': [('600585.SH', '海螺水泥', 'a'), ('601088.SH', '中国神华', 'a'), ('601857.SH', '中国石油', 'a')],
    # pharma
    '600276.SH': [('300760.SZ', '迈瑞医疗', 'a'), ('300750.SZ', '宁德时代', 'a'), ('002415.SZ', '海康威视', 'a')],
    # autonomous/EV
    '002594.SZ': [('601012.SH', '隆基绿能', 'a'), ('300014.SZ', '亿纬锂能', 'a'), ('300274.SZ', '阳光电源', 'a')],
    # PDD peer extension
    'PDD':        [('JD', '京东', 'us'), ('BABA', '阿里巴巴', 'us'), ('AMZN', '亚马逊', 'us')],
    # shipbuilding
    '600150.SH': [('601668.SH', '中国建筑', 'a'), ('600104.SH', '上汽集团', 'a'), ('002594.SZ', '比亚迪', 'a')],
}

def get_peers(code: str) -> list[dict]:
    c = code.upper()
    if c in PEER_GROUPS:
        return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in PEER_GROUPS[c]]
    stripped = c.replace('.HK', '').replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('.O', '').replace('.N', '')
    if stripped in PEER_GROUPS:
        return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in PEER_GROUPS[stripped]]
    for k in PEER_GROUPS:
        k_stripped = k.replace('.HK', '').replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('.O', '').replace('.N', '')
        if k_stripped == stripped:
            return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in PEER_GROUPS[k]]
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
        'cautions': _detect_cautions(res),
        'analysis': _generate_analysis(res),
        'profile': get_company_profile(res.company.code),
        'peers': get_peers(res.company.code)
    }

def _detect_cautions(res: DupontResult) -> list[dict]:
    if not res.best_year or not res.worst_year:
        return []
    cautions = []
    for y in res.years:
        reasons = []
        if y.net_profit < 0 and y.roe > 0:
            reasons.append('净亏损但 ROE 为正（靠杠杆/周转掩盖亏损）')
        if y.year == res.best_year.year or y.year == res.worst_year.year:
            continue
        idx = next((i for i, yy in enumerate(res.years) if yy.year == y.year), -1)
        if 0 < idx < len(res.years):
            prev = res.years[idx-1]
            if prev.roe > 0 and (y.roe - prev.roe) < -5:
                reasons.append(f'ROE 同比大幅下滑至 {y.roe*100:.1f}%（前值 {prev.roe*100:.1f}%）')
            if y.at > 0 and prev.at > 0 and (prev.at - y.at) / prev.at > 0.5:
                reasons.append(f'资产周转率同比骤降 {(1-y.at/prev.at)*100:.0f}%')
            if prev.em > 0 and y.em > 0 and (y.em - prev.em) / prev.em > 0.5:
                reasons.append(f'权益乘数同比骤升 {(y.em/prev.em-1)*100:.0f}%')
        if reasons:
            cautions.append({'year': y.year, 'roe': round(y.roe*100, 2), 'reasons': reasons})
    return cautions

def _generate_analysis(res: DupontResult) -> dict:
    if len(res.years) < 2:
        return {'summary': '数据不足，至少需要2年数据进行分析', 'trend': '', 'advice': '', 'drivers': []}
    last = res.years[-1]
    prev = res.years[-2]
    trend = '上升' if last.roe > prev.roe else '下降'
    roe_change = (last.roe - prev.roe) * 100
    summary_lines = []
    summary_lines.append(f"近年 ROE 呈{trend}趋势，最新{last.year}年 {last.roe*100:.2f}%（{'+' if roe_change>=0 else ''}{roe_change:.2f}pct）。")

    driver_lines = []
    if abs(last.npm - prev.npm) > abs(last.at - prev.at) * 100 and abs(last.npm - prev.npm) * 100 > abs(last.at - prev.at):
        driver_lines.append(f"🔑 主要驱动：净利率 {prev.npm*100:.2f}% → {last.npm*100:.2f}%")
    elif abs(last.at - prev.at) > 0.1:
        driver_lines.append(f"🔑 主要驱动：周转率 {prev.at:.3f} → {last.at:.3f}")
    if abs(last.em - prev.em) > 0.3:
        driver_lines.append(f"🔑 重要变动：权益乘数 {prev.em:.3f} → {last.em:.3f}")

    quality_lines = []
    if last.roe > 0.15 and last.npm > 0.10:
        quality_lines.append('高 ROE + 高净利润，是优秀的轻资产/品牌型生意。')
    elif last.roe > 0.15 and last.at > 1.5:
        quality_lines.append('高 ROE 主要来自高周转，符合零售/平台模型。')
    elif last.roe > 0.15 and last.em > 4:
        quality_lines.append('高 ROE 主要来自高杠杆，需关注财务风险。')
    elif last.npm < 0.02 and last.at < 0.5:
        quality_lines.append('低净利 + 低周转，行业特性偏重资产/低毛利（如钢铁、航运、船舶）。')
    elif last.npm > 0.05 and last.at > 1.0:
        quality_lines.append('盈利与周转双佳，ROE 质量较高。')

    if last.em > 5:
        quality_lines.append('⚠️ 杠杆偏高，存在财务风险。')
    if last.net_profit < 0 and last.roe > 0:
        quality_lines.append('⚠️ 当年净亏损但 ROE 仍正，靠杠杆/周转掩盖风险。')

    advice_parts = []
    if trend == '下降' and roe_change < -2:
        advice_parts.append('❗ ROE 持续下滑，建议考察客户结构/竞争壁垒是否受损。')
    if last.npm < 0.02 and last.at < 0.5:
        advice_parts.append('适合周期型反转策略，需观察产能利用率与价格信号。')
    if last.roe > 0.15 and last.npm > 0.08 and last.at > 1.0:
        advice_parts.append('✓ 长期高质量标的，可在估值合理时持有。')
    if last.roe < 0.05:
        advice_parts.append('注意：当前 ROE 偏低，回报效率不足。')
    if not advice_parts:
        advice_parts.append('观察景气拐点与估值匹配度。')

    return {
        'summary': ' '.join(summary_lines + quality_lines),
        'trend': trend,
        'drivers': driver_lines,
        'advice': ' '.join(advice_parts),
        'tag': (
            '🌟 优质标的' if (last.roe > 0.15 and last.npm > 0.08 and last.em < 4) else
            '📊 稳态中性' if (0.08 < last.roe <= 0.15) else
            '📉 回报偏低' if last.roe <= 0.08 and last.roe > 0 else
            '🔴 警惕/亏损' if last.roe <= 0 else
            '🟡 周期/杠杆驱动'
        )
    }
