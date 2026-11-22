"""
서울교통공사 열차시간표 API를 이용한 지하철 막차 정보 수집
- 1~9호선, 상행/하행(2호선: 내선/외선), 평일/주말/공휴일
- 역별 마지막 출발 시각, 종착역, 중간 종착 여부 기록
"""

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from collectors.api_client import ApiClient

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 호선별 방향 정의
LINE_DIRECTIONS = {
    "1호선": ["상행", "하행"],
    "2호선": ["내선", "외선"],
    "3호선": ["상행", "하행"],
    "4호선": ["상행", "하행"],
    "5호선": ["상행", "하행"],
    "6호선": ["상행", "하행"],
    "7호선": ["상행", "하행"],
    "8호선": ["상행", "하행"],
    "9호선": ["상행", "하행"],
}

# 요일 구분
DAY_TYPES = {
    "평일": "8",    # 평일 코드
    "주말": "9",    # 토요일/일요일
    "공휴일": "9",  # 공휴일도 주말과 동일 취급 (API에 따라 다를 수 있음)
}

# 호선별 종착역 (막차 중간종착 판별용)
LINE_TERMINAL_STATIONS = {
    "1호선": {"상행": ["소요산", "양주", "의정부", "광운대", "청량리"],
              "하행": ["인천", "신창", "서동탄", "병점", "천안"]},
    "2호선": {"내선": ["성수", "신도림", "까치산"],
              "외선": ["성수", "신도림", "까치산"]},
    "3호선": {"상행": ["대화"], "하행": ["오금"]},
    "4호선": {"상행": ["당고개", "노원"], "하행": ["오이도", "안산"]},
    "5호선": {"상행": ["방화"], "하행": ["마천", "하남검단산"]},
    "6호선": {"상행": ["응암순환", "응암"], "하행": ["신내", "봉화산"]},
    "7호선": {"상행": ["장암", "도봉산"], "하행": ["청라국제도시"]},
    "8호선": {"상행": ["암사"], "하행": ["모란"]},
    "9호선": {"상행": ["개화"], "하행": ["중앙보훈병원", "VHS의료원"]},
}


