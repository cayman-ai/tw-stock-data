"""共用工具：禮貌抓取、民國年換算、數字解析、表格整理。"""
from __future__ import annotations

import io
import time
import warnings
from pathlib import Path

import pandas as pd
import requests

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
PARTS = ROOT / "data" / "parts"
HISTORY = ROOT / "data" / "history"
LATEST = ROOT / "data" / "latest"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) tw-stock-data/1.0 (+https://github.com/cayman-ai/tw-stock-data)"}
DELAY = 1.5          # 每次請求最少間隔（秒）
_last = [0.0]
_session = requests.Session()
_session.headers.update(UA)


def fetch(url: str, data: dict | None = None, *, retries: int = 3, timeout: int = 180) -> str:
    """GET（data 為 None）或 POST，失敗退避重試。回傳文字。"""
    for attempt in range(1, retries + 1):
        wait = DELAY - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            if data is None:
                r = _session.get(url, timeout=timeout)
            else:
                r = _session.post(url, data=data, timeout=timeout)
            _last[0] = time.time()
            r.raise_for_status()
            if "html" in r.headers.get("content-type", "") and "charset" not in r.headers.get("content-type", "").lower():
                r.encoding = r.apparent_encoding
            text = r.text
            if "查詢過於頻繁" in text or "FOR SECURITY REASONS" in text.upper():
                raise RuntimeError("官方網站回應查詢過於頻繁")
            return text
        except Exception as e:  # noqa: BLE001
            _last[0] = time.time()
            if attempt == retries:
                raise
            back = 10 * attempt
            print(f"  ! 第 {attempt} 次失敗（{e}），{back} 秒後重試", flush=True)
            time.sleep(back)
    raise RuntimeError("unreachable")


def num(v):
    """官方表格數字 → float；'--'、空白、NaN → None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "--", "-", "－", "nan", "NaN", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc(year: int) -> int:
    return year - 1911


def read_tables(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(io.StringIO(html), flavor="lxml")
    except ValueError:
        return []


def flatten(df: pd.DataFrame) -> pd.DataFrame:
    """多層欄位只留最後一層，並去掉欄名空白。"""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[-1]) for c in df.columns]
    df.columns = [str(c).replace(" ", "").replace("　", "") for c in df.columns]
    return df


def is_common_code(code) -> bool:
    """普通股代號：4 碼數字（排除特別股 1101B、ETF 00xxxx 等）。"""
    s = str(code).strip()
    return len(s) == 4 and s.isdigit() and not s.startswith("00")


def pick(row, *names):
    for n in names:
        if n in row and num(row[n]) is not None:
            return num(row[n])
    return None
