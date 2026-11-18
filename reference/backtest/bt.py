import sqlite3, statistics as st, json
c = sqlite3.connect('/tmp/db/ct.sqlite')
NAMES = {'2303':'聯電','2308':'台達電','2317':'鴻海','2330':'台積電','2382':'廣達','2454':'聯發科','2603':'長榮',
         '3231':'緯創','3711':'日月光投控','5871':'中租-KY','5904':'寶雅','6488':'環球晶'}
POINTS = [('5月底', 1, 4), ('8月底', 2, 7), ('11月底', 3, 10)]   # (名稱, 已公布季數, 已公布月營收月數)
YEARS = [2024, 2025]
med = lambda v: st.median(v) if v else None

def load(code):
    q = {}
    for r in c.execute("select report_year,quarter,revenue,gross_profit,operating_expense,non_operating,pretax_income,income_tax,"
                       "net_income,coalesce(parent_net_income,net_income),coalesce(nci,0),basic_eps from quarterly_financials "
                       "where stock_code=? and basis='single'", (code,)):
        q[(r[0], r[1])] = dict(rev=r[2], gp=r[3], opex=r[4], nonop=r[5], pretax=r[6], tax=r[7], ni=r[8], pni=r[9], nci=r[10], eps=r[11])
    m = {(r[0], r[1]): r[2] for r in c.execute(
        "select revenue_year,revenue_month,current_month_revenue from monthly_revenues where stock_code=?", (code,))}
    # 沒有月營收的月份（2023/9 以前）用該季營收 ÷ 3 補
    for (y, qq), d in q.items():
        for mm in range(qq*3-2, qq*3+1):
            m.setdefault((y, mm), d['rev']/3)
    ann = dict(c.execute("select fiscal_year,basic_eps from annual_fundamentals where stock_code=?", (code,)).fetchall())
    return q, m, ann

def prev_quarters(y, nq, n):
    """截至 y 年第 nq 季（nq=0 表示去年 Q4）往回 n 季"""
    out = []; yy, qq = y, nq
    if qq == 0: yy, qq = y-1, 4
    while len(out) < n:
        out.append((yy, qq)); qq -= 1
        if qq == 0: yy, qq = yy-1, 4
    return out

def shares_of(q, key):
    d = q[key]; return d['pni']/d['eps']

def new_method(q, m, y, nq, nm):
    last4 = prev_quarters(y, nq, 4)
    margin = sum(q[k]['pni'] for k in last4) / sum(q[k]['rev'] for k in last4)
    sh = shares_of(q, (y, nq))
    avg3 = sum(m[(y, i)] for i in range(nm-2, nm+1)) / 3
    eps = sum(q[(y, i)]['eps'] for i in range(1, nq+1))
    for qq in range(nq+1, 5):
        rev = sum(m[(y, mm)] if mm <= nm else avg3 for mm in range(qq*3-2, qq*3+1))
        eps += rev * margin / sh
    return eps

def old_method(q, m, y, nq, nm):
    ytd = [q[(y, i)] for i in range(1, nq+1)]
    S = lambda k: sum(d[k] for d in ytd)
    st_rev, gp, opex, nonop, pretax, tax, ni, pni, nci = (S(k) for k in ('rev','gp','opex','nonop','pretax','tax','ni','pni','nci'))
    actual_rev = sum(m[(y, mm)] for mm in range(1, nm+1))
    # 旺淡季：前幾個完整年度「全年 ÷ 同期累計」的中位數，至少兩年
    factors = []
    for yy in range(y-4, y):
        if all((yy, mm) in m for mm in range(1, 13)):
            factors.append(sum(m[(yy, mm)] for mm in range(1, 13)) / sum(m[(yy, mm)] for mm in range(1, nm+1)))
    annual = actual_rev * med(factors) if len(factors) >= 2 else actual_rev / nm * 12
    if annual < actual_rev: annual = actual_rev / nm * 12
    gm, opr = gp/st_rev, opex/st_rev
    taxr = min(max(tax/pretax, 0), .5) if pretax > 0 else .2
    ncir = min(max(nci/ni, 0), .5) if ni > 0 else 0
    rem_q = 4 - nq
    nonop8 = [q[k]['nonop'] for k in prev_quarters(y, nq, 8) if k in q]
    rem_nonop = med(nonop8) * rem_q if len(nonop8) >= 4 else nonop * rem_q / nq
    rem_rev = max(0, annual - st_rev)
    pretax_y = (gp + rem_rev*gm) - (opex + rem_rev*opr) + nonop + rem_nonop
    tax_y = pretax_y * taxr if pretax_y > 0 else 0
    ni_y = pretax_y - tax_y
    fnci = max(0, ni_y - (pni + nci)) * ncir
    pni_y = ni_y - nci - fnci
    return pni_y / shares_of(q, (y, nq)), len(factors)

rows = []
for code, name in NAMES.items():
    q, m, ann = load(code)
    for y in YEARS:
        actual = ann.get(y)
        for pname, nq, nm in POINTS:
            ttm = sum(q[k]['eps'] for k in prev_quarters(y, nq, 4))
            n = new_method(q, m, y, nq, nm)
            o, nf = old_method(q, m, y, nq, nm)
            rows.append(dict(code=code, name=name, year=y, point=pname, actual=actual, new=n, old=o, ttm=ttm, factors=nf))
json.dump(rows, open('/home/claude/backtest/rows.json', 'w'), ensure_ascii=False)

def err(r, k): return abs(r[k]-r['actual'])/abs(r['actual'])
print('時點    方法   誤差中位數  平均誤差  誤差<10%家數')
for pname, _, _ in POINTS + [('全部', 0, 0)]:
    sub = [r for r in rows if pname == '全部' or r['point'] == pname]
    for k, lab in (('new', '新版'), ('old', '舊版'), ('ttm', '近四季')):
        e = [err(r, k) for r in sub]
        print(f"{pname:6} {lab:4} {st.median(e):8.1%} {st.mean(e):9.1%} {sum(x<.1 for x in e):4}/{len(e)}")
wins = sum(err(r,'old') < err(r,'new') for r in rows)
print('舊版贏新版：', wins, '/', len(rows))
for k in ('new','old'):
    print(k, '平均偏差（正=高估）', f"{st.mean((r[k]-r['actual'])/abs(r['actual']) for r in rows):+.1%}")
