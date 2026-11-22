"""
기존 데이터 인벤토리 모듈
- 기존 subway-data-quality 프로젝트의 데이터 파일 스캔
- MVP 기능에 필요한 데이터 대비 갭 분석
- 인벤토리 및 갭 분석 보고서 생성
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# MVP 기능에 필요한 데이터 타입 정의
REQUIRED_DATA_TYPES = [
    {
        "data_type": "지하철 시간표",
        "description": "1~9호선 역별 열차 시간표",
        "mvp_features": ["A: 출발 시각 역산", "B: 막차 안내", "C: 다음 차량 비교"],
        "search_patterns": ["timetable", "시간표", "schedule", "time_table"],
        "file_patterns": ["*.csv", "*.xlsx", "*.jsonl"],
    },
    {
        "data_type": "지하철 막차",
        "description": "역별 막차 출발 시각 (시간표에서 도출 가능 여부 확인)",
        "mvp_features": ["B: 막차 안내"],
        "search_patterns": ["last_train", "막차", "last", "final"],
        "file_patterns": ["*.csv", "*.json"],
        "can_derive_from": "지하철 시간표",
    },
    {
        "data_type": "지하철 환승 연결",
        "description": "환승역 간 연결 정보 (환승 소요시간, 경로)",
        "mvp_features": ["A: 출발 시각 역산 (환승)", "B: 막차 안내 (환승)"],
        "search_patterns": ["transfer", "환승", "connection"],
        "file_patterns": ["*.csv", "*.json"],
        "note": "환승 수요 통계와 다름 - 연결/소요시간 정보 필요",
    },
    {
        "data_type": "버스 노선/정류장",
        "description": "서울/경기/인천 버스 노선 및 정류장 목록",
        "mvp_features": ["A: 출발 시각 역산 (버스)", "B: 막차 안내 (버스)"],
        "search_patterns": ["bus_route", "bus_stop", "버스노선", "정류장"],
        "file_patterns": ["*.csv"],
    },
    {
        "data_type": "버스 운행시간",
        "description": "노선별 첫차/막차 시각, 배차간격",
        "mvp_features": ["B: 막차 안내 (버스)", "C: 다음 차량 비교 (버스)"],
        "search_patterns": ["service_hours", "운행시간", "첫차", "막차", "bus_time"],
        "file_patterns": ["*.csv"],
    },
    {
        "data_type": "버스 막차",
        "description": "정류장별 버스 막차 시각",
        "mvp_features": ["B: 막차 안내 (버스)"],
        "search_patterns": ["bus_last", "버스막차"],
        "file_patterns": ["*.csv"],
    },
    {
        "data_type": "운행변경 공지",
        "description": "지하철/버스 운행 변경, 임시중단, 우회 공지",
        "mvp_features": ["B: 막차 안내 (변경 반영)"],
        "search_patterns": ["service_update", "notice", "공지", "변경", "우회"],
        "file_patterns": ["*.csv", "*.jsonl"],
    },
    {
        "data_type": "사용자 불편사례",
        "description": "교통 이용 불편사항 (커뮤니티, 민원 등)",
        "mvp_features": ["서비스 기획 검증"],
        "search_patterns": ["pain_point", "complaint", "불편", "민원", "user"],
        "file_patterns": ["*.csv", "*.jsonl", "*.json"],
    },
    {
        "data_type": "실시간 도착정보",
        "description": "버스/지하철 실시간 도착 예정 정보",
        "mvp_features": ["C: 다음 차량 비교"],
        "search_patterns": ["realtime", "arrival", "실시간", "도착"],
        "file_patterns": ["*.csv", "*.json"],
    },
    {
        "data_type": "지하철-버스 환승",
        "description": "지하철역 주변 버스 정류장 및 환승 경로",
        "mvp_features": ["A: 출발 시각 역산 (환승)", "B: 막차 안내 (환승)"],
        "search_patterns": ["subway_bus", "지하철_버스", "multimodal", "연계환승"],
        "file_patterns": ["*.csv", "*.json"],
    },
]


class DataInventory:
    """기존 데이터 인벤토리 관리."""

    def __init__(self, existing_project_path: str = "~/subway-data-quality"):
        """
        Args:
            existing_project_path: 기존 프로젝트 경로
        """
        self.existing_path = Path(os.path.expanduser(existing_project_path))
        self.new_data_path = Path("data/new")
        self.reports_dir = Path("data/reports")
        self._inventory_df: pd.DataFrame | None = None
        self._gap_df: pd.DataFrame | None = None

    def _count_rows(self, filepath: Path) -> int | None:
        """파일의 행 수를 반환."""
        try:
            suffix = filepath.suffix.lower()
            if suffix == ".csv":
                df = pd.read_csv(filepath, nrows=0)
                # 빠른 행 수 카운트
                with open(filepath, "r", encoding="utf-8-sig") as f:
                    return sum(1 for _ in f) - 1  # 헤더 제외
            elif suffix == ".xlsx":
                df = pd.read_excel(filepath, engine="openpyxl")
                return len(df)
            elif suffix in (".json", ".jsonl"):
                with open(filepath, "r", encoding="utf-8") as f:
                    if suffix == ".jsonl":
                        return sum(1 for line in f if line.strip())
                    else:
                        import json
                        data = json.load(f)
                        if isinstance(data, list):
                            return len(data)
                        return 1
        except Exception as e:
            logger.debug(f"행 수 카운트 실패 ({filepath}): {e}")
        return None

    def _get_columns(self, filepath: Path) -> list[str]:
        """파일의 컬럼 목록을 반환."""
        try:
            suffix = filepath.suffix.lower()
            if suffix == ".csv":
                df = pd.read_csv(filepath, nrows=0)
                return df.columns.tolist()
            elif suffix == ".xlsx":
                df = pd.read_excel(filepath, nrows=0, engine="openpyxl")
                return df.columns.tolist()
        except Exception:
            pass
        return []

    def _get_date_range(self, filepath: Path) -> str:
        """파일 내 날짜 범위를 추정."""
        try:
            suffix = filepath.suffix.lower()
            if suffix not in (".csv", ".xlsx"):
                return ""

            if suffix == ".csv":
                df = pd.read_csv(filepath, nrows=100)
            else:
                df = pd.read_excel(filepath, nrows=100, engine="openpyxl")

            # 날짜 관련 컬럼 찾기
            date_cols = [c for c in df.columns if any(
                kw in c.lower() for kw in ["date", "time", "날짜", "일자", "collected", "created"]
            )]

            for col in date_cols:
                try:
                    dates = pd.to_datetime(df[col], errors="coerce").dropna()
                    if not dates.empty:
                        return f"{dates.min().strftime('%Y-%m-%d')} ~ {dates.max().strftime('%Y-%m-%d')}"
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    def scan_existing(self) -> pd.DataFrame:
        """기존 프로젝트의 데이터 파일을 스캔하여 인벤토리 DataFrame 반환.

        Returns:
            인벤토리 DataFrame (filename, path, size_bytes, row_count, columns, date_range, last_modified)
        """
        inventory_rows = []
        search_dirs = []

        # 기존 프로젝트 데이터 디렉토리
        if self.existing_path.exists():
            data_dir = self.existing_path / "data"
            if data_dir.exists():
                search_dirs.append(data_dir)
            else:
                search_dirs.append(self.existing_path)
        else:
            logger.warning(f"기존 프로젝트 경로를 찾을 수 없음: {self.existing_path}")

        # 현재 프로젝트 data/new
        if self.new_data_path.exists():
            search_dirs.append(self.new_data_path)

        extensions = {".csv", ".jsonl", ".json", ".xlsx"}

        for search_dir in search_dirs:
            for filepath in sorted(search_dir.rglob("*")):
                if filepath.is_file() and filepath.suffix.lower() in extensions:
                    try:
                        stat = filepath.stat()
                        inventory_rows.append({
                            "filename": filepath.name,
                            "path": str(filepath),
                            "size_bytes": stat.st_size,
                            "row_count": self._count_rows(filepath),
                            "columns": str(self._get_columns(filepath)),
                            "date_range": self._get_date_range(filepath),
                            "last_modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            "source_project": "subway-data-quality" if str(self.existing_path) in str(filepath) else "bus-crawler",
                        })
                    except Exception as e:
                        logger.warning(f"파일 스캔 실패 ({filepath}): {e}")

        self._inventory_df = pd.DataFrame(inventory_rows)
        logger.info(f"인벤토리 스캔 완료: {len(inventory_rows)}개 파일")
        return self._inventory_df

    def _match_data_type(self, filename: str, columns: list[str], search_patterns: list[str]) -> bool:
        """파일이 특정 데이터 타입에 해당하는지 판별."""
        filename_lower = filename.lower()
        for pattern in search_patterns:
            if pattern.lower() in filename_lower:
                return True
        # 컬럼명으로도 매칭 시도
        columns_str = " ".join(columns).lower()
        for pattern in search_patterns:
            if pattern.lower() in columns_str:
                return True
        return False

    def generate_gap_analysis(self) -> pd.DataFrame:
        """기존 데이터 대비 필요 데이터 갭 분석.

        Returns:
            갭 분석 DataFrame
        """
        if self._inventory_df is None:
            self.scan_existing()

        gap_rows = []

        for req in REQUIRED_DATA_TYPES:
            data_type = req["data_type"]
            status = "MISSING"
            existing_files = []
            missing_detail = ""
            notes = ""

            # 인벤토리에서 매칭 파일 검색
            if self._inventory_df is not None and not self._inventory_df.empty:
                for _, row in self._inventory_df.iterrows():
                    columns = []
                    try:
                        col_str = row.get("columns", "[]")
                        if isinstance(col_str, str) and col_str.startswith("["):
                            import ast
                            columns = ast.literal_eval(col_str)
                    except Exception:
                        pass

                    if self._match_data_type(
                        row["filename"], columns, req["search_patterns"]
                    ):
                        existing_files.append(row["filename"])

            if existing_files:
                # 파일이 존재하면 상세 상태 판별
                status = "AVAILABLE"

                # 날짜 확인하여 오래된 데이터인지 체크
                for _, row in self._inventory_df.iterrows():
                    if row["filename"] in existing_files:
                        date_range = row.get("date_range", "")
                        last_mod = row.get("last_modified", "")
                        if last_mod:
                            try:
                                mod_date = datetime.strptime(last_mod, "%Y-%m-%d %H:%M:%S")
                                days_old = (datetime.now() - mod_date).days
                                if days_old > 180:
                                    status = "OUTDATED"
                                    notes = f"마지막 수정: {days_old}일 전"
                            except Exception:
                                pass

                # 도출 가능 여부 확인
                if "can_derive_from" in req:
                    notes += f" ('{req['can_derive_from']}'에서 도출 가능)"

            else:
                # 파일이 없는 경우
                if "can_derive_from" in req:
                    # 원본 데이터가 있는지 확인
                    source_type = req["can_derive_from"]
                    source_found = False
                    for other_req in REQUIRED_DATA_TYPES:
                        if other_req["data_type"] == source_type:
                            if self._inventory_df is not None and not self._inventory_df.empty:
                                for _, row in self._inventory_df.iterrows():
                                    cols = []
                                    try:
                                        import ast
                                        cols = ast.literal_eval(row.get("columns", "[]"))
                                    except Exception:
                                        pass
                                    if self._match_data_type(
                                        row["filename"], cols, other_req["search_patterns"]
                                    ):
                                        source_found = True
                                        break
                    if source_found:
                        status = "PARTIAL"
                        notes = f"'{req['can_derive_from']}'에서 도출 가능하나 별도 파일 없음"
                    else:
                        missing_detail = "원본 데이터와 도출 데이터 모두 없음"

                if data_type == "실시간 도착정보":
                    status = "NOT_VERIFIED"
                    notes = "API 가용성 검증 필요"

                if not missing_detail:
                    missing_detail = "해당 데이터 파일을 찾을 수 없음"

            gap_rows.append({
                "data_type": data_type,
                "description": req["description"],
                "status": status,
                "existing_files": ", ".join(existing_files) if existing_files else "",
                "missing_detail": missing_detail,
                "mvp_features": ", ".join(req["mvp_features"]),
                "notes": notes.strip() if isinstance(notes, str) else str(notes),
            })

        self._gap_df = pd.DataFrame(gap_rows)
        logger.info(f"갭 분석 완료: {len(gap_rows)}개 데이터 타입 평가")

        # 상태별 요약 로그
        if not self._gap_df.empty:
            status_counts = self._gap_df["status"].value_counts().to_dict()
            logger.info(f"갭 분석 요약: {status_counts}")

        return self._gap_df

    def save_reports(self):
        """인벤토리 및 갭 분석 보고서를 data/reports/에 저장."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # 인벤토리가 아직 없으면 스캔
        if self._inventory_df is None:
            self.scan_existing()

        # 갭 분석이 아직 없으면 실행
        if self._gap_df is None:
            self.generate_gap_analysis()

        # 인벤토리 저장
        inventory_path = self.reports_dir / "data_inventory.csv"
        if self._inventory_df is not None and not self._inventory_df.empty:
            self._inventory_df.to_csv(inventory_path, index=False, encoding="utf-8-sig")
            logger.info(f"인벤토리 저장: {inventory_path}")
        else:
            # 빈 인벤토리라도 저장
            pd.DataFrame(columns=[
                "filename", "path", "size_bytes", "row_count",
                "columns", "date_range", "last_modified", "source_project",
            ]).to_csv(inventory_path, index=False, encoding="utf-8-sig")
            logger.info(f"빈 인벤토리 저장: {inventory_path}")

        # 갭 분석 저장
        gap_path = self.reports_dir / "gap_analysis.csv"
        if self._gap_df is not None:
            self._gap_df.to_csv(gap_path, index=False, encoding="utf-8-sig")
            logger.info(f"갭 분석 저장: {gap_path}")

        return {
            "inventory_path": str(inventory_path),
            "gap_analysis_path": str(gap_path),
        }
