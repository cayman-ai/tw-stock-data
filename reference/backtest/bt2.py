exec(open('bt.py').read().split("rows = []")[0])
def variant(q, m, y, nq, nm, rev_mode, margin_mode):
    # 營收：剩餘月份怎麼估
    actual_rev = sum(m[(y, mm)] for mm in range(1, nm+1))
    if rev_mode == 'seasonal':
        f = [sum(m[(yy,mm)] for mm in range(1,13))/sum(m[(yy,mm)] for mm in range(1,nm+1)) for yy in range(y-4,y) if all((yy,mm) in m for mm in range(1,13))]
        annual = actual_rev*med(f) if len(f)>=2 else actual_rev/nm*12
    elif rev_mode == 'yoy3':   # 去年同月 × (1+近 3 個月年增率)
        g = sum(m[(y,i)] for i in range(nm-2,nm+1))/sum(m[(y-1,i)] for i in range(nm-2,nm+1))
        annual = actual_rev + sum(m[(y-1,mm)] for mm in range(nm+1,13))*g
    elif rev_mode == 'yoyytd': # 去年同月 × (1+今年累計年增率)
        g = actual_rev/sum(m[(y-1,i)] for i in range(1,nm+1))
        annual = actual_rev + sum(m[(y-1,mm)] for mm in range(nm+1,13))*g
    st_rev = sum(q[(y,i)]['rev'] for i in range(1,nq+1))
    rem_rev = max(0, annual - st_rev)
    if margin_mode == 'ytd':
        mg = sum(q[(y,i)]['pni'] for i in range(1,nq+1))/st_rev
    elif margin_mode == 'ttm':
        k = prev_quarters(y,nq,4); mg = sum(q[x]['pni'] for x in k)/sum(q[x]['rev'] for x in k)
    elif margin_mode == 'latestq':
        mg = q[(y,nq)]['pni']/q[(y,nq)]['rev']
    pub = sum(q[(y,i)]['eps'] for i in range(1,nq+1))
    return pub + rem_rev*mg/shares_of(q,(y,nq))
R=[]
for code in NAMES:
    q,m,ann=load(code)
    for y in YEARS:
        for pname,nq,nm in POINTS:
            r=dict(point=pname,actual=ann[y],old=old_method(q,m,y,nq,nm)[0],new=new_method(q,m,y,nq,nm),ttm=sum(q[k]['eps'] for k in prev_quarters(y,nq,4)))
            for rv in ('seasonal','yoy3','yoyytd'):
                for mg in ('ytd','ttm','latestq'):
                    r[rv+'/'+mg]=variant(q,m,y,nq,nm,rv,mg)
            r['舊+近四季平均']=(r['old']+r['ttm'])/2
            r['舊+yoyytd/latestq平均']=(r['old']+r['yoyytd/latestq'])/2
            R.append(r)
keys=[k for k in R[0] if k not in('point','actual')]
e=lambda r,k:abs(r[k]-r['actual'])/abs(r['actual'])
print(f"{'方法':26}"+''.join(f"{p:>14}" for p in ('5月底','8月底','11月底','全部')))
for k in keys:
    line=f"{k:26}"
    for p in ('5月底','8月底','11月底',None):
        s=[e(r,k) for r in R if p is None or r['point']==p]
        line+=f"  {st.median(s):5.1%}/{st.mean(s):5.1%}"
    print(line)
