"""
서울 도시철도 환승 연결 정보 수집
- 서울 열린데이터 API (SearchInfoBySubwayNameService)로 환승역 자동 감지
- 같은 역명이 여러 호선에 존재하면 환승역으로 판별
- 환승 소요시간은 공식 CSV 또는 하드코딩 데이터 사용
"""

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml

from collectors.api_client import ApiClient

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 서울 지하철 주요 환승역 정보 (대체 데이터)
KNOWN_TRANSFER_STATIONS = [
    # (역명, 출발노선, 도착노선, 환승방향 설명)
    ("서울역", "1호선", "4호선", "1호선→4호선"),
    ("서울역", "4호선", "1호선", "4호선→1호선"),
    ("시청", "1호선", "2호선", "1호선→2호선"),
    ("시청", "2호선", "1호선", "2호선→1호선"),
    ("종로3가", "1호선", "3호선", "1호선→3호선"),
    ("종로3가", "3호선", "1호선", "3호선→1호선"),
    ("종로3가", "1호선", "5호선", "1호선→5호선"),
    ("종로3가", "5호선", "1호선", "5호선→1호선"),
    ("종로3가", "3호선", "5호선", "3호선→5호선"),
    ("종로3가", "5호선", "3호선", "5호선→3호선"),
    ("동대문역사문화공원", "2호선", "4호선", "2호선→4호선"),
    ("동대문역사문화공원", "4호선", "2호선", "4호선→2호선"),
    ("동대문역사문화공원", "2호선", "5호선", "2호선→5호선"),
    ("동대문역사문화공원", "5호선", "2호선", "5호선→2호선"),
    ("동대문역사문화공원", "4호선", "5호선", "4호선→5호선"),
    ("동대문역사문화공원", "5호선", "4호선", "5호선→4호선"),
    ("충무로", "3호선", "4호선", "3호선→4호선"),
    ("충무로", "4호선", "3호선", "4호선→3호선"),
    ("을지로3가", "2호선", "3호선", "2호선→3호선"),
    ("을지로3가", "3호선", "2호선", "3호선→2호선"),
    ("교대", "2호선", "3호선", "2호선→3호선"),
    ("교대", "3호선", "2호선", "3호선→2호선"),
    ("사당", "2호선", "4호선", "2호선→4호선"),
    ("사당", "4호선", "2호선", "4호선→2호선"),
    ("잠실", "2호선", "8호선", "2호선→8호선"),
    ("잠실", "8호선", "2호선", "8호선→2호선"),
    ("건대입구", "2호선", "7호선", "2호선→7호선"),
    ("건대입구", "7호선", "2호선", "7호선→2호선"),
    ("왕십리", "2호선", "5호선", "2호선→5호선"),
    ("왕십리", "5호선", "2호선", "5호선→2호선"),
    ("합정", "2호선", "6호선", "2호선→6호선"),
    ("합정", "6호선", "2호선", "6호선→2호선"),
    ("고속터미널", "3호선", "7호선", "3호선→7호선"),
    ("고속터미널", "7호선", "3호선", "7호선→3호선"),
    ("고속터미널", "3호선", "9호선", "3호선→9호선"),
    ("고속터미널", "9호선", "3호선", "9호선→3호선"),
    ("고속터미널", "7호선", "9호선", "7호선→9호선"),
    ("고속터미널", "9호선", "7호선", "9호선→7호선"),
    ("노원", "4호선", "7호선", "4호선→7호선"),
    ("노원", "7호선", "4호선", "7호선→4호선"),
    ("창동", "1호선", "4호선", "1호선→4호선"),
    ("창동", "4호선", "1호선", "4호선→1호선"),
    ("신도림", "1호선", "2호선", "1호선→2호선"),
    ("신도림", "2호선", "1호선", "2호선→1호선"),
    ("영등포구청", "2호선", "5호선", "2호선→5호선"),
    ("영등포구청", "5호선", "2호선", "5호선→2호선"),
    ("까치산", "2호선", "5호선", "2호선→5호선"),
    ("까치산", "5호선", "2호선", "5호선→2호선"),
    ("약수", "3호선", "6호선", "3호선→6호선"),
    ("약수", "6호선", "3호선", "6호선→3호선"),
    ("연신내", "3호선", "6호선", "3호선→6호선"),
    ("연신내", "6호선", "3호선", "6호선→3호선"),
    ("공덕", "5호선", "6호선", "5호선→6호선"),
    ("공덕", "6호선", "5호선", "6호선→5호선"),
    ("이수", "4호선", "7호선", "4호선→7호선"),
    ("이수", "7호선", "4호선", "7호선→4호선"),
    ("석계", "1호선", "6호선", "1호선→6호선"),
    ("석계", "6호선", "1호선", "6호선→1호선"),
    ("태릉입구", "6호선", "7호선", "6호선→7호선"),
    ("태릉입구", "7호선", "6호선", "7호선→6호선"),
    ("천호", "5호선", "8호선", "5호선→8호선"),
    ("천호", "8호선", "5호선", "8호선→5호선"),
    ("김포공항", "5호선", "9호선", "5호선→9호선"),
    ("김포공항", "9호선", "5호선", "9호선→5호선"),
    ("당산", "2호선", "9호선", "2호선→9호선"),
    ("당산", "9호선", "2호선", "9호선→2호선"),
    ("선정릉", "9호선", "분당선", "9호선→분당선"),
    ("선정릉", "분당선", "9호선", "분당선→9호선"),
    ("여의도", "5호선", "9호선", "5호선→9호선"),
    ("여의도", "9호선", "5호선", "9호선→5호선"),
    ("회기", "1호선", "경의중앙선", "1호선→경의중앙선"),
    ("회기", "경의중앙선", "1호선", "경의중앙선→1호선"),
    ("디지털미디어시티", "6호선", "경의중앙선", "6호선→경의중앙선"),
    ("디지털미디어시티", "경의중앙선", "6호선", "경의중앙선→6호선"),
]

