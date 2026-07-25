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
    else:
        # all years loss-making: pick highest ROE as best & lowest as worst
        res.best_year = max(res.years, key=lambda y: y.roe)
        res.worst_year = min(res.years, key=lambda y: y.roe)
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
    '000002.SZ':    {'industry': '房地产', 'desc': '中国房地产龙头企业（万科），',
                     'biz':     '住宅开发与销售、物业管理（万物云）、商业地产、长租公寓泊寓。',
                     'moat':    '品牌 + 物业 + 多元化融资能力。',
                     'cycle':   '强烈政策调控周期，受信贷/土地/销售政策影响。'},
    '000725.SZ':    {'industry': '面板', 'desc': '全球显示面板龙头（京东方），',
                     'biz':     'LCD/OLED/MiniLED 面板、传感器、整机 ODM、显示器件上游材料。',
                     'moat':    '产能规模 + 自主研发 + 规模制造。',
                     'cycle':   '强半导体周期，受面板价格与下游需求波动。'},
    '002230.SZ':    {'industry': 'AI软件', 'desc': '中国人工智能与智能语音技术龙头（科大讯飞），',
                     'biz':     '基础语音 AI、智慧教育、智慧医疗、智能硬件（翻译笔/录音笔）、讯飞开放平台。',
                     'moat':    '语音合成/NLP 技术 + 行业数据积累。',
                     'cycle':   '弱周期，AI 行业支出增长长期化。'},
    '002352.SZ':    {'industry': '物流', 'desc': '中国综合物流与供应链龙头（顺丰），',
                     'biz':     '时效快递、经济快递、快运、冷运、国际业务、同城急送、仓储供应链。',
                     'moat':    '自有机队 + 航空 + 仓储网络。',
                     'cycle':   '与电商/制造业消费周期相关。'},
    '002594.SZ':    {'industry': '新能源车', 'desc': '全球新能源车与动力电池龙头之一（比亚迪），',
                     'biz':     '乘用车王朝/海洋系列与方程豹高端品牌、动力电池外供、光伏储能、轨道交通。',
                     'moat':    '垂直整合（电池-芯片-电机-整车）的成本与供应链壁垒。',
                     'cycle':   '受新能源车渗透率、补贴节奏与电池材料波动。'},
    '300014.SZ':    {'industry': '新能源', 'desc': '锂电池生产设备与消费锂电池龙头（亿纬锂能），',
                     'biz':     '锂原电池（ETC/胎压）、动力电池与储能电池、锂锰圆柱电池、大圆柱动力电池。',
                     'moat':    '多品类电池技术路线（锂原+动力+储能）。',
                     'cycle':   '新能源车/储能产业强周期。'},
    '300059.SZ':    {'industry': '金融科技', 'desc': '中国互联网券商及基金代销龙头（东方财富），',
                     'biz':     '东方财富网、天天基金、天天牛证券开户/经纪/两融、Choice 数据终端。',
                     'moat':    '线上流量 + 低费率经纪 + 基金代销入口。',
                     'cycle':   '强烈资本市场与零售投资者情绪周期。'},
    '300124.SZ':    {'industry': '自动化', 'desc': '工业自动化与智能制造平台型龙头（汇川技术），',
                     'biz':     '变频器、伺服驱动、PLC 控制器、新能源汽车动力总成、工业机器人。',
                     'moat':    '工控技术积累 + 新能源车电驱动赛道布局。',
                     'cycle':   '制造业 PMI/资本开支周期敏感。'},
    '300274.SZ':    {'industry': '新能源', 'desc': '光伏逆变器与储能系统龙头（阳光电源），',
                     'biz':     '光伏逆变器、储能系统、光伏电站开发与运维、氢能。',
                     'moat':    '逆变器市占率 + 全球渠道 + 大储能系统经验。',
                     'cycle':   '光伏装机与储能项目资本开支周期。'},
    '300760.SZ':    {'industry': '医疗器械', 'desc': '中国医疗器械龙头（迈瑞医疗），',
                     'biz':     '监护仪、麻醉机、血液细胞分析、体外诊断、医学影像。',
                     'moat':    '技术平台 + 全球准入证书 + 产品升级迭代。',
                     'cycle':   '医院采购预算周期，存在贸易摩擦政策风险。'},
    '600030.SH':    {'industry': '券商', 'desc': '中国头部券商（中信证券），',
                     'biz':     '投资银行、经纪、资产管理、两融、衍生品、研究。',
                     'moat':    '全牌照 + 资本实力 + 投行客户深度。',
                     'cycle':   '强烈资本市场周期（牛市弹性极高）。'},
    '600276.SH':    {'industry': '医药', 'desc': '中国创新药研发与制造龙头（恒瑞医药），',
                     'biz':     '肿瘤药（PD1、靶向）、糖尿病管线、麻醉镇静药、造影剂。',
                     'moat':    '研发管线纵深 + 销售网络。',
                     'cycle':   '医药弱周期，受集采和医保谈判影响。'},
    '600585.SH':    {'industry': '建材', 'desc': '中国水泥龙头（海螺水泥），',
                     'biz':     '熟料/水泥/T 型水泥贸易，骨料，混凝土制品。',
                     'moat':    '成本优势（自有矿山/石灰石）+ 长江水运分销优势。',
                     'cycle':   '强地产/基建投资周期。'},
    '600809.SH':    {'industry': '白酒', 'desc': '山西汾酒，清香型白酒代表，',
                     'biz':     '汾酒、竹叶青酒、杏花村酒品牌。',
                     'moat':    '品牌/口感差异化 + 省外扩张渗透。',
                     'cycle':   '弱消费周期。'},
    '600887.SH':    {'industry': '食品', 'desc': '中国乳业龙头（伊利股份），',
                     'biz':     '液态奶、奶粉、冷饮、奶酪、健康饮品。',
                     'moat':    '奶源布局 + 渠道渗透 + 多品类强品牌。',
                     'cycle':   '弱消费周期（必需消费品）。'},
    '600941.SH':    {'industry': '电信', 'desc': '中国移动 A 股上市主体，',
                     'biz':     '移动通信、宽带、物联网、云计算、数据中心业务。',
                     'moat':    '用户基数（9 亿+）+ 基础设施 + 5G 覆盖。',
                     'cycle':   '弱周期，受 ARPU 与政企数字化投入影响。'},
    '601012.SH':    {'industry': '新能源', 'desc': '全球光伏硅片龙头（隆基绿能），',
                     'biz':     '单晶硅片、光伏组件、电池片、氢能装备。',
                     'moat':    '单晶路线 + 规模制造 + 组件品牌化。',
                     'cycle':   '光伏产能扩张与价格战周期。'},
    '601088.SH':    {'industry': '煤炭', 'desc': '中国最大的煤炭综合能源企业（中国神华），',
                     'biz':     '煤炭生产销售、铁路港口运输、燃煤发电、煤化工甲醇制烯烃。',
                     'moat':    '煤电路港一体化 + 低成本长协 + 高分红。',
                     'cycle':   '强烈能源价格周期。'},
    '601288.SH':    {'industry': '银行', 'desc': '中国四大国有银行之一（农业银行），',
                     'biz':     '三农/县域金融、公司金融、零售银行、资金业务。',
                     'moat':    '网点密度（全国 2 万+）+ 高分红。',
                     'cycle':   '受息差与不良贷款周期影响。'},
    '601398.SH':    {'industry': '银行', 'desc': '中国第一大国有银行（工商银行），',
                     'biz':     '公司金融、零售银行、资金业务、养老金托管/代理。',
                     'moat':    '资产规模最大 + 系统重要性 + 高分红。',
                     'cycle':   '利率周期敏感。'},
    '601668.SH':    {'industry': '建筑', 'desc': '中国最大建筑公司（中国建筑），',
                     'biz':     '房建、基建（桥梁/公路/铁路）、地产开发、水泥/钢结构/设计。',
                     'moat':    '工程总承包规模 + 央企信用。',
                     'cycle':   '强基建投资/地产开工周期。'},
    '601857.SH':    {'industry': '石油', 'desc': '中国最大油气公司（中国石油），',
                     'biz':     '勘探与生产、炼油化工、油品销售、天然气管道。',
                     'moat':    '上游资源 + 垄断性管网。',
                     'cycle':   '强烈国际油价周期。'},
    '601939.SH':    {'industry': '银行', 'desc': '中国四大国有银行之一（建设银行），',
                     'biz':     '基础设施金融、零售银行、普惠、住房金融。',
                     'moat':    '基建/地产信贷市场地位 + 高分红。',
                     'cycle':   '利率周期敏感。'},
    '000333.SZ':    {'industry': '家电', 'desc': '中国暖通空调与消费电器龙头（美的集团），',
                     'biz':     '暖通空调（家用/商用）、冰箱洗衣机、小家电、安得物流。',
                     'moat':    '制造效率 + 全球品牌矩阵 + 暖通数字化。',
                     'cycle':   '弱周期，受地产与消费需求影响。'},
    '09880.HK':      {'industry': '机器人', 'desc': '中国最知名的通用人形机器人上市企业（优必选），',
                     'biz':     'Walker 人形机器人、UBTECH 教育编程机器人、物流机器人、K-12 教育 AI 解决方案。',
                     'moat':    '人形机器人先发优势 + 技术积累。',
                     'cycle':   'AI 产业发展阶段驱动。'},
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
    ('00941.HK', '中国移动', 'hk'),
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
    ('09880.HK', '优必选', 'hk'),
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