class SubwayLastTrainCollector:
    """지하철 막차 정보 수집기."""

    BASE_URL = "https://apis.data.go.kr/B553766/schedule/schedule"
    OUTPUT_PATH = Path("data/new/subway_last_train_supplement.csv")

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

    def _fetch_timetable(self, line: str, direction: str, day_type_code: str) -> list[dict]:
        """특정 호선/방향/요일의 열차시간표를 조회.

        Args:
            line: 호선명 (예: "1호선")
            direction: 방향 (예: "상행", "하행", "내선", "외선")
            day_type_code: 요일 코드 ("8"=평일, "9"=주말)

        Returns:
            시간표 아이템 리스트
        """
        params = {
            "lineNm": line,
            "upbdnbSe": direction,
            "wkndSe": day_type_code,
            "numOfRows": 1000,
            "dataType": "JSON",
        }

        try:
            items = self.api_client.get_paginated(
                self.BASE_URL,
                params,
                response_format="json",
                page_size=1000,
                max_pages=10,
            )
            logger.info(f"{line} {direction} (요일코드 {day_type_code}): {len(items)}건 조회")
            return items
        except Exception as e:
            logger.error(f"{line} {direction} 시간표 조회 실패: {e}")
            return []

    def _extract_last_trains(
        self, items: list[dict], line: str, direction: str, day_type: str
    ) -> list[dict]:
        """시간표 데이터에서 역별 막차 정보를 추출.

        Args:
            items: 시간표 아이템 리스트
            line: 호선명
            direction: 방향
            day_type: 요일 구분 ("평일", "주말", "공휴일")

        Returns:
            역별 막차 정보 딕셔너리 리스트
        """
        if not items:
            return []

        # 역별로 그룹화하여 가장 늦은 출발 시각 찾기
        station_last: dict[str, dict] = {}

        for item in items:
            station_code = item.get("stnCode", item.get("statnId", ""))
            station_name = item.get("stnNm", item.get("statnNm", ""))
            departure_time = item.get("trainDptreTm", item.get("dptTm", ""))
            destination = item.get("arvlStnNm", item.get("tmnStnNm", ""))

            if not station_code or not departure_time:
                continue

            # 시각 비교 (HH:MM:SS 형식)
            # 자정 넘김 처리: 00:xx 는 24:xx 로 변환하여 비교
            compare_time = departure_time
            if departure_time.startswith("00:"):
                compare_time = "24:" + departure_time[3:]

            key = station_code
            if key not in station_last or compare_time > station_last[key]["_compare_time"]:
                station_last[key] = {
                    "station_code": station_code,
                    "station_name": station_name,
                    "departure_time": departure_time,
                    "destination": destination,
                    "_compare_time": compare_time,
                }

        # 중간종착 판별
        terminal_stations = LINE_TERMINAL_STATIONS.get(line, {}).get(direction, [])

        now = datetime.now(KST)
        results = []
        for info in station_last.values():
            dest = info["destination"]
            is_mid_terminus = False
            if terminal_stations and dest:
                is_mid_terminus = dest not in terminal_stations

            results.append({
                "line": line,
                "station_code": info["station_code"],
                "station_name": info["station_name"],
                "direction": direction,
                "day_type": day_type,
                "last_train_time": info["departure_time"],
                "destination": dest,
                "is_mid_terminus": is_mid_terminus,
                "timetable_valid_date": now.strftime("%Y-%m-%d"),
                "collected_at": now.isoformat(),
            })

        return results

    def can_derive_from_timetable(self, timetable_data: list[dict]) -> bool:
        """기존 시간표 데이터에서 막차 정보를 도출할 수 있는지 확인.

        Args:
            timetable_data: 기존 시간표 데이터

        Returns:
            도출 가능 여부
        """
        if not timetable_data:
            return False

        required_fields = {"trainDptreTm", "stnCode", "arvlStnNm"}
        sample = timetable_data[0] if timetable_data else {}
        return required_fields.issubset(set(sample.keys()))

    def collect(self) -> pd.DataFrame:
        """전체 막차 정보를 수집.

        Returns:
            수집된 막차 정보 DataFrame
        """
        if not self.api_client.data_go_kr_key:
            logger.warning("API 키 미설정. 막차 정보 수집을 건너뜁니다.")
            return pd.DataFrame()

        logger.info("지하철 막차 정보 수집 시작")
        self.results = []

        for line, directions in LINE_DIRECTIONS.items():
            for direction in directions:
                for day_type, day_code in DAY_TYPES.items():
                    if day_type == "공휴일":
                        # 공휴일은 주말과 동일 코드이므로 별도 수집 불필요 시 스킵
                        continue

                    logger.info(f"수집 중: {line} {direction} {day_type}")
                    items = self._fetch_timetable(line, direction, day_code)
                    last_trains = self._extract_last_trains(items, line, direction, day_type)
                    self.results.extend(last_trains)

        df = pd.DataFrame(self.results)

        if not df.empty:
            self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.OUTPUT_PATH, index=False, encoding="utf-8-sig")
            logger.info(f"막차 정보 저장 완료: {self.OUTPUT_PATH} ({len(df)}건)")
        else:
            logger.warning("수집된 막차 정보가 없습니다.")

        return df

    def validate(self, df: pd.DataFrame | None = None) -> dict:
        """수집된 데이터의 품질을 검증.

        Args:
            df: 검증할 DataFrame. None이면 저장된 파일에서 로드.

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
            "line", "station_code", "station_name", "direction",
            "day_type", "last_train_time", "destination",
        ]
        missing_cols = [c for c in required_columns if c not in df.columns]

        issues = []
        if missing_cols:
            issues.append(f"누락 컬럼: {missing_cols}")

        # 호선별 데이터 존재 여부
        if "line" in df.columns:
            collected_lines = set(df["line"].unique())
            expected_lines = set(LINE_DIRECTIONS.keys())
            missing_lines = expected_lines - collected_lines
            if missing_lines:
                issues.append(f"미수집 호선: {missing_lines}")

        # 막차 시간 유효성 (보통 22:00 ~ 01:00 사이)
        if "last_train_time" in df.columns:
            null_times = df["last_train_time"].isna().sum()
            if null_times > 0:
                issues.append(f"막차 시간 누락: {null_times}건")

        # 중간종착 비율
        if "is_mid_terminus" in df.columns:
            mid_ratio = df["is_mid_terminus"].mean()
            if mid_ratio > 0.5:
                issues.append(f"중간종착 비율 과다: {mid_ratio:.1%}")

        result = {
            "valid": len(issues) == 0,
            "total_records": len(df),
            "lines_covered": sorted(df["line"].unique().tolist()) if "line" in df.columns else [],
            "issues": issues,
        }
        logger.info(f"검증 결과: {result}")
        return result
