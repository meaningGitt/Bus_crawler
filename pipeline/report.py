"""
보고서 생성 모듈
- 데이터 소스 레지스트리
- 수집 요약 (collection_summary.json)
- MVP 기능별 데이터 확보 가능성 평가 (mvp_data_feasibility.md)
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# 데이터 소스 레지스트리 정보
DATA_SOURCES = [
    {
        "provider": "국토교통부 (TAGO)",
        "data_name": "전국 버스노선정보",
        "official_url": "https://www.data.go.kr/data/15098529/openapi.do",
        "scope": "전국 (서울 cityCode=11)",
        "collection_method": "REST API (JSON)",
        "update_frequency": "수시",
        "auth_required": True,
        "rate_limit": "일 10,000건",
        "license": "공공누리 1유형",
        "collectible": True,
    },
    {
        "provider": "국토교통부 (TAGO)",
        "data_name": "전국 버스정류장정보",
        "official_url": "https://www.data.go.kr/data/15098534/openapi.do",
        "scope": "전국",
        "collection_method": "REST API (JSON)",
        "update_frequency": "수시",
        "auth_required": True,
        "rate_limit": "일 10,000건",
        "license": "공공누리 1유형",
        "collectible": True,
    },
    {
        "provider": "국토교통부 (TAGO)",
        "data_name": "전국 버스도착정보",
        "official_url": "https://www.data.go.kr/data/15098530/openapi.do",
        "scope": "전국",
        "collection_method": "REST API (JSON)",
        "update_frequency": "실시간",
        "auth_required": True,
        "rate_limit": "일 10,000건",
        "license": "공공누리 1유형",
        "collectible": True,
    },
    {
        "provider": "서울교통공사",
        "data_name": "열차시간표",
        "official_url": "https://www.data.go.kr/data/15071311/openapi.do",
        "scope": "서울 1~9호선",
        "collection_method": "REST API (JSON)",
        "update_frequency": "반기",
        "auth_required": True,
        "rate_limit": "일 10,000건",
        "license": "공공누리 1유형",
        "collectible": True,
    },
    {
        "provider": "경기도",
        "data_name": "버스노선정보",
        "official_url": "https://www.data.go.kr/data/15080666/openapi.do",
        "scope": "경기도",
        "collection_method": "REST API (JSON)",
        "update_frequency": "수시",
        "auth_required": True,
        "rate_limit": "일 10,000건",
        "license": "공공누리 1유형",
        "collectible": True,
    },
    {
        "provider": "인천광역시",
        "data_name": "버스노선정보",
        "official_url": "https://www.data.go.kr/data/15058852/openapi.do",
        "scope": "인천",
        "collection_method": "REST API (XML)",
        "update_frequency": "수시",
        "auth_required": True,
        "rate_limit": "일 10,000건",
        "license": "공공누리 1유형",
        "collectible": True,
    },
    {
        "provider": "서울시",
        "data_name": "버스운행정보 (ws.bus.go.kr)",
        "official_url": "http://ws.bus.go.kr/",
        "scope": "서울",
        "collection_method": "REST API (JSON)",
        "update_frequency": "실시간",
        "auth_required": True,
        "rate_limit": "엔드포인트당 1,000건/일",
        "license": "서울시 공공데이터",
        "collectible": True,
    },
    {
        "provider": "서울열린데이터광장",
        "data_name": "지하철 환승정보",
        "official_url": "https://data.seoul.go.kr/dataList/OA-22521/F/1/datasetView.do",
        "scope": "서울 도시철도",
        "collection_method": "CSV 다운로드",
        "update_frequency": "비정기",
        "auth_required": False,
        "rate_limit": "없음",
        "license": "서울시 공공데이터",
        "collectible": True,
    },
    {
        "provider": "서울교통공사",
        "data_name": "실시간 지하철 도착정보",
        "official_url": "http://data.seoul.go.kr/dataList/OA-12764/F/1/datasetView.do",
        "scope": "서울 도시철도",
        "collection_method": "REST API (JSON)",
        "update_frequency": "실시간",
        "auth_required": True,
        "rate_limit": "일 1,000건",
        "license": "서울시 공공데이터",
        "collectible": True,
    },
    {
        "provider": "서울교통공사/커뮤니티",
        "data_name": "운행변경 공지",
        "official_url": "https://www.seoulmetro.co.kr/",
        "scope": "서울 도시철도/버스",
        "collection_method": "웹 스크래핑",
        "update_frequency": "수시",
        "auth_required": False,
        "rate_limit": "없음 (예의상 1초 간격)",
        "license": "공지사항 (저작권 주의)",
        "collectible": True,
    },
]


class ReportGenerator:
    """보고서 생성기."""

    def __init__(self, config: dict):
        """
        Args:
            config: 전체 설정 딕셔너리 (config.yaml 로드 결과)
        """
        self.config = config
        output_config = config.get("output", {})
        self.new_data_dir = Path(output_config.get("new_data_dir", "data/new"))
        self.reports_dir = Path(output_config.get("reports_dir", "data/reports"))
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_source_registry(self) -> pd.DataFrame:
        """데이터 소스 레지스트리를 생성.

        Returns:
            소스 레지스트리 DataFrame
        """
        rows = []
        now = datetime.now().strftime("%Y-%m-%d")

        for source in DATA_SOURCES:
            rows.append({
                "provider": source["provider"],
                "data_name": source["data_name"],
                "official_url": source["official_url"],
                "scope": source["scope"],
                "collection_method": source["collection_method"],
                "update_frequency": source["update_frequency"],
                "auth_required": source["auth_required"],
                "rate_limit": source["rate_limit"],
                "license": source["license"],
                "last_checked": now,
                "collectible": source["collectible"],
            })

        df = pd.DataFrame(rows)
        path = self.reports_dir / "source_registry.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info(f"소스 레지스트리 생성: {path} ({len(df)}개 소스)")
        return df

    def generate_collection_summary(self) -> dict:
        """수집 요약 정보를 생성하여 JSON으로 저장.

        Returns:
            수집 요약 딕셔너리
        """
        summary = {
            "generated_at": datetime.now().isoformat(),
            "collectors": {},
        }

        # 수집된 파일별 통계
        file_stats = {
            "subway_last_train": {
                "file": "subway_last_train_supplement.csv",
                "collector": "SubwayLastTrainCollector",
            },
            "bus_routes": {
                "file": "bus_routes.csv",
                "collector": "BusRouteCollector",
            },
            "bus_stops": {
                "file": "bus_stops.csv",
                "collector": "BusRouteCollector",
            },
            "bus_route_stops": {
                "file": "bus_route_stops.csv",
                "collector": "BusRouteCollector",
            },
            "bus_service_hours": {
                "file": "bus_service_hours.csv",
                "collector": "BusServiceHoursCollector",
            },
            "subway_transfer": {
                "file": "subway_transfer_connections.csv",
                "collector": "SubwayTransferCollector",
            },
        }

        # 품질 보고서에서 검증 결과 로드
        quality_report_path = self.reports_dir / "data_quality_report.csv"
        quality_data = {}
        if quality_report_path.exists():
            try:
                qdf = pd.read_csv(quality_report_path)
                for data_type in qdf["data_type"].unique():
                    type_rows = qdf[qdf["data_type"] == data_type]
                    quality_data[data_type] = {
                        "total_rows": int(type_rows["total_rows"].iloc[0]) if "total_rows" in type_rows.columns else 0,
                        "valid_rows": int(type_rows["valid_rows"].iloc[0]) if "valid_rows" in type_rows.columns else 0,
                        "issue_count": int(type_rows["issue_count"].sum()) if "issue_count" in type_rows.columns else 0,
                    }
            except Exception as e:
                logger.warning(f"품질 보고서 로드 실패: {e}")

        for data_type, info in file_stats.items():
            filepath = self.new_data_dir / info["file"]
            collector_stats = {
                "collector": info["collector"],
                "file": info["file"],
                "records_collected": 0,
                "records_valid": 0,
                "records_quarantined": 0,
                "file_exists": filepath.exists(),
                "file_size_bytes": 0,
                "errors": [],
            }

            if filepath.exists():
                try:
                    df = pd.read_csv(filepath)
                    collector_stats["records_collected"] = len(df)
                    collector_stats["file_size_bytes"] = filepath.stat().st_size

                    # 품질 검증 결과 반영
                    if data_type in quality_data:
                        q = quality_data[data_type]
                        collector_stats["records_valid"] = q["valid_rows"]
                        collector_stats["records_quarantined"] = q["total_rows"] - q["valid_rows"]
                    else:
                        collector_stats["records_valid"] = len(df)
                except Exception as e:
                    collector_stats["errors"].append(str(e))

            # 격리 파일 확인
            quarantine_dir = Path("data/quarantine")
            if quarantine_dir.exists():
                quarantine_files = list(quarantine_dir.glob(f"{Path(info['file']).stem}_quarantine_*.csv"))
                for qf in quarantine_files:
                    try:
                        qdf = pd.read_csv(qf)
                        collector_stats["records_quarantined"] += len(qdf)
                    except Exception:
                        pass

            summary["collectors"][data_type] = collector_stats

        # JSON 저장
        summary_path = self.reports_dir / "collection_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        logger.info(f"수집 요약 생성: {summary_path}")
        return summary

    def generate_mvp_feasibility(self) -> str:
        """MVP 기능별 데이터 확보 가능성 평가 보고서를 생성.

        Returns:
            생성된 보고서 파일 경로
        """
        lines = []
        lines.append("# MVP 데이터 확보 가능성 평가")
        lines.append("")
        lines.append(f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # 수집된 파일 현황 확인
        collected_files = {}
        if self.new_data_dir.exists():
            for f in self.new_data_dir.iterdir():
                if f.is_file() and f.suffix == ".csv":
                    try:
                        df = pd.read_csv(f)
                        collected_files[f.stem] = len(df)
                    except Exception:
                        collected_files[f.stem] = 0

        # 갭 분석 결과 로드
        gap_path = self.reports_dir / "gap_analysis.csv"
        gap_data = {}
        if gap_path.exists():
            try:
                gap_df = pd.read_csv(gap_path)
                for _, row in gap_df.iterrows():
                    gap_data[row["data_type"]] = row["status"]
            except Exception:
                pass

        def _assess(data_types_needed: list[str], collected: dict, gaps: dict) -> tuple[str, str]:
            """필요한 데이터의 확보 상태를 평가."""
            statuses = []
            for dt in data_types_needed:
                if dt in gaps:
                    statuses.append(gaps[dt])
                else:
                    statuses.append("NOT_VERIFIED")

            if all(s == "AVAILABLE" for s in statuses):
                return "PASS", "모든 필요 데이터 확보 완료"
            elif any(s == "AVAILABLE" for s in statuses):
                missing = [dt for dt, s in zip(data_types_needed, statuses) if s != "AVAILABLE"]
                return "PARTIAL", f"일부 데이터 미확보: {', '.join(missing)}"
            elif all(s == "NOT_VERIFIED" for s in statuses):
                return "NOT_VERIFIED", "데이터 확보 여부 미확인"
            else:
                missing = [dt for dt, s in zip(data_types_needed, statuses) if s in ("MISSING", "NOT_VERIFIED")]
                return "FAIL", f"필수 데이터 미확보: {', '.join(missing)}"

        # Feature A: 출발 시각 역산
        lines.append("## Feature A: 출발 시각 역산")
        lines.append("")

        scenarios_a = [
            ("A-1: 지하철 단독", ["지하철 시간표"]),
            ("A-2: 버스 단독", ["버스 노선/정류장", "버스 운행시간"]),
            ("A-3: 지하철+버스 환승", ["지하철 시간표", "버스 노선/정류장", "버스 운행시간", "지하철-버스 환승"]),
        ]

        for name, deps in scenarios_a:
            status, reason = _assess(deps, collected_files, gap_data)
            lines.append(f"### {name}")
            lines.append(f"- 판정: **{status}**")
            lines.append(f"- 필요 데이터: {', '.join(deps)}")
            lines.append(f"- 근거: {reason}")
            lines.append("")

        # Feature B: 막차 안내
        lines.append("## Feature B: 막차 안내")
        lines.append("")

        scenarios_b = [
            ("B-1: 지하철 단일 노선", ["지하철 시간표", "지하철 막차"]),
            ("B-2: 지하철 환승", ["지하철 시간표", "지하철 막차", "지하철 환승 연결"]),
            ("B-3: 버스", ["버스 노선/정류장", "버스 운행시간", "버스 막차"]),
            ("B-4: 지하철+버스", ["지하철 막차", "버스 막차", "지하철-버스 환승"]),
        ]

        for name, deps in scenarios_b:
            status, reason = _assess(deps, collected_files, gap_data)
            lines.append(f"### {name}")
            lines.append(f"- 판정: **{status}**")
            lines.append(f"- 필요 데이터: {', '.join(deps)}")
            lines.append(f"- 근거: {reason}")
            lines.append("")

        # Feature C: 다음 차량 비교
        lines.append("## Feature C: 다음 차량 비교")
        lines.append("")

        scenarios_c = [
            ("C-1: 지하철", ["지하철 시간표"]),
            ("C-2: 버스", ["버스 운행시간", "실시간 도착정보"]),
        ]

        for name, deps in scenarios_c:
            status, reason = _assess(deps, collected_files, gap_data)
            lines.append(f"### {name}")
            lines.append(f"- 판정: **{status}**")
            lines.append(f"- 필요 데이터: {', '.join(deps)}")
            lines.append(f"- 근거: {reason}")
            lines.append("")

        # 종합 요약
        lines.append("## 종합 요약")
        lines.append("")
        lines.append("| Feature | Scenario | 판정 |")
        lines.append("|---------|----------|------|")

        all_scenarios = [
            ("A: 출발 시각 역산", scenarios_a),
            ("B: 막차 안내", scenarios_b),
            ("C: 다음 차량 비교", scenarios_c),
        ]

        for feature_name, scenarios in all_scenarios:
            for name, deps in scenarios:
                status, _ = _assess(deps, collected_files, gap_data)
                lines.append(f"| {feature_name} | {name} | {status} |")

        lines.append("")
        lines.append("---")
        lines.append(f"*이 보고서는 자동 생성되었습니다. ({datetime.now().isoformat()})*")

        content = "\n".join(lines)
        path = self.reports_dir / "mvp_data_feasibility.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"MVP 가능성 평가 생성: {path}")
        return str(path)

    def generate_all(self):
        """모든 보고서를 생성.

        Returns:
            생성된 보고서 경로 딕셔너리
        """
        logger.info("전체 보고서 생성 시작")

        source_registry = self.generate_source_registry()
        collection_summary = self.generate_collection_summary()
        feasibility_path = self.generate_mvp_feasibility()

        result = {
            "source_registry": str(self.reports_dir / "source_registry.csv"),
            "collection_summary": str(self.reports_dir / "collection_summary.json"),
            "mvp_feasibility": feasibility_path,
        }

        logger.info(f"전체 보고서 생성 완료: {list(result.keys())}")
        return result
