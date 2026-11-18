"""診斷：XBRL 清單與下載各步驟耗時。"""
import json, re, time, html, requests
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) tw-stock-data/1.0"}
def t(label, fn):
    s = time.time()
    try:
        r = fn(); print(f"{label}: {r.status_code} {len(r.content)} bytes {time.time()-s:.1f}s", flush=True); return r
    except Exception as e:
        print(f"{label}: ERROR {type(e).__name__} {e} {time.time()-s:.1f}s", flush=True)
r = t("redirectToOld", lambda: requests.post("https://mops.twse.com.tw/mops/api/redirectToOld", headers=UA, timeout=30,
      json={"apiName": "ajax_t203sb01", "parameters": {"co_id": "2330", "TYPEK": "all", "encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1}}))
if r is not None:
    print(r.text[:300], flush=True)
    try:
        url = r.json()["result"]["url"]; print("url", url, flush=True)
        l = t("listing", lambda: requests.get(url, headers=UA, timeout=30))
        txt = l.text
        m = re.findall(r"window\.open\('(/server-java/FileDownLoad\?[^']+)'", txt)
        print("docs", len(m), txt[:300] if not m else m[0], flush=True)
        if m:
            d = t("download", lambda: requests.get("https://mopsov.twse.com.tw" + html.unescape(m[0]), headers=UA, timeout=60))
            print(d.headers.get("content-type"), d.content[:200], flush=True)
    except Exception as e:
        print("parse error", e, flush=True)
# 直接用 mopsov 舊站的 t203sb01
t("mopsov t203sb01", lambda: requests.post("https://mopsov.twse.com.tw/mops/web/ajax_t203sb01", headers=UA, timeout=30,
  data=dict(encodeURIComponent=1, step=1, firstin=1, off=1, co_id="2330", TYPEK="all")))