_HK_STOCK_MAP = None

def _hk_stock_map():
    global _HK_STOCK_MAP
    if _HK_STOCK_MAP is not None:
        return _HK_STOCK_MAP
    path = os.path.join(CACHE_DIR, '_hk_stock_map.pkl')
    try:
        with open(path, 'rb') as f:
            _HK_STOCK_MAP = pickle.load(f)
        if _HK_STOCK_MAP and len(_HK_STOCK_MAP) > 500:
            return _HK_STOCK_MAP
    except: pass
    try:
        df = ak.stock_hk_spot_em()
        _HK_STOCK_MAP = {}
        for _, row in df.iterrows():
            code = str(row['代码'])
            name = str(row['名称'])
            _HK_STOCK_MAP[name] = code
        with open(path, 'wb') as f:
            pickle.dump(_HK_STOCK_MAP, f)
    except:
        _HK_STOCK_MAP = {}
    return _HK_STOCK_MAP

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

    # also search HK stock name map
    hmap = _hk_stock_map()
    for name, code in hmap.items():
        key = (code, name, 'hk')
        if key in seen:
            continue
        seen.add(key)
        if q == name.lower():
            priority.append({'code': code, 'name': name, 'market': 'hk'})
        elif q in name.lower():
            a_fuzzy.append({'code': code, 'name': name, 'market': 'hk'})

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

