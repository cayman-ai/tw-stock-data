from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run_step(project: Path, label: str, script: str, code: str, *options: str) -> str | None:
    print(f"\n[{code}｜{label}]", flush=True)
    command = [sys.executable, str(project / "scripts" / script), code, *options]
    try:
        subprocess.run(command, cwd=project, check=True)
        return None
    except subprocess.CalledProcessError:
        message = f"{code} 的「{label}」更新失敗，其他項目與公司會繼續更新。"
        print(message, file=sys.stderr, flush=True)
        return message


def main() -> None:
    parser = argparse.ArgumentParser(description="一次更新公司的月營收、季度財報、股利與股權變動資料")
    parser.add_argument("stock_codes", nargs="+", help="股票代號，例如 2330 2308 6488")
    parser.add_argument("--periods", type=int, default=20, help="季度財報回溯期數，預設 20")
    parser.add_argument("--months", type=int, default=36, help="月營收回溯月數，預設 36")
    parser.add_argument("--years", type=int, default=5, help="股利與股權事件回溯年數，預設 5")
    parser.add_argument("--skip-revenue", action="store_true", help="保留現有月營收，不重新下載")
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[1]
    codes = [str(code).strip() for code in args.stock_codes]
    invalid = [code for code in codes if not code.isalnum() or not 4 <= len(code) <= 6]
    if invalid:
        raise SystemExit(f"股票代號格式錯誤：{', '.join(invalid)}")

    failures: list[str] = []
    for code in codes:
        if not args.skip_revenue:
            result = run_step(project, "月營收", "ingest_monthly_revenue.py", code, "--months", str(args.months))
            if result: failures.append(result)
        for label, script, options in [
            ("季度財報", "ingest_xbrl.py", ("--periods", str(args.periods))),
            ("股利與自由現金流", "ingest_capital_history.py", ("--years", str(args.years))),
            ("股權變動", "ingest_equity_events.py", ("--years", str(args.years))),
            ("近三年每日有效本益比", "ingest_valuation_history.py", ("--months", "36")),
        ]:
            result = run_step(project, label, script, code, *options)
            if result: failures.append(result)
    if failures:
        print("\n更新完成，但有部分資料需要稍後重試：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        raise SystemExit(1)
    print("\n全部更新完成。", flush=True)


if __name__ == "__main__":
    main()
