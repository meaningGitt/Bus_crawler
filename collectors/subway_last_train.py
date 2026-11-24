"""
서울 열린데이터(openapi.seoul.go.kr) API를 이용한 지하철 막차 정보 수집
- SearchSTNTimeTableByIDService: 역별 열차시간표
- SearchInfoBySubwayNameService: 역 정보 조회 (역코드 확보용)
- 1~9호선, 상행/하행(2호선: 내선/외선), 평일/토요일/공휴일
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

# 요일 구분 (서울 열린데이터 API 코드)
# week_tag: 1=평일, 2=토요일, 3=공휴일
DAY_TYPES = {
    "평일": "1",
    "주말": "2",    # 토요일
    "공휴일": "3",
}

# 방향 -> inout_tag 매핑
# inout_tag: 1=상행/내선, 2=하행/외선
DIRECTION_TO_INOUT = {
    "상행": "1",
    "하행": "2",
    "내선": "1",
    "외선": "2",
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

# 호선명 -> LINE_NUM 매핑 (API에서 사용하는 호선명)
LINE_NAME_MAP = {
    "1호선": "01호선",
    "2호선": "02호선",
    "3호선": "03호선",
    "4호선": "04호선",
    "5호선": "05호선",
    "6호선": "06호선",
    "7호선": "07호선",
    "8호선": "08호선",
    "9호선": "09호선",
}

# 호선별 역코드 범위 (대략적 범위, 없는 코드는 API가 빈 응답 반환)
LINE_STATION_CODE_RANGES = {
    "1호선": (150, 160),
    "2호선": (201, 250),
    "3호선": (309, 342),
    "4호선": (409, 434),
    "5호선": (509, 556),
    "6호선": (609, 648),
    "7호선": (709, 753),
    "8호선": (810, 825),
    "9호선": (901, 938),
}


class SubwayLastTrainCollector:
    """지하철 막차 정보 수집기 (서울 열린데이터 API 사용)."""

    OUTPUT_PATH = Path("data/new/subway_last_train_supplement.csv")

    # 서울 열린데이터 API 서비스명
    TIMETABLE_SERVICE = "SearchSTNTimeTableByIDService"
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
        self._station_cache: dict[str, list[dict]] = {}  # line -> station list

    def _get_station_codes_for_line(self, line: str) -> list[dict]:
        """특정 호선의 역 코드 목록을 조회.

        SearchInfoBySubwayNameService로 역 정보를 조회하여
        해당 호선의 모든 역 코드를 확보.

        Args:
            line: 호선명 (예: "1호선")

        Returns:
            역 정보 딕셔너리 리스트 [{station_cd, station_nm, fr_code, line_num}, ...]
        """
        if line in self._station_cache:
            return self._station_cache[line]

        line_api_name = LINE_NAME_MAP.get(line, line)
        logger.info(f"{line} 역 목록 조회 중 (SearchInfoBySubwayNameService)...")

        try:
            rows = self.api_client.get_seoul_api_all(
                self.STATION_INFO_SERVICE,
                max_rows=1000,
            )
        except Exception as e:
            logger.error(f"{line} 역 목록 조회 실패: {e}")
            # 대안: 코드 범위로 생성
            return self._generate_station_codes_from_range(line)

        # 해당 호선만 필터링
        stations = []
        for row in rows:
            row_line = row.get("LINE_NUM", "")
            if row_line == line_api_name or row_line == line:
                stations.append({
                    "station_cd": row.get("STATION_CD", ""),
                    "station_nm": row.get("STATION_NM", ""),
                    "fr_code": row.get("FR_CODE", ""),
                    "line_num": row_line,
                })

        if stations:
            logger.info(f"{line}: {len(stations)}개 역 발견")
        else:
            logger.warning(f"{line}: SearchInfoBySubwayNameService에서 역을 찾지 못함. 코드 범위로 대체.")
            stations = self._generate_station_codes_from_range(line)

        self._station_cache[line] = stations
        return stations

    def _generate_station_codes_from_range(self, line: str) -> list[dict]:
        """호선별 역코드 범위로 역 목록 생성 (대체 방법).

        Args:
            line: 호선명

        Returns:
            역 정보 딕셔너리 리스트
        """
        code_range = LINE_STATION_CODE_RANGES.get(line)
        if not code_range:
            logger.warning(f"{line}: 코드 범위 정의 없음")
            return []

        start, end = code_range
        stations = []
        for code in range(start, end + 1):
            stations.append({
                "station_cd": f"{code:04d}",
                "station_nm": "",
                "fr_code": f"{code:04d}",
                "line_num": LINE_NAME_MAP.get(line, line),
            })

        logger.info(f"{line}: 코드 범위 {start}-{end}에서 {len(stations)}개 역코드 생성")
        return stations

    def _fetch_timetable(self, line: str, direction: str, day_type_code: str) -> list[dict]:
        """특정 호선/방향/요일의 열차시간표를 조회.

        서울 열린데이터 API SearchSTNTimeTableByIDService를 사용.
        각 역별로 시간표를 조회하여 모든 역의 데이터를 수집.

        Args:
            line: 호선명 (예: "1호선")
            direction: 방향 (예: "상행", "하행", "내선", "외선")
            day_type_code: 요일 코드 ("1"=평일, "2"=토요일, "3"=공휴일)

        Returns:
            시간표 아이템 리스트
        """
        stations = self._get_station_codes_for_line(line)
        if not stations:
            logger.warning(f"{line}: 역 목록이 비어있어 시간표 조회 불가")
            return []

        inout_tag = DIRECTION_TO_INOUT.get(direction, "1")
        all_items = []
        errors = 0
        empty_count = 0

        for station in stations:
            station_cd = station["station_cd"]
            if not station_cd:
                continue

            try:
                # SearchSTNTimeTableByIDService/{start}/{end}/{station_code}/{week_tag}/{inout_tag}
                rows = self.api_client.get_seoul_api_all(
                    self.TIMETABLE_SERVICE,
                    station_cd, day_type_code, inout_tag,
                    max_rows=500,
                )

                if rows:
                    # 역 이름이 비어있으면 응답에서 채우기
                    if not station["station_nm"] and rows:
                        station["station_nm"] = rows[0].get("STATION_NM", "")

                    for row in rows:
                        row["_station_cd_queried"] = station_cd
                    all_items.extend(rows)
                else:
                    empty_count += 1

            except Exception as e:
                errors += 1
                if errors <= 3:
                    logger.warning(f"{line} {direction} 역코드 {station_cd} 시간표 조회 실패: {e}")
                elif errors == 4:
                    logger.warning(f"{line} {direction}: 추가 오류는 로그 생략...")

        logger.info(
            f"{line} {direction} (요일코드 {day_type_code}): "
            f"{len(all_items)}건 조회 (역 {len(stations)}개 중 "
            f"빈 응답 {empty_count}개, 오류 {errors}개)"
        )
        return all_items

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
            # 서울 열린데이터 API 필드명
            station_code = item.get("STATION_CD", item.get("_station_cd_queried",
                           item.get("stnCode", item.get("statnId", ""))))
            station_name = item.get("STATION_NM", item.get("stnNm",
                            item.get("statnNm", "")))
            # LEFTTIME = 출발시각, ARRIVETIME = 도착시각
            departure_time = item.get("LEFTTIME", item.get("trainDptreTm",
                             item.get("dptTm", "")))
            destination = item.get("SUBWAYENAME", item.get("DESTSTATION",
                          item.get("arvlStnNm", item.get("tmnStnNm", ""))))

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

        # 서울 열린데이터 API 필드 또는 기존 data.go.kr 필드
        required_fields_new = {"LEFTTIME", "STATION_CD", "SUBWAYENAME"}
        required_fields_old = {"trainDptreTm", "stnCode", "arvlStnNm"}
        sample = timetable_data[0] if timetable_data else {}
        keys = set(sample.keys())
        return required_fields_new.issubset(keys) or required_fields_old.issubset(keys)

    def collect(self) -> pd.DataFrame:
        """전체 막차 정보를 수집.

        Returns:
            수집된 막차 정보 DataFrame
        """
        # 서울 열린데이터 API는 별도 키 체계 사용 (sample 키도 동작)
        logger.info("지하철 막차 정보 수집 시작 (서울 열린데이터 API)")

        if self.api_client._is_sample_key:
            logger.warning(
                "sample 키 사용 중: 호출당 5건 제한으로 완전한 데이터 수집이 불가합니다. "
                "SEOUL_DATA_API_KEY를 설정하세요."
            )

        self.results = []

        for line, directions in LINE_DIRECTIONS.items():
            for direction in directions:
                for day_type, day_code in DAY_TYPES.items():
                    if day_type == "공휴일":
                        # 공휴일은 주말과 유사하므로 별도 수집 불필요 시 스킵
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
