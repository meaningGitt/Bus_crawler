"""앉을지도 교통 데이터 수집 파이프라인 메인 실행기"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml


def load_config() -> dict:
    """config.yaml 로드."""
    config_path = Path("config.yaml")
    if not config_path.exists():
        print("[ERROR] config.yaml 파일을 찾을 수 없습니다.")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict) -> logging.Logger:
    """로깅 설정 (콘솔 + 파일)."""
    logs_dir = Path(config.get("output", {}).get("logs_dir", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"run_{timestamp}.log"

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 기존 핸들러 제거
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 콘솔 핸들러 (INFO 이상)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # 파일 핸들러 (DEBUG 이상)
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s (%(filename)s:%(lineno)d): %(message)s",
    )
    file_handler.setFormatter(file_format)
    root_logger.addHandler(file_handler)

    logger = logging.getLogger("main")
    logger.info(f"로그 파일: {log_file}")
    return logger


def cmd_inventory(config: dict, logger: logging.Logger):
    """기존 데이터 스캔 및 갭 분석."""
    from pipeline.inventory import DataInventory

    logger.info("=== 기존 데이터 인벤토리 시작 ===")
    start = time.time()

    inventory = DataInventory()
    inv_df = inventory.scan_existing()
    gap_df = inventory.generate_gap_analysis()
    paths = inventory.save_reports()

    elapsed = time.time() - start
    print(f"\n[인벤토리 완료] ({elapsed:.1f}초)")
    print(f"  - 발견 파일: {len(inv_df)}개")
    print(f"  - 데이터 타입 평가: {len(gap_df)}개")

    if not gap_df.empty:
        status_counts = gap_df["status"].value_counts()
        for status, count in status_counts.items():
            print(f"  - {status}: {count}개")

    print(f"  - 보고서: {paths['inventory_path']}")
    print(f"  - 갭 분석: {paths['gap_analysis_path']}")


def cmd_collect_p0(config: dict, logger: logging.Logger) -> dict:
    """P0 수집기 실행 (버스 노선/정류장, 버스 운행시간, 지하철 막차, 지하철 환승)."""
    from collectors.subway_last_train import SubwayLastTrainCollector
    from collectors.bus_routes import BusRouteCollector
    from collectors.bus_service_hours import BusServiceHoursCollector
    from collectors.subway_transfer import SubwayTransferCollector

    logger.info("=== P0 데이터 수집 시작 ===")
    start = time.time()
    collection_config = config.get("collection", {})
    results = {}

    # 1. 지하철 막차
    logger.info("--- 지하철 막차 수집 ---")
    try:
        collector = SubwayLastTrainCollector(collection_config)
        df = collector.collect()
        results["subway_last_train"] = {"records": len(df), "status": "OK"}
        print(f"  [OK] 지하철 막차: {len(df)}건")
    except Exception as e:
        results["subway_last_train"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"지하철 막차 수집 실패: {e}", exc_info=True)
        print(f"  [ERROR] 지하철 막차: {e}")

    # 2. 지하철 환승
    logger.info("--- 지하철 환승 수집 ---")
    try:
        collector = SubwayTransferCollector(collection_config)
        df = collector.collect()
        results["subway_transfer"] = {"records": len(df), "status": "OK"}
        print(f"  [OK] 지하철 환승: {len(df)}건")
    except Exception as e:
        results["subway_transfer"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"지하철 환승 수집 실패: {e}", exc_info=True)
        print(f"  [ERROR] 지하철 환승: {e}")

    # 3. 버스 노선/정류장
    logger.info("--- 버스 노선/정류장 수집 ---")
    try:
        collector = BusRouteCollector(collection_config)
        data = collector.collect()
        total = sum(len(df) for df in data.values())
        results["bus_routes"] = {
            "records": total,
            "routes": len(data.get("routes", [])),
            "stops": len(data.get("stops", [])),
            "route_stops": len(data.get("route_stops", [])),
            "status": "OK",
        }
        print(f"  [OK] 버스 노선: {len(data.get('routes', []))}건, "
              f"정류장: {len(data.get('stops', []))}건, "
              f"노선-정류장: {len(data.get('route_stops', []))}건")
    except Exception as e:
        results["bus_routes"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"버스 노선 수집 실패: {e}", exc_info=True)
        print(f"  [ERROR] 버스 노선: {e}")

    # 4. 버스 운행시간
    logger.info("--- 버스 운행시간 수집 ---")
    try:
        collector = BusServiceHoursCollector(collection_config)
        df = collector.collect()
        results["bus_service_hours"] = {"records": len(df), "status": "OK"}
        print(f"  [OK] 버스 운행시간: {len(df)}건")
    except Exception as e:
        results["bus_service_hours"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"버스 운행시간 수집 실패: {e}", exc_info=True)
        print(f"  [ERROR] 버스 운행시간: {e}")

    elapsed = time.time() - start
    print(f"\n[P0 수집 완료] ({elapsed:.1f}초)")
    return results


def cmd_collect_p1(config: dict, logger: logging.Logger) -> dict:
    """P1 수집기 실행 (운행변경 공지, 실시간 버스 검증, 지하철-버스 환승, 사용자 불편사례)."""
    from collectors.service_updates import ServiceUpdatesCollector
    from collectors.realtime_bus import RealtimeBusValidator
    from collectors.subway_bus_transfer import SubwayBusTransferCollector
    from collectors.user_pain_points import UserPainPointsCollector

    logger.info("=== P1 데이터 수집 시작 ===")
    start = time.time()
    collection_config = config.get("collection", {})
    results = {}

    # 1. 운행변경 공지
    logger.info("--- 운행변경 공지 수집 ---")
    try:
        collector = ServiceUpdatesCollector(collection_config)
        df = collector.collect()
        results["service_updates"] = {"records": len(df), "status": "OK"}
        print(f"  [OK] 운행변경 공지: {len(df)}건")
    except Exception as e:
        results["service_updates"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"운행변경 공지 수집 실패: {e}", exc_info=True)
        print(f"  [ERROR] 운행변경 공지: {e}")

    # 2. 실시간 버스 도착 API 검증
    logger.info("--- 실시간 버스 API 검증 ---")
    try:
        validator = RealtimeBusValidator(collection_config)
        result = validator.collect()
        results["realtime_bus"] = {"status": "OK", "result": str(result)}
        print(f"  [OK] 실시간 버스 API 검증 완료")
    except Exception as e:
        results["realtime_bus"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"실시간 버스 검증 실패: {e}", exc_info=True)
        print(f"  [ERROR] 실시간 버스 검증: {e}")

    # 3. 지하철-버스 환승
    logger.info("--- 지하철-버스 환승 수집 ---")
    try:
        collector = SubwayBusTransferCollector(collection_config)
        df = collector.collect()
        results["subway_bus_transfer"] = {"records": len(df), "status": "OK"}
        print(f"  [OK] 지하철-버스 환승: {len(df)}건")
    except Exception as e:
        results["subway_bus_transfer"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"지하철-버스 환승 수집 실패: {e}", exc_info=True)
        print(f"  [ERROR] 지하철-버스 환승: {e}")

    # 4. 사용자 불편사례
    logger.info("--- 사용자 불편사례 수집 ---")
    try:
        collector = UserPainPointsCollector(collection_config)
        df = collector.collect()
        results["user_pain_points"] = {"records": len(df), "status": "OK"}
        print(f"  [OK] 사용자 불편사례: {len(df)}건")
    except Exception as e:
        results["user_pain_points"] = {"records": 0, "status": "ERROR", "error": str(e)}
        logger.error(f"사용자 불편사례 수집 실패: {e}", exc_info=True)
        print(f"  [ERROR] 사용자 불편사례: {e}")

    elapsed = time.time() - start
    print(f"\n[P1 수집 완료] ({elapsed:.1f}초)")
    return results


def cmd_collect_all(config: dict, logger: logging.Logger):
    """P0 + P1 전체 수집."""
    logger.info("=== 전체 데이터 수집 시작 ===")
    p0 = cmd_collect_p0(config, logger)
    p1 = cmd_collect_p1(config, logger)
    return {**p0, **p1}


def cmd_validate(config: dict, logger: logging.Logger):
    """수집된 데이터 품질 검증."""
    from pipeline.quality import DataQualityValidator

    logger.info("=== 데이터 품질 검증 시작 ===")
    start = time.time()

    validator = DataQualityValidator(config)
    result = validator.validate_all()

    elapsed = time.time() - start
    print(f"\n[검증 완료] ({elapsed:.1f}초)")
    print(f"  - 검증 파일: {result['summary']['files_checked']}개")
    print(f"  - 총 행 수: {result['summary']['total_rows']}")
    print(f"  - 유효 행 수: {result['summary']['valid_rows']}")
    print(f"  - 발견 이슈: {result['summary']['total_issues']}건")

    # 파일별 이슈 요약
    for file_result in result["files"]:
        filepath = Path(file_result["filepath"]).name
        issues_count = len(file_result["issues"])
        status = "PASS" if issues_count == 0 else f"ISSUES({issues_count})"
        print(f"  - {filepath}: {file_result['total_rows']}행, {status}")

    # 품질 보고서 생성
    report_path = validator.generate_report()
    print(f"  - 보고서: {report_path}")

    return result


def cmd_report(config: dict, logger: logging.Logger):
    """보고서 생성."""
    from pipeline.report import ReportGenerator

    logger.info("=== 보고서 생성 시작 ===")
    start = time.time()

    generator = ReportGenerator(config)
    paths = generator.generate_all()

    elapsed = time.time() - start
    print(f"\n[보고서 생성 완료] ({elapsed:.1f}초)")
    for name, path in paths.items():
        print(f"  - {name}: {path}")

    return paths


def cmd_run_all(config: dict, logger: logging.Logger):
    """전체 파이프라인 실행: inventory -> collect-p0 -> validate -> report."""
    logger.info("========================================")
    logger.info("전체 파이프라인 실행 시작")
    logger.info("========================================")
    start = time.time()

    print("\n[1/4] 인벤토리 스캔...")
    cmd_inventory(config, logger)

    print("\n[2/4] P0 데이터 수집...")
    cmd_collect_p0(config, logger)

    print("\n[3/4] 데이터 품질 검증...")
    cmd_validate(config, logger)

    print("\n[4/4] 보고서 생성...")
    cmd_report(config, logger)

    elapsed = time.time() - start
    print(f"\n{'=' * 50}")
    print(f"[전체 파이프라인 완료] 총 소요 시간: {elapsed:.1f}초")
    print(f"{'=' * 50}")


def cmd_status(config: dict, logger: logging.Logger):
    """현재 수집 상태 및 데이터 카운트 표시."""
    new_data_dir = Path(config.get("output", {}).get("new_data_dir", "data/new"))
    quarantine_dir = Path(config.get("output", {}).get("quarantine_dir", "data/quarantine"))
    reports_dir = Path(config.get("output", {}).get("reports_dir", "data/reports"))

    print("\n=== 앉을지도 데이터 수집 현황 ===\n")

    # 수집 데이터 현황
    print("[수집 데이터 (data/new/)]")
    if new_data_dir.exists():
        csv_files = sorted(new_data_dir.glob("*.csv"))
        if csv_files:
            total_rows = 0
            for f in csv_files:
                try:
                    import pandas as pd
                    df = pd.read_csv(f)
                    rows = len(df)
                    total_rows += rows
                    size_kb = f.stat().st_size / 1024
                    print(f"  {f.name:40s} {rows:>8,}행  ({size_kb:>8.1f} KB)")
                except Exception:
                    print(f"  {f.name:40s} (읽기 실패)")
            print(f"  {'합계':40s} {total_rows:>8,}행")
        else:
            print("  (수집된 파일 없음)")
    else:
        print("  (디렉토리 없음)")

    # 격리 데이터 현황
    print(f"\n[격리 데이터 (data/quarantine/)]")
    if quarantine_dir.exists():
        q_files = sorted(quarantine_dir.glob("*.csv"))
        if q_files:
            for f in q_files:
                try:
                    import pandas as pd
                    df = pd.read_csv(f)
                    print(f"  {f.name:40s} {len(df):>8,}행")
                except Exception:
                    print(f"  {f.name:40s} (읽기 실패)")
        else:
            print("  (격리된 파일 없음)")
    else:
        print("  (디렉토리 없음)")

    # 보고서 현황
    print(f"\n[보고서 (data/reports/)]")
    if reports_dir.exists():
        report_files = sorted(reports_dir.iterdir())
        if report_files:
            for f in report_files:
                if f.is_file():
                    mod_time = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    print(f"  {f.name:40s} {mod_time}")
        else:
            print("  (보고서 없음)")
    else:
        print("  (디렉토리 없음)")

    # 로그 현황
    logs_dir = Path(config.get("output", {}).get("logs_dir", "logs"))
    if logs_dir.exists():
        log_files = sorted(logs_dir.glob("run_*.log"), reverse=True)
        if log_files:
            latest = log_files[0]
            mod_time = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"\n[최근 실행 로그]")
            print(f"  {latest.name} ({mod_time})")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="앉을지도 교통 데이터 수집 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python main.py inventory      기존 데이터 스캔 및 갭 분석
  python main.py collect-p0     P0 (필수) 데이터 수집
  python main.py collect-p1     P1 (우선) 데이터 수집
  python main.py collect-all    P0 + P1 전체 수집
  python main.py validate       수집 데이터 품질 검증
  python main.py report         보고서 생성
  python main.py run-all        전체 파이프라인 실행
  python main.py status         현재 상태 확인
        """,
    )
    parser.add_argument(
        "command",
        choices=[
            "inventory",
            "collect-p0",
            "collect-p1",
            "collect-all",
            "validate",
            "report",
            "run-all",
            "status",
        ],
        help="실행할 명령",
    )

    args = parser.parse_args()

    # 설정 로드
    config = load_config()

    # 로깅 설정
    logger = setup_logging(config)
    logger.info(f"명령 실행: {args.command}")

    # 명령 실행
    commands = {
        "inventory": cmd_inventory,
        "collect-p0": cmd_collect_p0,
        "collect-p1": cmd_collect_p1,
        "collect-all": cmd_collect_all,
        "validate": cmd_validate,
        "report": cmd_report,
        "run-all": cmd_run_all,
        "status": cmd_status,
    }

    try:
        commands[args.command](config, logger)
    except KeyboardInterrupt:
        print("\n[중단] 사용자에 의해 중단되었습니다.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"실행 중 오류 발생: {e}", exc_info=True)
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
