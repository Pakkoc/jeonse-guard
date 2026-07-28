"""CLI 진입점.

  jeonse-guard scan "서울 마포구 성산동 200-1" --deposit 35000 --name 성산시영 --area 50
  jeonse-guard watch --watchlist watchlist.toml
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "-", text).strip("-")[:60] or "scan"


def _to_jsonable(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    return obj


def cmd_scan(args: argparse.Namespace) -> int:
    from . import pipeline, report

    result = pipeline.scan(
        args.address,
        deposit_10k=args.deposit,
        unit_name=args.name,
        area_m2=args.area,
        asset_type=args.asset,
        months=args.months,
    )
    markdown = report.render(result)

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    date = result.scanned_at[:10]
    path = out_dir / f"{date}-{_slug(args.address)}.md"
    path.write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2))
    else:
        print(markdown)
    print(f"\n[리포트 저장] {path}", file=sys.stderr)
    warn = sum(1 for s in result.signals if s.level == "warn")
    print(f"[요약] 검토 신호 {len(result.signals)}건 (warn {warn}건)", file=sys.stderr)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from . import watch

    events = watch.run_watch(
        watchlist_path=pathlib.Path(args.watchlist),
        snapshots_dir=pathlib.Path(args.snapshots),
        reports_dir=pathlib.Path(args.reports),
    )
    for event in events:
        print(json.dumps(_to_jsonable(event), ensure_ascii=False))
    print(f"[watch] 이벤트 {len(events)}건", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jeonse-guard", description="전세 안전진단 + 보증금 워치독")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="주소 1건 원샷 안전진단")
    p_scan.add_argument("address", help="지번 주소 (예: '서울 마포구 성산동 200-1')")
    p_scan.add_argument("--deposit", type=int, default=None, help="전세보증금 (만원 단위, 예: 35000 = 3억5천)")
    p_scan.add_argument("--name", default=None, help="단지/건물명 필터 (예: 성산시영)")
    p_scan.add_argument("--area", type=float, default=None, help="전용면적 ㎡ (±15%% 면적대 매칭)")
    p_scan.add_argument("--asset", default="apartment", choices=["apartment", "villa", "officetel"], help="자산 유형")
    p_scan.add_argument("--months", type=int, default=12, help="실거래 조회 개월 수 (기본 12)")
    p_scan.add_argument("--out", default="reports", help="리포트 저장 디렉토리")
    p_scan.add_argument("--json", action="store_true", help="stdout에 JSON 출력")
    p_scan.set_defaults(func=cmd_scan)

    p_watch = sub.add_parser("watch", help="watchlist 전체 재스캔 + diff 이벤트 출력")
    p_watch.add_argument("--watchlist", default="watchlist.toml")
    p_watch.add_argument("--snapshots", default="snapshots")
    p_watch.add_argument("--reports", default="reports")
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