def _normalize_code(code: str) -> str:
    return code.upper().replace('.HK', '').replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('.O', '').replace('.N', '')

def get_peers(code: str) -> list[dict]:
    c = code.upper()
    # direct match first
    if c in PEER_GROUPS:
        return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in PEER_GROUPS[c]]
    nc = _normalize_code(c)
    if nc in PEER_GROUPS:
        return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in PEER_GROUPS[nc]]
    for k in PEER_GROUPS:
        if _normalize_code(k) == nc:
            return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in PEER_GROUPS[k]]

    # industry-based fallback: group by COMPANY_PROFILES industry
    profile = get_company_profile(code)
    if profile and profile.get('industry'):
        ind = profile['industry']
        peers = []
        for k, v in COMPANY_PROFILES.items():
            kn = _normalize_code(k)
            if kn == nc:
                continue
            if v.get('industry') == ind:
                pname = k
                for cs_code, cs_name, cs_mkt in COMMON_STOCKS:
                    if _normalize_code(cs_code) == kn:
                        pname = cs_name
                        break
                peers.append((k, pname, _detect_market_for_code(k)))
                if len(peers) >= 3:
                    break
        if peers:
            return [{'code': p[0], 'name': p[1], 'market': p[2]} for p in peers]
    # market reference fallback: rewrite market detection by checking profile
    market = profile.get('market', '') if profile else ''
    if not market:
        for entry in COMMON_STOCKS:
            if _normalize_code(entry[0]) == nc:
                market = entry[2]
                break
    if not market:
        market = _detect_market_for_code(code)
        if market == 'us' and nc.isdigit():
            market = 'a' if len(nc) == 6 else 'hk' if len(nc) == 5 else 'us'
    refs = {
        'a': [('600519.SH', '贵州茅台'), ('000858.SZ', '五粮液'), ('600036.SH', '招商银行')],
        'hk': [('00700.HK', '腾讯控股'), ('03690.HK', '美团'), ('00941.HK', '中国移动')],
        'us': [('AAPL', '苹果'), ('NVDA', '英伟达'), ('MSFT', '微软')],
    }
    stocks = refs.get(market, refs['us'])
    return [{'code': s[0], 'name': s[1], 'market': market, 'ref': True} for s in stocks if _normalize_code(s[0]) != nc]
    return []