# 서울 열린데이터 API 호선명 -> 표준 호선명 매핑
API_LINE_NAME_TO_STANDARD = {
    "01호선": "1호선",
    "02호선": "2호선",
    "03호선": "3호선",
    "04호선": "4호선",
    "05호선": "5호선",
    "06호선": "6호선",
    "07호선": "7호선",
    "08호선": "8호선",
    "09호선": "9호선",
    "1호선": "1호선",
    "2호선": "2호선",
    "3호선": "3호선",
    "4호선": "4호선",
    "5호선": "5호선",
    "6호선": "6호선",
    "7호선": "7호선",
    "8호선": "8호선",
    "9호선": "9호선",
}


class SubwayTransferCollector:
    """지하철 환승 정보 수집기 (서울 열린데이터 API + 하드코딩 대체)."""

    OPEN_DATA_PAGE_URL = "https://data.seoul.go.kr/dataList/OA-22521/F/1/datasetView.do"
    OUTPUT_PATH = Path("data/new/subway_transfer_connections.csv")

    # 서울 열린데이터 API 서비스명
    STATION_INFO_SERVICE = "SearchInfoBySubwayNameService"

    def __init__(self, config: dict | None = None):
        if config is None:
            config_path = Path("config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    full_config = yaml.safe_load(f)
                config = full_config.get("collection", {})
            else:
                config = {}

        self.api_client = ApiClient(config)
        self.results: list[dict] = []

    def _detect_transfer_stations_from_api(self) -> list[dict] | None:
        """서울 열린데이터 API로 환승역을 자동 감지.

        SearchInfoBySubwayNameService에서 모든 역을 조회한 후,
        같은 역명이 여러 호선에 존재하면 환승역으로 판별.

        Returns:
            환승정보 리스트 또는 감지 실패 시 None
        """
        logger.info("서울 열린데이터 API로 환승역 자동 감지 시도")

        try:
            rows = self.api_client.get_seoul_api_all(
                self.STATION_INFO_SERVICE,
                max_rows=5000,
            )
        except Exception as e:
            logger.warning(f"역 정보 API 조회 실패: {e}")
            return None

        if not rows:
            logger.warning("역 정보 API에서 데이터를 받지 못함")
            return None

        if self.api_client._is_sample_key and len(rows) <= 5:
            logger.warning(
                "sample 키 사용으로 5건만 조회됨. "
                "환승역 자동 감지 불가, 하드코딩 데이터 사용."
            )
            return None

        # 역명 -> 호선 목록 매핑
        station_lines: dict[str, set[str]] = {}
        for row in rows:
            station_nm = row.get("STATION_NM", "").strip()
            line_num = row.get("LINE_NUM", "").strip()
            if station_nm and line_num:
                std_line = API_LINE_NAME_TO_STANDARD.get(line_num, line_num)
                if station_nm not in station_lines:
                    station_lines[station_nm] = set()
                station_lines[station_nm].add(std_line)

        # 2개 이상 호선이 있는 역 = 환승역
        transfer_stations = {
            name: sorted(lines)
            for name, lines in station_lines.items()
            if len(lines) >= 2
        }

        if not transfer_stations:
            logger.warning("API 데이터에서 환승역을 감지하지 못함")
            return None

        logger.info(f"API에서 {len(transfer_stations)}개 환승역 감지")

        # 환승 정보 생성 (양방향)
        now = datetime.now(KST)
        results = []
        station_id_counter = {}

        for station_name, lines in sorted(transfer_stations.items()):
            if station_name not in station_id_counter:
                station_id_counter[station_name] = len(station_id_counter) + 1

            transfer_id = f"TRF_{station_id_counter[station_name]:04d}"

            # 모든 호선 쌍에 대해 양방향 환승 생성
            for i, from_line in enumerate(lines):
                for j, to_line in enumerate(lines):
                    if i == j:
                        continue
                    results.append({
                        "transfer_station_id": transfer_id,
                        "transfer_station_name": station_name,
                        "from_line": from_line,
                        "to_line": to_line,
                        "transfer_direction": f"{from_line}→{to_line}",
                        "transfer_time_seconds": None,  # API에서 환승 시간 제공 안함
                        "transfer_path_info": "",
                        "source": "seoul_openapi_auto_detect",
                        "collected_at": now.isoformat(),
                    })

        return results

    def _try_download_official_csv(self) -> pd.DataFrame | None:
        """서울 열린데이터에서 환승정보 CSV를 다운로드 시도.

        Returns:
            성공 시 DataFrame, 실패 시 None
        """
        logger.info("서울 열린데이터 환승정보 CSV 다운로드 시도")

        # 열린데이터 직접 다운로드 URL 패턴 시도
        possible_urls = [
            "https://data.seoul.go.kr/dataList/OA-22521/F/1/datasetView.do",
        ]

        for url in possible_urls:
            try:
                response = requests.get(url, timeout=30, headers={
                    "User-Agent": "AnjeulJido-DataCollector/1.0"
                })
                if response.status_code == 200:
                    content_type = response.headers.get("Content-Type", "")
                    # CSV 파일인 경우 직접 파싱
                    if "text/csv" in content_type or "application/octet-stream" in content_type:
                        from io import StringIO
                        df = pd.read_csv(StringIO(response.text), encoding="utf-8")
                        logger.info(f"환승정보 CSV 다운로드 성공: {len(df)}건")
                        return df
                    else:
                        logger.info(f"응답이 CSV가 아님 (Content-Type: {content_type}). "
                                    f"해당 URL은 웹페이지일 수 있음.")
            except Exception as e:
                logger.warning(f"CSV 다운로드 실패 ({url}): {e}")

        logger.info("공식 CSV 다운로드 실패.")
        return None

    def _build_from_known_stations(self) -> list[dict]:
        """하드코딩된 환승역 데이터로 환승정보 구성.

        Returns:
            환승정보 딕셔너리 리스트
        """
        now = datetime.now(KST)
        results = []

        # 환승역 고유 ID 생성
        station_id_counter = {}

        for station_name, from_line, to_line, direction in KNOWN_TRANSFER_STATIONS:
            if station_name not in station_id_counter:
                station_id_counter[station_name] = len(station_id_counter) + 1

            transfer_id = f"TRF_{station_id_counter[station_name]:04d}"

            results.append({
                "transfer_station_id": transfer_id,
                "transfer_station_name": station_name,
                "from_line": from_line,
                "to_line": to_line,
                "transfer_direction": direction,
                "transfer_time_seconds": None,  # 공식 소스 없음
                "transfer_path_info": "",
                "source": "hardcoded_known_stations",
                "collected_at": now.isoformat(),
            })

        return results

    def _convert_official_csv(self, df: pd.DataFrame) -> list[dict]:
        """공식 CSV 데이터를 표준 형식으로 변환.

        Args:
            df: 공식 CSV DataFrame

        Returns:
            변환된 환승정보 리스트
        """
        now = datetime.now(KST)
        results = []

        # 공식 CSV 컬럼 매핑 (실제 CSV 구조에 따라 조정 필요)
        col_mapping = {
            "환승역명": "transfer_station_name",
            "출발노선": "from_line",
            "도착노선": "to_line",
            "환승소요시간": "transfer_time_seconds",
        }

        for idx, row in df.iterrows():
            record = {
                "transfer_station_id": f"TRF_{idx + 1:04d}",
                "transfer_station_name": "",
                "from_line": "",
                "to_line": "",
                "transfer_direction": "",
                "transfer_time_seconds": None,
                "transfer_path_info": "",
                "source": "seoul_open_data_OA-22521",
                "collected_at": now.isoformat(),
            }

            for csv_col, our_col in col_mapping.items():
                if csv_col in df.columns:
                    value = row[csv_col]
                    if pd.notna(value):
                        record[our_col] = value

            # 환승방향 자동 생성
            if record["from_line"] and record["to_line"]:
                record["transfer_direction"] = f"{record['from_line']}→{record['to_line']}"

            results.append(record)

        return results

    def collect(self) -> pd.DataFrame:
        """환승 정보를 수집.

        수집 우선순위:
        1. 공식 CSV 다운로드 시도
        2. 서울 열린데이터 API로 환승역 자동 감지
        3. 하드코딩 데이터 사용

        Returns:
            수집된 환승 정보 DataFrame
        """
        logger.info("지하철 환승 정보 수집 시작")

        # 1. 공식 CSV 다운로드 시도
        official_df = self._try_download_official_csv()

        if official_df is not None and not official_df.empty:
            self.results = self._convert_official_csv(official_df)
            logger.info(f"공식 CSV에서 {len(self.results)}건 환승정보 로드")
        else:
            # 2. 서울 열린데이터 API로 환승역 자동 감지
            api_results = self._detect_transfer_stations_from_api()

            if api_results:
                self.results = api_results
                logger.info(f"API 자동 감지에서 {len(self.results)}건 환승정보 생성")
            else:
                # 3. 하드코딩 데이터 사용
                self.results = self._build_from_known_stations()
                logger.info(f"하드코딩 데이터에서 {len(self.results)}건 환승정보 로드")

        df = pd.DataFrame(self.results)

        if not df.empty:
            self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.OUTPUT_PATH, index=False, encoding="utf-8-sig")
            logger.info(f"환승 정보 저장 완료: {self.OUTPUT_PATH} ({len(df)}건)")
        else:
            logger.warning("수집된 환승 정보가 없습니다.")

        return df

    def validate(self, df: pd.DataFrame | None = None) -> dict:
        """수집된 데이터의 품질을 검증.

        Returns:
            검증 결과 딕셔너리
        """
        if df is None:
            if self.OUTPUT_PATH.exists():
                df = pd.read_csv(self.OUTPUT_PATH)
            else:
                return {"valid": False, "error": "데이터 파일 없음"}

        if df.empty:
            return {"valid": False, "error": "데이터가 비어있음"}

        required_columns = [
            "transfer_station_id", "transfer_station_name",
            "from_line", "to_line", "transfer_direction",
        ]
        missing_cols = [c for c in required_columns if c not in df.columns]

        issues = []
        if missing_cols:
            issues.append(f"누락 컬럼: {missing_cols}")

        if "transfer_station_name" in df.columns:
            unique_stations = df["transfer_station_name"].nunique()
            if unique_stations < 10:
                issues.append(f"환승역 수 부족: {unique_stations}개 (최소 10개 이상 기대)")

        # 양방향 환승 쌍 확인
        if all(c in df.columns for c in ["from_line", "to_line", "transfer_station_name"]):
            pairs = set()
            for _, row in df.iterrows():
                pairs.add((row["transfer_station_name"], row["from_line"], row["to_line"]))

            unpaired = 0
            for station, from_l, to_l in pairs:
                if (station, to_l, from_l) not in pairs:
                    unpaired += 1
            if unpaired > 0:
                issues.append(f"양방향 환승 미등록: {unpaired}건")

        # 소스 정보
        sources = df["source"].unique().tolist() if "source" in df.columns else []

        result = {
            "valid": len(issues) == 0,
            "total_records": len(df),
            "unique_stations": df["transfer_station_name"].nunique() if "transfer_station_name" in df.columns else 0,
            "sources": sources,
            "issues": issues,
        }
        logger.info(f"검증 결과: {result}")
        return result
