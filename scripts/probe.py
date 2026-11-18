"""實測官方資料來源：抓一次存原始回應到 out/probe/"""
import requests, time, json, sys, pathlib
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) tw-stock-data/0.1"}
O = pathlib.Path("out/probe"); O.mkdir(parents=True, exist_ok=True)
MOPS = "https://mopsov.twse.com.tw/mops/web/"
def form(**k): return dict(encodeURIComponent=1, step=1, firstin=1, off=1, isQuery="Y", **k)
P = [
 ("rev_sii_html", "GET", "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_115_10_0.html", None),
 ("rev_otc_html", "GET", "https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_115_10_0.html", None),
 ("rev_sii_old",  "GET", "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_110_1_0.html", None),
 ("is_sii", "POST", MOPS+"ajax_t163sb04", form(TYPEK="sii", year="115", season="03")),
 ("is_otc", "POST", MOPS+"ajax_t163sb04", form(TYPEK="otc", year="115", season="03")),
 ("bs_sii", "POST", MOPS+"ajax_t163sb05", form(TYPEK="sii", year="115", season="03")),
 ("cf_sii_sb20", "POST", MOPS+"ajax_t163sb20", form(TYPEK="sii", year="115", season="03")),
 ("is_sii_old", "POST", MOPS+"ajax_t163sb04", form(TYPEK="sii", year="110", season="04")),
 ("div_sii_sb27", "POST", MOPS+"ajax_t108sb27", form(TYPEK="sii", year="114", type1="0", qryType="1")),
 ("div_t05st09", "POST", MOPS+"ajax_t05st09_2", form(co_id="2330", year="114", qryType="1", TYPEK="all")),
 ("co_sii", "GET", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L", None),
 ("co_otc", "GET", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", None),
 ("pe_sii", "GET", "https://www.twse.com.tw/exchangeReport/BWIBBU_ALL?response=json", None),
 ("px_sii", "GET", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", None),
 ("pe_otc", "GET", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", None),
 ("px_otc", "GET", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", None),
 ("div_sii_api", "GET", "https://openapi.twse.com.tw/v1/opendata/t187ap45_L", None),
 ("div_otc_api", "GET", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap39_O", None),
]
only = set(sys.argv[1:])
for name, m, url, data in P:
    if only and name not in only: continue
    t = time.time()
    try:
        r = requests.request(m, url, data=data, headers=UA, timeout=60)
        r.encoding = r.apparent_encoding if 'html' in r.headers.get('content-type','') else r.encoding
        (O/f"{name}.txt").write_text(r.text, encoding="utf-8")
        print(f"{name}: {r.status_code} {len(r.content)} bytes {time.time()-t:.1f}s {r.headers.get('content-type')}")
    except Exception as e:
        print(f"{name}: ERROR {e}")
    time.sleep(2)