def _detect_market_for_code(code: str) -> str:
    c = code.upper()
    if '.HK' in c: return 'hk'
    if '.SH' in c or '.SZ' in c or '.BJ' in c: return 'a'
    return 'us'

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

# ── business model classification ───────────────────────────────
MODEL_BRAND       = 'brand'       # 品牌溢价型：看净利润率
MODEL_TURNOVER    = 'turnover'    # 周转效率型：看资产周转率
MODEL_NETWORK     = 'network'     # 规模网络型：看网络效应/用户增长
MODEL_LEVERAGE    = 'leverage'    # 杠杆经营型：看权益乘数/资本效率
MODEL_CYCLICAL     = 'cyclical'    # 周期重资产型：看产能利用率/价格信号
MODEL_LOSSMARKER = 'lossmaking'   # 亏损/投入期：杜邦解释力有限

def classify_business_model(res: DupontResult) -> dict:
    "Returns {model, key_metric, label, desc, strategy}"
    if not res.years:
        return {'model': MODEL_LOSSMARKER, 'key_metric': 'roe', 'label': '亏损/投入期', 'desc': '', 'strategy': ''}

    last = res.years[-1]
    profile = get_company_profile(res.company.code)
    ind = profile.get('industry', '') if profile else ''

    # 亏损企业 → lossmaking
    if last.net_profit <= 0 and last.roe <= 0:
        return {
            'model': MODEL_LOSSMARKER, 'key_metric': 'roe',
            'label': '早期投入/亏损',
            'desc': '该公司当前处于投入期，尚无法形成稳定的 ROE 结构。传统杜邦三项对亏损企业解释力有限——更应关注营收增速、毛利率改善和现金流是否转正。杜邦分解仅供参考，不代表估值判断。',
            'strategy': '适合早期赛道型布局，关注业绩拐点信号。'
        }

    # rule-based classification
    if ind in ('银行', '保险', '券商', '金融科技'):
        return {
            'model': MODEL_LEVERAGE, 'key_metric': 'em',
            'label': '杠杆经营型',
            'desc': '该企业属于金融/资本密集型行业。赚钱的核心不是高毛利，而是用大量资本撬动收益——权益乘数越高，同等净息差下 ROE 越大。因此解读杜邦时应重点看权益乘数的变化，而非利润率。',
            'strategy': '关注息差/利差变化及资本充足率。高 ROCE > 高 ROE 表面。',
        }
    if ind in ('电商零售', '商超零售', '物流'):
        return {
            'model': MODEL_TURNOVER, 'key_metric': 'at',
            'label': '周转效率型',
            'desc': '零售/物流企业赚钱靠的是「薄利多销」——每一块钱资产能创造多少收入，是构建利润的基础。净利润率通常较低（批发差价模式），所以资产周转率才是 ROE 的主要驱动。解读时应重点关注周转率高低与趋势。',
            'strategy': '流通效率 > 毛利率。关注存货天数和供应链执行力。',
        }
    if ind in ('医药', '白酒', '食品', '医疗器械'):
        return {
            'model': MODEL_BRAND, 'key_metric': 'npm',
            'label': '品牌溢价型',
            'desc': '该企业赚钱靠品牌护城河和定价权——消费者愿意为品牌支付溢价，而非靠多建工厂或多周转。因此净利润率是杜邦中的关键变量。如果溢价能力受损，ROE 会被严重侵蚀。',
            'strategy': '长期持有看品牌力 + 渠道渗透。',
        }
    if ind in ('互联网平台', '本地生活', 'AI软件'):
        return {
            'model': MODEL_NETWORK, 'key_metric': 'npm',
            'label': '规模网络型',
            'desc': '互联网平台企业早期不依赖杜邦三项，需要用户规模和网络效应夯实后再看盈利能力。现阶段已进入 PE 变现期，净利润率的改善程度是判断 ROE 质量的核心。周转率与杠杆为辅助——平台轻资产属性使资产周转率天然较高但不一定稳定维持。',
            'strategy': '先看用户数/变现率 → 再看 ROE 质量。',
        }
    if ind in ('船舶制造', '煤炭', '石油', '建筑', '面板', '建材'):
        return {
            'model': MODEL_CYCLICAL, 'key_metric': 'at',
            'label': '周期重资产型',
            'desc': '该企业处于强周期行业，资本密集、产能固定，在行业低迷期利润急剧收缩。ROE 变化主要来自使用率（产能利用率）的波动——当价格信号回升，产能装入订单时，周转率和利润率相向而行。解时重点是产能利用率/收入上升拐点，而不是净利润率结构。',
            'strategy': '周期型：买入 PB 低估 + 去产能底部，卖出景气高点 ROE 飙升。',
        }

    # fallback: data-driven
    if last.npm > 0.10 and last.at < 1.0:
        return {
            'model': MODEL_BRAND, 'key_metric': 'npm',
            'label': '品牌溢价型', 'desc': '数据显示该企业高净利+低周转，符合品牌/技术型商业模式。', 'strategy': '看定价权。',
        }
    if last.at > 1.5 and last.npm < 0.05:
        return {
            'model': MODEL_TURNOVER, 'key_metric': 'at',
            'label': '周转效率型', 'desc': '数据显示该企业低利高周转，符合零售/流通模式。', 'strategy': '看供应链效率。',
        }
    if last.em > 4 and last.npm < 0.06:
        return {
            'model': MODEL_LEVERAGE, 'key_metric': 'em',
            'label': '杠杆经营型', 'desc': '数据显示该企业依赖高杠杆驱动 ROE。', 'strategy': '关注资本充足率。',
        }
    return {
        'model': MODEL_BRAND, 'key_metric': 'npm',
        'label': '通用综合型', 'desc': '企业尚未明确归类，利润/周转/杠杆较为均衡。', 'strategy': '多维观察 ROE 质量。',
    }

