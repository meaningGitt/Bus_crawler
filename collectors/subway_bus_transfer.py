"""
P1-B: 지하철-버스 환승 정보 수집
- 각 지하철역 주변 버스 정류장 조회 (TAGO getCrdntPrxmtSttnList)
- 주변 정류장 경유 버스 노선 기록
- 환승 검증은 미수행 (근접성 기반)
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

# 주요 지하철역 GPS 좌표 (서울 주요 환승역)
# 실 운용 시에는 전체 역 좌표를 별도 데이터에서 로드해야 함
SUBWAY_STATION_COORDS = [
    {"station": "서울역", "line": "1호선", "lat": 37.5547, "lng": 126.9707},
    {"station": "서울역", "line": "4호선", "lat": 37.5547, "lng": 126.9707},
    {"station": "시청", "line": "1호선", "lat": 37.5641, "lng": 126.9771},
    {"station": "시청", "line": "2호선", "lat": 37.5641, "lng": 126.9771},
    {"station": "종로3가", "line": "1호선", "lat": 37.5713, "lng": 126.9916},
    {"station": "종각", "line": "1호선", "lat": 37.5700, "lng": 126.9828},
    {"station": "을지로입구", "line": "2호선", "lat": 37.5660, "lng": 126.9826},
    {"station": "교대", "line": "2호선", "lat": 37.4934, "lng": 127.0145},
    {"station": "교대", "line": "3호선", "lat": 37.4934, "lng": 127.0145},
    {"station": "강남", "line": "2호선", "lat": 37.4979, "lng": 127.0276},
    {"station": "잠실", "line": "2호선", "lat": 37.5133, "lng": 127.1000},
    {"station": "잠실", "line": "8호선", "lat": 37.5133, "lng": 127.1000},
    {"station": "사당", "line": "2호선", "lat": 37.4765, "lng": 126.9816},
    {"station": "사당", "line": "4호선", "lat": 37.4765, "lng": 126.9816},
    {"station": "건대입구", "line": "2호선", "lat": 37.5403, "lng": 127.0691},
    {"station": "건대입구", "line": "7호선", "lat": 37.5403, "lng": 127.0691},
    {"station": "왕십리", "line": "2호선", "lat": 37.5614, "lng": 127.0380},
    {"station": "합정", "line": "2호선", "lat": 37.5494, "lng": 126.9139},
    {"station": "합정", "line": "6호선", "lat": 37.5494, "lng": 126.9139},
    {"station": "고속터미널", "line": "3호선", "lat": 37.5049, "lng": 127.0049},
    {"station": "고속터미널", "line": "7호선", "lat": 37.5049, "lng": 127.0049},
    {"station": "고속터미널", "line": "9호선", "lat": 37.5049, "lng": 127.0049},
    {"station": "신도림", "line": "1호선", "lat": 37.5088, "lng": 126.8912},
    {"station": "신도림", "line": "2호선", "lat": 37.5088, "lng": 126.8912},
    {"station": "노원", "line": "4호선", "lat": 37.6555, "lng": 127.0616},
    {"station": "노원", "line": "7호선", "lat": 37.6555, "lng": 127.0616},
    {"station": "충무로", "line": "3호선", "lat": 37.5614, "lng": 126.9941},
    {"station": "충무로", "line": "4호선", "lat": 37.5614, "lng": 126.9941},
    {"station": "동대문역사문화공원", "line": "2호선", "lat": 37.5651, "lng": 127.0085},
    {"station": "동대문역사문화공원", "line": "4호선", "lat": 37.5651, "lng": 127.0085},
    {"station": "동대문역사문화공원", "line": "5호선", "lat": 37.5651, "lng": 127.0085},
    {"station": "여의도", "line": "5호선", "lat": 37.5219, "lng": 126.9242},
    {"station": "여의도", "line": "9호선", "lat": 37.5219, "lng": 126.9242},
    {"station": "공덕", "line": "5호선", "lat": 37.5440, "lng": 126.9513},
    {"station": "공덕", "line": "6호선", "lat": 37.5440, "lng": 126.9513},
    {"station": "김포공항", "line": "5호선", "lat": 37.5617, "lng": 126.8014},
    {"station": "김포공항", "line": "9호선", "lat": 37.5617, "lng": 126.8014},
    {"station": "디지털미디어시티", "line": "6호선", "lat": 37.5775, "lng": 126.8999},
    {"station": "홍대입구", "line": "2호선", "lat": 37.5571, "lng": 126.9245},
    {"station": "신촌", "line": "2호선", "lat": 37.5553, "lng": 126.9365},
    {"station": "이수", "line": "4호선", "lat": 37.4855, "lng": 126.9822},
    {"station": "이수", "line": "7호선", "lat": 37.4855, "lng": 126.9822},
]


class SubwayBusTransferCollector:
    """지하철-버스 환승 정보 수집기."""

    NEARBY_STOP_URL = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList"
    ROUTES_AT_STOP_URL = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService/getSttnAcctoRouteList"
    OUTPUT_PATH = Path("data/new/subway_bus_transfers.csv")

    # 반경 (미터)
    SEARCH_RADIUS = 300

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

    def _find_nearby_stops(self, lat: float, lng: float) -> list[dict]:
        """좌표 주변 버스 정류장을 조회.

        Args:
            lat: 위도
            lng: 경도

        Returns:
            주변 정류장 리스트
        """
        params = {
            "gpsLati": lat,
            "gpsLong": lng,
        }

        try:
            items = self.api_client.get_paginated(
                self.NEARBY_STOP_URL,
                params,
                response_format="json",
                page_size=50,
                max_pages=2,
            )
            return items
        except Exception as e:
            logger.warning(f"주변 정류장 조회 실패 (lat={lat}, lng={lng}): {e}")
            return []

    def _get_routes_at_stop(self, city_code: str, node_id: str) -> list[dict]:
        """정류장 경유 노선을 조회.

        Args:
            city_code: 도시 코드
            node_id: 정류장 ID

        Returns:
            경유 노선 리스트
        """
        params = {
            "cityCode": city_code,
            "nodeId": node_id,
        }

        try:
            items = self.api_client.get_paginated(
                self.ROUTES_AT_STOP_URL,
                params,
                response_format="json",
                page_size=50,
                max_pages=2,
            )
            return items
        except Exception as e:
            logger.warning(f"경유 노선 조회 실패 (nodeId={node_id}): {e}")
            return []

    def _calculate_distance_meters(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """두 좌표 간 거리를 미터로 계산 (Haversine)."""
        import math
        R = 6371000  # 지구 반지름 (미터)
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lng2 - lng1)

        a = (math.sin(d_phi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    def collect(self) -> pd.DataFrame:
        """지하철-버스 환승 정보를 수집.

        Returns:
            수집된 환승 정보 DataFrame
        """
        if not self.api_client.data_go_kr_key:
            logger.warning("API 키 미설정. 지하철-버스 환승 수집을 건너뜁니다.")
            return pd.DataFrame()

        logger.info("지하철-버스 환승 정보 수집 시작")
        self.results = []
        now = datetime.now(KST)

        # 중복 방지: 이미 처리한 (station, line) 쌍 추적
        processed = set()

        for station_info in SUBWAY_STATION_COORDS:
            station = station_info["station"]
            line = station_info["line"]
            lat = station_info["lat"]
            lng = station_info["lng"]

            key = (station, line)
            if key in processed:
                continue
            processed.add(key)

            logger.info(f"조회 중: {station} ({line}) - lat={lat}, lng={lng}")

            # 주변 정류장 조회
            nearby_stops = self._find_nearby_stops(lat, lng)

            for stop in nearby_stops:
                stop_id = stop.get("nodeId", stop.get("stationId", ""))
                stop_name = stop.get("nodeNm", stop.get("stationNm", ""))
                stop_lat = float(stop.get("gpsLati", stop.get("gpslati", 0)))
                stop_lng = float(stop.get("gpsLong", stop.get("gpslong", 0)))
                city_code = stop.get("cityCode", "11")

                # 거리 계산
                if stop_lat and stop_lng:
                    distance = self._calculate_distance_meters(lat, lng, stop_lat, stop_lng)
                else:
                    distance = None

                # 반경 필터
                if distance is not None and distance > self.SEARCH_RADIUS:
                    continue

                # 경유 노선 조회
                routes_at_stop = self._get_routes_at_stop(city_code, stop_id)
                route_names = [
                    r.get("routeNo", r.get("routeNm", ""))
                    for r in routes_at_stop if r.get("routeNo") or r.get("routeNm")
                ]
                bus_routes = ", ".join(route_names[:20]) if route_names else ""

                self.results.append({
                    "subway_station": station,
                    "subway_line": line,
                    "bus_stop_id": stop_id,
                    "bus_stop_name": stop_name,
                    "bus_routes": bus_routes,
                    "distance_meters": round(distance, 1) if distance else None,
                    "walking_time_seconds": None,
                    "walking_time_source": "NOT_VERIFIED",
                    "transfer_verified": False,
                    "collected_at": now.isoformat(),
                })

            # 쿼터 확인
            quota = self.api_client.check_remaining_quota()
            if quota["remaining"] < 50:
                logger.warning(f"일일 쿼터 부족 (남은: {quota['remaining']}). 수집 중단.")
                break

        df = pd.DataFrame(self.results)

        if not df.empty:
            self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.OUTPUT_PATH, index=False, encoding="utf-8-sig")
            logger.info(f"환승 정보 저장: {self.OUTPUT_PATH} ({len(df)}건)")
        else:
            logger.warning("수집된 지하철-버스 환승 정보가 없습니다.")

        return df

    def validate(self, df: pd.DataFrame | None = None) -> dict:
        """수집된 데이터의 품질을 검증."""
        if df is None:
            if self.OUTPUT_PATH.exists():
                df = pd.read_csv(self.OUTPUT_PATH)
            else:
                return {"valid": False, "error": "데이터 파일 없음"}

        if df.empty:
            return {"valid": False, "error": "데이터가 비어있음"}

        required_columns = [
            "subway_station", "subway_line", "bus_stop_id", "bus_stop_name",
        ]
        missing_cols = [c for c in required_columns if c not in df.columns]

        issues = []
        if missing_cols:
            issues.append(f"누락 컬럼: {missing_cols}")

        if "subway_station" in df.columns:
            unique_stations = df["subway_station"].nunique()
            if unique_stations < 5:
                issues.append(f"지하철역 수 부족: {unique_stations}개")

        if "distance_meters" in df.columns:
            far_stops = (df["distance_meters"] > self.SEARCH_RADIUS).sum()
            if far_stops > 0:
                issues.append(f"반경 초과 정류장: {far_stops}건")

        if "bus_routes" in df.columns:
            empty_routes = (df["bus_routes"] == "").sum() + df["bus_routes"].isna().sum()
            if empty_routes > len(df) * 0.5:
                issues.append(f"경유 노선 미조회: {empty_routes}/{len(df)}건")

        result = {
            "valid": len(issues) == 0,
            "total_records": len(df),
            "unique_subway_stations": df["subway_station"].nunique() if "subway_station" in df.columns else 0,
            "unique_bus_stops": df["bus_stop_id"].nunique() if "bus_stop_id" in df.columns else 0,
            "issues": issues,
        }
        logger.info(f"검증 결과: {result}")
        return result
