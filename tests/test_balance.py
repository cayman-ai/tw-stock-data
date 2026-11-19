"""資產負債表解析的離線測試：python tests/test_balance.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from twdata import xbrl  # noqa: E402
from build_site import contract_info, trend  # noqa: E402

FX = ROOT / "tests" / "fixtures"


def load(name, y, q):
    return xbrl.parse_balance((FX / f"{name}.html").read_text(encoding="utf-8"), y, q)


def main():
    a = load("bs_6488_2025Q4", 2025, 4)       # 第 4 季只有兩欄
    assert a[(2025, 4)]["cl_current"] == 8022772 and a[(2024, 4)]["cl_noncurrent"] == 19880163
    assert a[(2025, 4)]["receivables"] == 10062969 + 50301          # 一般＋關係人
    b = load("bs_2376_2026Q3", 2026, 3)       # 第 1～3 季三欄，取第一欄與最後一欄（去年同期）
    assert b[(2025, 3)]["inventory"] == 57765296 and b[(2026, 3)]["cl_noncurrent"] is None
    c = load("bs_2330_2026Q3", 2026, 3)       # 沒有單獨列示合約負債
    assert c[(2026, 3)]["cl_current"] is None and c[(2026, 3)]["total_liabilities"] == 3009660084
    assert load("bs_2376_2026Q3", 2026, 2) == "頁面格式不符"     # 期別對不上不硬套
    assert xbrl.parse_balance("查無資料", 2026, 3) == "查無資料"
    assert trend(111, 100) == "up" and trend(89, 100) == "down" and trend(105, 100) == "flat" and trend(5, 0) == "up"
    qd = {(2026, 3): {"revenue": 100.0}, (2025, 3): {"revenue": 102.0}}
    stuck = contract_info({(2026, 3): {"cl_current": 50.0, "inventory": 300.0, "total_liabilities": 500.0, "total_assets": 1000.0},
                           (2025, 3): {"cl_current": 20.0, "inventory": 100.0}}, qd, 400.0)
    assert stuck["signal"]["level"] == "warn" and "出貨卡關" in stuck["signal"]["text"]
    assert stuck["debt_ratio_ex_cl"] == 0.45 and stuck["months_of_revenue"] == 1.5
    tiny = contract_info({(2026, 3): {"cl_current": 1.0}, (2025, 3): {"cl_current": 0.5}}, qd, 400.0)
    assert tiny["small"] and tiny["signal"] is None
    print("全部通過")


if __name__ == "__main__":
    main()