def _generate_analysis(res: DupontResult) -> dict:
    if len(res.years) < 2:
        return {'summary': '数据不足，至少需要2年数据进行分析', 'trend': '', 'advice': '', 'drivers': []}
    last = res.years[-1]
    prev = res.years[-2]
    trend = '上升' if last.roe > prev.roe else '下降'
    roe_change = (last.roe - prev.roe) * 100

    model = classify_business_model(res)
    key = model['key_metric']
    key_name = {'npm': '净利润率', 'at': '资产周转率', 'em': '权益乘数', 'roe': 'ROE'}[key]

    summary_lines = []
    summary_lines.append(f"近年 ROE 呈{trend}趋势，最新{last.year}年 {last.roe*100:.2f}%（{'+' if roe_change>=0 else ''}{roe_change:.2f}pct）。")

    driver_lines = []
    if key == 'npm':
        npm_chg = (last.npm - prev.npm)*100
        driver_lines.append(f"🎯 核心指标：净利润率 ({prev.npm*100:.2f}% → {last.npm*100:.2f}%，{'+'if npm_chg>=0 else ''}{npm_chg:.2f}pct)")
    elif key == 'at':
        at_chg = (last.at - prev.at)*100
        driver_lines.append(f"🎯 核心指标：资产周转率 ({prev.at:.3f} → {last.at:.3f}，{'+'if at_chg>=0 else ''}{at_chg:.3f}pct)")
    elif key == 'em':
        em_chg = (last.em - prev.em)*100
        driver_lines.append(f"🎯 核心指标：权益乘数 ({prev.em:.3f} → {last.em:.3f}，{'+'if em_chg>=0 else ''}{em_chg:.3f}pct)")

    # secondary changes
    if key != 'npm' and abs(last.npm - prev.npm) > 0.02:
        driver_lines.append(f"净利率 {prev.npm*100:.2f}% → {last.npm*100:.2f}%")
    if key != 'at' and abs(last.at - prev.at) > 0.15:
        driver_lines.append(f"周转率 {prev.at:.3f} → {last.at:.3f}")
    if key != 'em' and abs(last.em - prev.em) > 0.5:
        driver_lines.append(f"权益乘数 {prev.em:.3f} → {last.em:.3f}")

    # model-specific commentary
    model_lines = []
    if model['model'] == MODEL_BRAND:
        model_lines.append(f'💡 品牌溢价型企业的关键：{key_name}如能维持高位（>10%），ROE 通常持续高质量。')
    elif model['model'] == MODEL_TURNOVER:
        model_lines.append(f'💡 周转效率型的关键：{key_name}越高，利润覆盖成本越大。需跟踪库存周转天数。')
    elif model['model'] == MODEL_LEVERAGE:
        model_lines.append(f'💡 杠杆经营型的关键：{key_name}反映资本放大效率，但也增加风险——需关注不良/利率变动。')
    elif model['model'] == MODEL_NETWORK:
        model_lines.append(f'💡 网络型企业已进入盈利变现期：{key_name}的波动揭示用户/变现力度。')
    elif model['model'] == MODEL_CYCLICAL:
        model_lines.append(f'💡 周期重资产型的关键：{key_name}反映产能利用率——当景气周期到来时，收入/利润/周转率猛烈回升。')
    elif model['model'] == MODEL_LOSSMARKER:
        model_lines.append('该企业仍在亏损阶段，ROE 受负净利润挤压。聚焦营收增速与毛利率改善，杜邦呈观仅供参考。')

    # quality
    quality_lines = []
    if model['model'] == MODEL_LOSSMARKER:
        quality_lines.append('当前企业未盈利。')
    elif last.roe > 0.15 and last.npm > 0.08 and last.em < 5:
        quality_lines.append('ROE 质量较高：产品利润率与杠杆在健康范围。')
    elif last.at > 1.5:
        quality_lines.append('高周转模式运行良好。')
    elif last.em > 5:
        quality_lines.append('⚠️ 杠杆偏高，存在财务风险。')

    advice_parts = []
    if model['model'] == MODEL_BRAND:
        if trend == '下降' and roe_change < -2:
            advice_parts.append('品牌溢价有所下滑，检查定价权是否受损。')
    elif model['model'] == MODEL_TURNOVER:
        advice_parts.append('核心看库存周转与毛利率变化。')
    elif model['model'] == MODEL_LEVERAGE:
        advice_parts.append('核心看净息差/利差与不良贷款率。')
    advice_parts.append(model['strategy'])
    if not advice_parts:
        advice_parts.append('观察景气拐点与估值匹配度。')

    return {
        'summary': ' '.join(summary_lines + model_lines + quality_lines),
        'trend': trend,
        'drivers': driver_lines,
        'advice': ' '.join(advice_parts),
        'model': model,
        'tag': (
            '🌟 优质标的' if (last.roe > 0.15 and last.npm > 0.08 and last.em < 4) else
            '📊 稳态中性' if (0.08 < last.roe <= 0.15) else
            '📉 回报偏低' if last.roe <= 0.08 and last.roe > 0 else
            '🔴 警惕/亏损' if last.roe <= 0 else
            '🟡 周期/杠杆驱动'
        )
    }

