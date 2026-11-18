"""診斷：資本支出替代來源的速度。"""
import time, requests, io, zipfile
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) tw-stock-data/1.0"}
def t(label, fn):
    s = time.time()
    try:
        r = fn(); print(f"{label}: {r.status_code} {len(r.content)} bytes {time.time()-s:.1f}s {r.headers.get('content-type')} {r.headers.get('content-length')}", flush=True); return r
    except Exception as e:
        print(f"{label}: ERROR {type(e).__name__} {e} {time.time()-s:.1f}s", flush=True)
# 1. 單一公司現金流量表網頁
for co in ("2330", "5904"):
    r = t(f"t164sb05 {co}", lambda: requests.post("https://mopsov.twse.com.tw/mops/web/ajax_t164sb05", headers=UA, timeout=60,
          data=dict(encodeURIComponent=1, step=1, firstin=1, off=1, queryName="co_id", inpuType="co_id", TYPEK="all", isnew="false",
                    co_id=co, year="115", season="03")))
    if r is not None:
        i = r.text.find("取得不動產")
        print("   ", r.text[i-50:i+300].replace("\n", " ") if i >= 0 else r.text[:300].replace("\n", " "), flush=True)
    time.sleep(2)
# 2. 整批 XBRL 壓縮檔（只看檔案大小與前 5MB 速度）
for url in ["https://mopsov.twse.com.tw/server-java/FileDownLoad?step=9&fileName=tifrs-2026Q3.zip&filePath=/home/html/nas/ifrs/2026/",
            "https://mopsov.twse.com.tw/server-java/FileDownLoad?step=9&fileName=tifrs-2025Q4.zip&filePath=/home/html/nas/ifrs/2025/"]:
    s = time.time()
    try:
        with requests.get(url, headers=UA, timeout=60, stream=True) as r:
            n = 0
            for ch in r.iter_content(1 << 16):
                n += len(ch)
                if n > 5_000_000 or time.time() - s > 60: break
            print(f"bulk {url[-40:]}: {r.status_code} {r.headers.get('content-type')} 總長 {r.headers.get('content-length')} 讀 {n} bytes {time.time()-s:.1f}s", flush=True)
    except Exception as e:
        print("bulk ERROR", e, flush=True)
