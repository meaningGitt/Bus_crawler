"""
서울/경기/인천 버스 노선 및 정류장 정보 수집
- 서울: TAGO API (cityCode=11)
- 경기: 경기도 버스노선 API (6410000)
- 인천: 인천 버스노선 API (6280000, XML)
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

# 지역별 API 설정
REGION_CONFIG = {
    "서울": {
        "code": "SEL",
        "city_code": "11",
        "base_url": "https://apis.data.go.kr/1613000/BusRouteInfoInqireService",
        "endpoints": {
            "route_list": "/getRouteNoList",
            "route_info": "/getRouteInfoIem",
            "route_stops": "/getRouteAcctoThrghSttnList",
        },
        "format": "json",
        "id_field": "routeId",
    },
    "경기": {
        "code": "GGI",
        "base_url": "https://apis.data.go.kr/6410000/busrouteservice/v2",
        "endpoints": {
            "route_list": "/getBusRouteListv2",
            "route_info": "/getBusRouteInfoItemv2",
            "route_stops": "/getRouteStationListv2",
        },
        "format": "json",
        "id_field": "routeId",
    },
    "인천": {
        "code": "ICN",
        "base_url": "https://apis.data.go.kr/6280000/busRouteService",
        "endpoints": {
            "route_list": "/getBusRouteList",
            "route_info": "/getBusRouteInfoItem",
            "route_stops": "/getBusRouteStationList",
        },
        "format": "xml",
        "id_field": "routeId",
    },
}

# 버스 유형 코드 매핑
BUS_TYPE_NAMES = {
    "1": "일반",
    "2": "좌석",
    "3": "마을",
    "4": "직행좌석",
    "5": "공항",
    "6": "간선급행",
    "11": "간선",
    "12": "지선",
    "13": "순환",
    "14": "광역",
    "15": "급행",
    "16": "관광",
    "21": "농어촌",
    "22": "경기순환",
    "26": "급행간선",
}


class BusRouteCollector:
    """버스 노선 및 정류장 정보 수집기."""

    OUTPUT_DIR = Path("data/new")

    def __init__(self, config: dict | None = None, regions: list[str] | None = None):
        """
        Args:
            config: 수집 설정 딕셔너리
            regions: 수집할 지역 리스트 (기본: 전체)
        """
        if config is None:
            config_path = Path("config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    full_config = yaml.safe_load(f)
                config = full_config.get("collection", {})
            else:
                config = {}

        self.api_client = ApiClient(config)
        self.regions = regions or list(REGION_CONFIG.keys())

        self.routes_data: list[dict] = []
        self.stops_data: list[dict] = []
        self.route_stops_data: list[dict] = []

    def _make_unified_id(self, region_code: str, original_id: str) -> str:
        """통합 ID 생성."""
        return f"{region_code}_{original_id}"

    # ---- 서울 (TAGO API) ----

    def _collect_seoul_routes(self) -> list[dict]:
        """서울 버스 노선 목록 수집 (TAGO API)."""
        cfg = REGION_CONFIG["서울"]
        url = cfg["base_url"] + cfg["endpoints"]["route_list"]

        params = {"cityCode": cfg["city_code"]}
        items = self.api_client.get_paginated(url, params, response_format="json", page_size=100)
        logger.info(f"서울 노선 목록: {len(items)}건")
        return items

    def _collect_seoul_route_info(self, route_id: str) -> dict:
        """서울 노선 상세정보 조회."""
        cfg = REGION_CONFIG["서울"]
        url = cfg["base_url"] + cfg["endpoints"]["route_info"]
        params = {"cityCode": cfg["city_code"], "routeId": route_id}

        try:
            data = self.api_client.get(url, params, response_format="json")
            items = self.api_client._extract_items(data, "json")
            return items[0] if items else {}
        except Exception as e:
            logger.warning(f"서울 노선 상세 조회 실패 (routeId={route_id}): {e}")
            return {}

    def _collect_seoul_route_stops(self, route_id: str) -> list[dict]:
        """서울 노선 정류장 목록 조회."""
        cfg = REGION_CONFIG["서울"]
        url = cfg["base_url"] + cfg["endpoints"]["route_stops"]
        params = {"cityCode": cfg["city_code"], "routeId": route_id}

        return self.api_client.get_paginated(
            url, params, response_format="json", page_size=200, max_pages=5
        )

    # ---- 경기 ----

    def _collect_gyeonggi_routes(self) -> list[dict]:
        """경기도 버스 노선 목록 수집."""
        cfg = REGION_CONFIG["경기"]
        url = cfg["base_url"] + cfg["endpoints"]["route_list"]

        # 경기도 API는 keyword 파라미터 필요. 빈 값으로 전체 조회 시도
        params = {}
        items = self.api_client.get_paginated(url, params, response_format="json", page_size=100)
        logger.info(f"경기 노선 목록: {len(items)}건")
        return items

    def _collect_gyeonggi_route_info(self, route_id: str) -> dict:
        """경기도 노선 상세정보 조회."""
        cfg = REGION_CONFIG["경기"]
        url = cfg["base_url"] + cfg["endpoints"]["route_info"]
        params = {"routeId": route_id}

        try:
            data = self.api_client.get(url, params, response_format="json")
            items = self.api_client._extract_items(data, "json")
            return items[0] if items else {}
        except Exception as e:
            logger.warning(f"경기 노선 상세 조회 실패 (routeId={route_id}): {e}")
            return {}

    def _collect_gyeonggi_route_stops(self, route_id: str) -> list[dict]:
        """경기도 노선 정류장 목록 조회."""
        cfg = REGION_CONFIG["경기"]
        url = cfg["base_url"] + cfg["endpoints"]["route_stops"]
        params = {"routeId": route_id}

        return self.api_client.get_paginated(
            url, params, response_format="json", page_size=200, max_pages=5
        )

    # ---- 인천 (XML) ----

    def _collect_incheon_routes(self) -> list[dict]:
        """인천 버스 노선 목록 수집."""
        cfg = REGION_CONFIG["인천"]
        url = cfg["base_url"] + cfg["endpoints"]["route_list"]

        params = {}
        items = self.api_client.get_paginated(url, params, response_format="xml", page_size=100)
        logger.info(f"인천 노선 목록: {len(items)}건")
        return items

    def _collect_incheon_route_info(self, route_id: str) -> dict:
        """인천 노선 상세정보 조회."""
        cfg = REGION_CONFIG["인천"]
        url = cfg["base_url"] + cfg["endpoints"]["route_info"]
        params = {"routeId": route_id}

        try:
            data = self.api_client.get(url, params, response_format="xml")
            items = data.get("_items", [])
            return items[0] if items else {}
        except Exception as e:
            logger.warning(f"인천 노선 상세 조회 실패 (routeId={route_id}): {e}")
            return {}

    def _collect_incheon_route_stops(self, route_id: str) -> list[dict]:
        """인천 노선 정류장 목록 조회."""
        cfg = REGION_CONFIG["인천"]
        url = cfg["base_url"] + cfg["endpoints"]["route_stops"]
        params = {"routeId": route_id}

        return self.api_client.get_paginated(
            url, params, response_format="xml", page_size=200, max_pages=5
        )

    # ---- 통합 수집 ----

    def _process_region(self, region: str):
        """특정 지역의 노선/정류장 데이터 수집 및 가공."""
        cfg = REGION_CONFIG[region]
        region_code = cfg["code"]
        now = datetime.now(KST)

        logger.info(f"=== {region} 버스 데이터 수집 시작 ===")

        # 1. 노선 목록 수집
        if region == "서울":
            raw_routes = self._collect_seoul_routes()
        elif region == "경기":
            raw_routes = self._collect_gyeonggi_routes()
        elif region == "인천":
            raw_routes = self._collect_incheon_routes()
        else:
            logger.error(f"지원하지 않는 지역: {region}")
            return

        logger.info(f"{region}: {len(raw_routes)}개 노선 수집됨")

        seen_stops: dict[str, dict] = {}  # stop_id -> stop_info

        for idx, route_raw in enumerate(raw_routes):
            route_id = str(route_raw.get(cfg["id_field"], route_raw.get("routeId", "")))
            route_name = route_raw.get("routeNo", route_raw.get("routeName",
                                       route_raw.get("routeNm", "")))

            if not route_id:
                continue

            unified_route_id = self._make_unified_id(region_code, route_id)

            # 2. 노선 상세정보
            if region == "서울":
                info = self._collect_seoul_route_info(route_id)
            elif region == "경기":
                info = self._collect_gyeonggi_route_info(route_id)
            else:
                info = self._collect_incheon_route_info(route_id)

            route_type_code = str(info.get("routeTypeCd", info.get("routeTp",
                                  route_raw.get("routeTypeCd", ""))))
            route_type = BUS_TYPE_NAMES.get(route_type_code, route_type_code)

            self.routes_data.append({
                "region": region,
                "route_id": unified_route_id,
                "route_name": route_name,
                "route_type": route_type,
                "operator": info.get("corpNm", info.get("companyNm", "")),
                "start_stop": info.get("startStaNm", info.get("stStaNm",
                              route_raw.get("startNodeNm", ""))),
                "end_stop": info.get("endStaNm", info.get("edStaNm",
                            route_raw.get("endNodeNm", ""))),
                "data_date": now.strftime("%Y-%m-%d"),
            })

            # 3. 노선 정류장 목록
            if region == "서울":
                stops = self._collect_seoul_route_stops(route_id)
            elif region == "경기":
                stops = self._collect_gyeonggi_route_stops(route_id)
            else:
                stops = self._collect_incheon_route_stops(route_id)

            for seq_idx, stop_raw in enumerate(stops):
                stop_id = str(stop_raw.get("nodeId", stop_raw.get("stationId",
                              stop_raw.get("bsId", ""))))
                stop_name = stop_raw.get("nodeNm", stop_raw.get("stationName",
                             stop_raw.get("staNm", "")))
                lat = stop_raw.get("gpsLati", stop_raw.get("y", stop_raw.get("latitude", "")))
                lng = stop_raw.get("gpsLong", stop_raw.get("x", stop_raw.get("longitude", "")))
                stop_seq = stop_raw.get("nodeOrd", stop_raw.get("stationSeq",
                           stop_raw.get("seq", seq_idx + 1)))
                direction = stop_raw.get("upDownCd", stop_raw.get("direction", ""))

                unified_stop_id = self._make_unified_id(region_code, stop_id)

                # 정류장 정보 (중복 제거)
                if stop_id and stop_id not in seen_stops:
                    seen_stops[stop_id] = {
                        "region": region,
                        "stop_id": unified_stop_id,
                        "stop_name": stop_name,
                        "latitude": lat,
                        "longitude": lng,
                        "original_id": stop_id,
                        "unified_id": unified_stop_id,
                    }

                # 노선-정류장 매핑
                self.route_stops_data.append({
                    "region": region,
                    "route_id": unified_route_id,
                    "stop_id": unified_stop_id,
                    "stop_name": stop_name,
                    "stop_sequence": stop_seq,
                    "direction": direction,
                    "latitude": lat,
                    "longitude": lng,
                })

            if (idx + 1) % 50 == 0:
                logger.info(f"{region}: {idx + 1}/{len(raw_routes)} 노선 처리 완료")

            # 쿼터 확인
            quota = self.api_client.check_remaining_quota()
            if quota["remaining"] < 100:
                logger.warning(f"일일 쿼터 부족 (남은: {quota['remaining']}). 수집 중단.")
                break

        self.stops_data.extend(seen_stops.values())
        logger.info(f"{region}: 노선 {len(raw_routes)}개, 정류장 {len(seen_stops)}개 처리 완료")

    def collect(self) -> dict[str, pd.DataFrame]:
        """전체 버스 노선/정류장 데이터 수집.

        Returns:
            {"routes": DataFrame, "stops": DataFrame, "route_stops": DataFrame}
        """
        if not self.api_client.data_go_kr_key:
            logger.warning("API 키 미설정. 버스 노선 수집을 건너뜁니다.")
            return {
                "routes": pd.DataFrame(),
                "stops": pd.DataFrame(),
                "route_stops": pd.DataFrame(),
            }

        logger.info("버스 노선/정류장 데이터 수집 시작")
        self.routes_data = []
        self.stops_data = []
        self.route_stops_data = []

        for region in self.regions:
            if region not in REGION_CONFIG:
                logger.warning(f"지원하지 않는 지역: {region}")
                continue
            try:
                self._process_region(region)
            except Exception as e:
                logger.error(f"{region} 수집 중 오류: {e}", exc_info=True)

        # DataFrame 생성 및 저장
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        df_routes = pd.DataFrame(self.routes_data)
        df_stops = pd.DataFrame(self.stops_data)
        df_route_stops = pd.DataFrame(self.route_stops_data)

        if not df_routes.empty:
            path = self.OUTPUT_DIR / "bus_routes.csv"
            df_routes.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"노선 데이터 저장: {path} ({len(df_routes)}건)")

        if not df_stops.empty:
            path = self.OUTPUT_DIR / "bus_stops.csv"
            df_stops.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"정류장 데이터 저장: {path} ({len(df_stops)}건)")

        if not df_route_stops.empty:
            path = self.OUTPUT_DIR / "bus_route_stops.csv"
            df_route_stops.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"노선-정류장 데이터 저장: {path} ({len(df_route_stops)}건)")

        return {
            "routes": df_routes,
            "stops": df_stops,
            "route_stops": df_route_stops,
        }

    def validate(self, data: dict[str, pd.DataFrame] | None = None) -> dict:
        """수집된 데이터 품질 검증.

        Returns:
            검증 결과 딕셔너리
        """
        if data is None:
            data = {}
            for name in ["bus_routes", "bus_stops", "bus_route_stops"]:
                path = self.OUTPUT_DIR / f"{name}.csv"
                if path.exists():
                    key = name.replace("bus_", "").rstrip("s") + "s" if name != "bus_route_stops" else "route_stops"
                    data[key] = pd.read_csv(path)

        issues = []

        # 노선 검증
        df_routes = data.get("routes", pd.DataFrame())
        if df_routes.empty:
            issues.append("노선 데이터 없음")
        else:
            required = ["region", "route_id", "route_name", "route_type", "start_stop", "end_stop"]
            missing = [c for c in required if c not in df_routes.columns]
            if missing:
                issues.append(f"노선 누락 컬럼: {missing}")
            if "route_id" in df_routes.columns:
                dupes = df_routes["route_id"].duplicated().sum()
                if dupes > 0:
                    issues.append(f"노선 ID 중복: {dupes}건")

        # 정류장 검증
        df_stops = data.get("stops", pd.DataFrame())
        if df_stops.empty:
            issues.append("정류장 데이터 없음")
        else:
            if "latitude" in df_stops.columns:
                invalid_coords = ((df_stops["latitude"].astype(float) < 33) |
                                  (df_stops["latitude"].astype(float) > 43)).sum()
                if invalid_coords > 0:
                    issues.append(f"유효하지 않은 좌표: {invalid_coords}건")

        # 노선-정류장 검증
        df_rs = data.get("route_stops", pd.DataFrame())
        if df_rs.empty:
            issues.append("노선-정류장 데이터 없음")
        else:
            if "stop_sequence" in df_rs.columns:
                # 각 노선의 정류장 순서가 연속적인지 확인
                seq_issues = 0
                for route_id, group in df_rs.groupby("route_id"):
                    seqs = sorted(group["stop_sequence"].astype(int).tolist())
                    if seqs and seqs[0] != 1:
                        seq_issues += 1
                if seq_issues > 10:
                    issues.append(f"정류장 순서 시작값 오류: {seq_issues}개 노선")

        result = {
            "valid": len(issues) == 0,
            "routes_count": len(df_routes),
            "stops_count": len(df_stops),
            "route_stops_count": len(df_rs),
            "regions": sorted(df_routes["region"].unique().tolist()) if not df_routes.empty and "region" in df_routes.columns else [],
            "issues": issues,
        }
        logger.info(f"검증 결과: {result}")
        return result