# ── PEG valuation ───────────────────────────────────────────────
def _ni_cagr(net_profits: list[tuple], years_back=3) -> float:
    "Calculate 3-year CAGR of net profit from sorted (year, net_profit) pairs"
    if len(net_profits) < years_back + 1:
        return None
    ps = sorted(net_profits, key=lambda x: x[0])
    start = ps[-(years_back+1)]
    end = ps[-1]
    if start[1] <= 0 or end[1] <= 0:
        return None
    return (end[1] / start[1]) ** (1.0 / years_back) - 1

def fetch_peg_a(symbol: str, net_profits: list[tuple]) -> Optional[dict]:
    try:
        info = ak.stock_individual_info_em(symbol=symbol)
        info_dict = dict(zip(info['item'].values, info['value'].values))
        total_shares = float(info_dict.get('总股本', 0))
        eps = None
        if total_shares > 0 and net_profits:
            latest_np = net_profits[-1][1]
            eps = latest_np / total_shares
        cagr = _ni_cagr(net_profits)
        return {
            'pe_ttm': None,
            'eps': round(eps, 4) if eps else None,
            'ni_cagr_3y': round(cagr * 100, 2) if cagr else None,
            'peg': None,
            'zone': '',
            'warning': 'A 股 PE 需实时行情，当前展示 EPS 与利润增速。',
            'note': 'PEG 核心看利润增速是否支撑估值，可与券商 PE 结合使用。',
        }
    except:
        return None

def fetch_peg_us(code: str, net_profits: list[tuple]) -> Optional[dict]:
    try:
        ind = ak.stock_financial_us_analysis_indicator_em(symbol=code)
        if ind.empty:
            return None
        latest = ind.iloc[0]
        eps = float(latest.get('BASIC_EPS', 0))
        cagr = _ni_cagr(net_profits)
        return {
            'pe_ttm': None,
            'eps': round(eps, 4) if eps else None,
            'ni_cagr_3y': round(cagr * 100, 2) if cagr else None,
            'peg': None,
            'zone': '',
            'warning': '美股实时 PE 暂不可用' if not eps else '',
            'note': 'PE/PEG 需实时股价，当前仅展示 EPS 与利润增速。',
        }
    except:
        return None

def fetch_peg_hk(symbol: str, net_profits: list[tuple]) -> Optional[dict]:
    try:
        eps = None
        try:
            indicator = ak.stock_hk_financial_indicator_em(symbol=symbol)
            indicator_dict = dict(zip(indicator['item'].values, indicator['value'].values))
            eps = float(indicator_dict.get('每股盈利', 0))
        except:
            pass
        cagr = _ni_cagr(net_profits)
        return {
            'pe_ttm': None,
            'eps': round(eps, 4) if eps else None,
            'ni_cagr_3y': round(cagr * 100, 2) if cagr else None,
            'peg': None,
            'zone': '',
            'warning': '港股 PE 需实时行情，当前展示 EPS 与利润增速。',
            'note': 'PEG 核心看利润增速是否支撑估值，可参考券商目标 PE。',
        }
    except:
        return None

def fetch_peg(code: str, market: str, net_profits: list[tuple]) -> Optional[dict]:
    cached_key = f"peg_{market}_{code}".replace('.', '_')
    cached = _cache_get('peg_' + code, market)
    if cached:
        return cached
    result = None
    if market == 'a':
        sym = code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
        result = fetch_peg_a(sym, net_profits)
    elif market == 'hk':
        result = fetch_peg_hk(code.replace('.HK', '').zfill(5), net_profits)
    else:
        result = fetch_peg_us(code, net_profits)
    if result:
        _cache_set('peg_' + code, market, result)
    return result

def _peg_result(pe_ttm, eps, peg, net_profits, warning=''):
    cagr = _ni_cagr(net_profits)
    zone = ''
    if pe_ttm:
        if peg and peg < 0.8:
            zone = '🟢 低估区间（PEG < 0.8）'
        elif peg and 0.8 <= peg <= 1.5:
            zone = '🟡 合理区间（0.8 ≤ PEG ≤ 1.5）'
        elif peg and peg > 1.5:
            zone = '🔴 高估区间（PEG > 1.5），增长已被透支'
        elif pe_ttm > 50:
            zone = '🔴 高 PE（>50x），估值压力较大'
        elif pe_ttm < 10:
            zone = '🟢 低 PE（<10x），可能被低估'
    return {
        'pe_ttm': round(pe_ttm, 2) if pe_ttm else None,
        'eps': round(eps, 4) if eps else None,
        'ni_cagr_3y': round(cagr * 100, 2) if cagr else None,
        'peg': round(peg, 2) if peg else None,
        'zone': zone,
        'warning': warning,
        'note': '净利润 CAGR 基于近 3 年财务数据；周期股 PEG 仅供参考。',
    }
