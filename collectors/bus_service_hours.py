"""
버스 첫차/막차 시각 및 배차간격 정보 수집
- 서울: 서울 열린데이터 API (busRouteInfo) 우선, data.go.kr TAGO API 대안
- 경기: data.go.kr getBusRouteInfoItemv2
- 인천: data.go.kr getBusRouteInfoItem (XML)
- API 실패 시 graceful 처리 및 가이드 리포트 생성
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

# 서울 열린데이터 API 버스 서비스명
SEOUL_BUS_ROUTE_INFO_SERVICE = "busRouteInfo"


class BusServiceHoursCollector:
    """버스 운행 시간 정보 수집기."""

    OUTPUT_PATH = Path("data/new/bus_service_hours.csv")
    REPORT_DIR = Path("data/reports")

    # 지역별 API 설정
    API_CONFIG = {
        "서울": {
            "code": "SEL",
            "base_url": "https://apis.data.go.kr/1613000/BusRouteInfoInqireService",
            "route_list_endpoint": "/getRouteNoList",
            "route_info_endpoint": "/getRouteInfoIem",
            "format": "json",
            "city_code": "11",
        },
        "경기": {
            "code": "GGI",
            "base_url": "https://apis.data.go.kr/6410000/busrouteservice/v2",
            "route_list_endpoint": "/getBusRouteListv2",
            "route_info_endpoint": "/getBusRouteInfoItemv2",
            "format": "json",
        },
        "인천": {
            "code": "ICN",
            "base_url": "https://apis.data.go.kr/6280000/busRouteService",
            "route_list_endpoint": "/getBusRouteList",
            "route_info_endpoint": "/getBusRouteInfoItem",
            "format": "xml",
        },
    }

    def __init__(self, config: dict | None = None, regions: list[str] | None = None):
        if config is None:
            config_path = Path("config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    full_config = yaml.safe_load(f)
                config = full_config.get("collection", {})
            else:
                config = {}

        self.api_client = ApiClient(config)
        self.regions = regions or list(self.API_CONFIG.keys())
        self.results: list[dict] = []
        self._api_failures: dict[str, str] = {}

    def _format_time(self, raw_time: str | None) -> str:
        """시간 문자열을 HH:MM 형식으로 정규화."""
        if not raw_time:
            return ""
        raw_time = str(raw_time).strip()
        # HHMM -> HH:MM
        if len(raw_time) == 4 and raw_time.isdigit():
            return f"{raw_time[:2]}:{raw_time[2:]}"
        # HHMMSS -> HH:MM
        if len(raw_time) == 6 and raw_time.isdigit():
            return f"{raw_time[:2]}:{raw_time[2:4]}"
        # 이미 HH:MM 또는 HH:MM:SS
        if ":" in raw_time:
            return raw_time[:5]
        return raw_time

    # ================================================================
    # 서울 열린데이터 API 기반 수집
    # ================================================================

    def _collect_seoul_via_open_api(self) -> bool:
        """서울 열린데이터 API (busRouteInfo)로 운행 시간 수집.

        Returns:
            성공 여부
        """
        logger.info("서울 열린데이터 API (busRouteInfo)로 운행 시간 수집 시도")
        now = datetime.now(KST)

        try:
            route_rows = self.api_client.get_seoul_api_all(
                SEOUL_BUS_ROUTE_INFO_SERVICE,
                max_rows=10000,
            )
        except Exception as e:
            logger.warning(f"서울 열린데이터 busRouteInfo 조회 실패: {e}")
            self._api_failures["서울_openapi_busRouteInfo"] = str(e)
            return False

        if not route_rows:
            logger.warning("서울 열린데이터 busRouteInfo: 데이터 없음")
            if self.api_client._is_sample_key:
                logger.warning(
                    "sample 키 사용 중 (5건 제한). "
                    "SEOUL_DATA_API_KEY를 설정하면 전체 데이터를 수집할 수 있습니다."
                )
            self._api_failures["서울_openapi_busRouteInfo"] = "데이터 없음"
            return False

        logger.info(f"서울(OpenAPI): {len(route_rows)}개 노선 운행정보 조회됨")

        for route_raw in route_rows:
            route_id = str(route_raw.get("ROUTE_ID", route_raw.get("routeId", "")))
            route_name = route_raw.get("ROUTE_NM", route_raw.get("busRouteNm", ""))

            if not route_id:
                continue

            # 서울 열린데이터 API 필드에서 운행 시간 추출
            first_bus_tm = route_raw.get("FIRST_BUS_TM", route_raw.get("firstBusTm", ""))
            last_bus_tm = route_raw.get("LAST_BUS_TM", route_raw.get("lastBusTm", ""))
            first_low_tm = route_raw.get("FIRST_LOW_TM", "")
            last_low_tm = route_raw.get("LAST_LOW_TM", "")
            interval = route_raw.get("TERM", route_raw.get("intervalTime", ""))

            first_bus = self._format_time(first_bus_tm or first_low_tm)
            last_bus = self._format_time(last_bus_tm or last_low_tm)

            self.results.append({
                "region": "서울",
                "route_id": f"SEL_{route_id}",
                "route_name": route_name,
                "day_type": "평일",
                "first_bus": first_bus,
                "last_bus": last_bus,
                "interval_minutes": interval if interval else None,
                "interval_peak": None,
                "interval_offpeak": None,
                "start_stop_name": route_raw.get("ORIGIN_STOP", route_raw.get("stStaNm", "")),
                "end_stop_name": route_raw.get("DEST_STOP", route_raw.get("edStaNm", "")),
                "per_stop_last_available": "NOT_VERIFIED",
                "data_date": now.strftime("%Y-%m-%d"),
                "collected_at": now.isoformat(),
            })

        logger.info(f"서울(OpenAPI): {len(route_rows)}개 노선 운행시간 처리 완료")
        return True

    # ================================================================
    # data.go.kr 기반 수집 (기존 코드, 인증 실패 graceful 처리)
    # ================================================================

    def _collect_seoul(self):
        """서울 버스 운행 시간 수집.

        서울 열린데이터 API 우선 시도, 실패 시 data.go.kr TAGO API 사용.
        """
        # 1. 서울 열린데이터 API 먼저 시도
        if self._collect_seoul_via_open_api():
            return

        logger.info("서울 열린데이터 API 실패. data.go.kr TAGO API로 대체 시도.")

        if not self.api_client.data_go_kr_key:
            logger.warning(
                "서울: 서울 열린데이터 API와 data.go.kr API 모두 사용 불가. "
                "SEOUL_DATA_API_KEY 또는 DATA_GO_KR_API_KEY를 설정하세요."
            )
            self._api_failures["서울_키미설정"] = "API 키 미설정"
            return

        cfg = self.API_CONFIG["서울"]
        now = datetime.now(KST)

        # 노선 목록 조회
        list_url = cfg["base_url"] + cfg["route_list_endpoint"]
        params = {"cityCode": cfg["city_code"]}
        try:
            routes = self.api_client.get_paginated(list_url, params, response_format="json", page_size=100)
        except Exception as e:
            logger.warning(f"서울 TAGO API 노선 목록 조회 실패: {e}")
            self._api_failures["서울_tago_route_list"] = str(e)
            return

        logger.info(f"서울 노선 {len(routes)}개 조회됨")

        info_url = cfg["base_url"] + cfg["route_info_endpoint"]

        for idx, route in enumerate(routes):
            route_id = str(route.get("routeId", ""))
            route_name = route.get("routeNo", route.get("routeNm", ""))

            if not route_id:
                continue

            # 상세정보 조회
            try:
                data = self.api_client.get(
                    info_url,
                    {"cityCode": cfg["city_code"], "routeId": route_id},
                    response_format="json",
                )
                items = self.api_client._extract_items(data, "json")
                info = items[0] if items else {}
            except Exception as e:
                logger.warning(f"서울 노선 상세 조회 실패 ({route_name}): {e}")
                continue

            first_bus = self._format_time(info.get("firstBusTm", ""))
            last_bus = self._format_time(info.get("lastBusTm", ""))
            interval = info.get("intervalTime", info.get("term", ""))

            self.results.append({
                "region": "서울",
                "route_id": f"SEL_{route_id}",
                "route_name": route_name,
                "day_type": "평일",
                "first_bus": first_bus,
                "last_bus": last_bus,
                "interval_minutes": interval if interval else None,
                "interval_peak": None,
                "interval_offpeak": None,
                "start_stop_name": info.get("startNodeNm", route.get("startNodeNm", "")),
                "end_stop_name": info.get("endNodeNm", route.get("endNodeNm", "")),
                "per_stop_last_available": "NOT_VERIFIED",
                "data_date": now.strftime("%Y-%m-%d"),
                "collected_at": now.isoformat(),
            })

            if (idx + 1) % 100 == 0:
                logger.info(f"서울: {idx + 1}/{len(routes)} 노선 처리")

            if self.api_client.check_remaining_quota()["remaining"] < 100:
                logger.warning("쿼터 부족. 서울 수집 중단.")
                break

    def _collect_gyeonggi(self):
        """경기 버스 운행 시간 수집."""
        if not self.api_client.data_go_kr_key:
            logger.warning("경기: DATA_GO_KR_API_KEY 미설정으로 수집 건너뜀.")
            self._api_failures["경기_키미설정"] = "DATA_GO_KR_API_KEY 필요"
            return

        cfg = self.API_CONFIG["경기"]
        now = datetime.now(KST)

        list_url = cfg["base_url"] + cfg["route_list_endpoint"]
        try:
            routes = self.api_client.get_paginated(list_url, {}, response_format="json", page_size=100)
        except Exception as e:
            logger.warning(f"경기 API 노선 목록 조회 실패: {e}")
            self._api_failures["경기_route_list"] = str(e)
            return

        logger.info(f"경기 노선 {len(routes)}개 조회됨")

        info_url = cfg["base_url"] + cfg["route_info_endpoint"]

        for idx, route in enumerate(routes):
            route_id = str(route.get("routeId", ""))
            route_name = route.get("routeName", route.get("routeNm", ""))

            if not route_id:
                continue

            try:
                data = self.api_client.get(info_url, {"routeId": route_id}, response_format="json")
                items = self.api_client._extract_items(data, "json")
                info = items[0] if items else {}
            except Exception as e:
                logger.warning(f"경기 노선 상세 조회 실패 ({route_name}): {e}")
                continue

            # 경기도 API는 상행/하행 별도 시간 제공
            up_first = self._format_time(info.get("upFirstTime", ""))
            up_last = self._format_time(info.get("upLastTime", ""))
            down_first = self._format_time(info.get("downFirstTime", ""))
            down_last = self._format_time(info.get("downLastTime", ""))

            # 첫차/막차는 더 이른/늦은 시각 사용
            first_bus = min(up_first, down_first) if up_first and down_first else (up_first or down_first)
            last_bus = max(up_last, down_last) if up_last and down_last else (up_last or down_last)

            interval_min = info.get("peekAlloc", "")
            interval_offpeak = info.get("nPeekAlloc", "")

            self.results.append({
                "region": "경기",
                "route_id": f"GGI_{route_id}",
                "route_name": route_name,
                "day_type": "평일",
                "first_bus": first_bus,
                "last_bus": last_bus,
                "interval_minutes": interval_min if interval_min else None,
                "interval_peak": interval_min if interval_min else None,
                "interval_offpeak": interval_offpeak if interval_offpeak else None,
                "start_stop_name": info.get("stStaNm", info.get("startStaNm", "")),
                "end_stop_name": info.get("edStaNm", info.get("endStaNm", "")),
                "per_stop_last_available": "NOT_VERIFIED",
                "data_date": now.strftime("%Y-%m-%d"),
                "collected_at": now.isoformat(),
            })

            if (idx + 1) % 100 == 0:
                logger.info(f"경기: {idx + 1}/{len(routes)} 노선 처리")

            if self.api_client.check_remaining_quota()["remaining"] < 100:
                logger.warning("쿼터 부족. 경기 수집 중단.")
                break

    def _collect_incheon(self):
        """인천 버스 운행 시간 수집 (XML)."""
        if not self.api_client.data_go_kr_key:
            logger.warning("인천: DATA_GO_KR_API_KEY 미설정으로 수집 건너뜀.")
            self._api_failures["인천_키미설정"] = "DATA_GO_KR_API_KEY 필요"
            return

        cfg = self.API_CONFIG["인천"]
        now = datetime.now(KST)

        list_url = cfg["base_url"] + cfg["route_list_endpoint"]
        try:
            routes = self.api_client.get_paginated(list_url, {}, response_format="xml", page_size=100)
        except Exception as e:
            logger.warning(f"인천 API 노선 목록 조회 실패: {e}")
            self._api_failures["인천_route_list"] = str(e)
            return

        logger.info(f"인천 노선 {len(routes)}개 조회됨")

        info_url = cfg["base_url"] + cfg["route_info_endpoint"]

        for idx, route in enumerate(routes):
            route_id = str(route.get("routeId", ""))
            route_name = route.get("routeNm", route.get("routeName", ""))

            if not route_id:
                continue

            try:
                data = self.api_client.get(info_url, {"routeId": route_id}, response_format="xml")
                items = data.get("_items", [])
                info = items[0] if items else {}
            except Exception as e:
                logger.warning(f"인천 노선 상세 조회 실패 ({route_name}): {e}")
                continue

            up_first = self._format_time(info.get("upFirstTime", ""))
            up_last = self._format_time(info.get("upLastTime", ""))

            self.results.append({
                "region": "인천",
                "route_id": f"ICN_{route_id}",
                "route_name": route_name,
                "day_type": "평일",
                "first_bus": up_first,
                "last_bus": up_last,
                "interval_minutes": info.get("intervalTime", None),
                "interval_peak": None,
                "interval_offpeak": None,
                "start_stop_name": info.get("stStaNm", info.get("startStaNm", "")),
                "end_stop_name": info.get("edStaNm", info.get("endStaNm", "")),
                "per_stop_last_available": "NOT_VERIFIED",
                "data_date": now.strftime("%Y-%m-%d"),
                "collected_at": now.isoformat(),
            })

            if (idx + 1) % 100 == 0:
                logger.info(f"인천: {idx + 1}/{len(routes)} 노선 처리")

            if self.api_client.check_remaining_quota()["remaining"] < 100:
                logger.warning("쿼터 부족. 인천 수집 중단.")
                break

    def _save_failure_report(self):
        """API 실패 시 가이드 리포트를 저장."""
        if not self._api_failures:
            return

        self.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        guide_path = self.REPORT_DIR / "api_registration_guide.md"

        # 기존 가이드가 있으면 실패 정보만 추가
        if guide_path.exists():
            existing = guide_path.read_text(encoding="utf-8")
            if "## 버스 운행시간 API 오류" not in existing:
                section = "\n## 버스 운행시간 API 오류\n\n"
                for api_name, error_msg in self._api_failures.items():
                    section += f"- **{api_name}**: {error_msg}\n"
                guide_path.write_text(existing + section, encoding="utf-8")
        else:
            # bus_routes.py에서 이미 생성했을 수 있으므로 간단 버전
            content = "# API 등록 가이드\n\n"
            content += "버스 운행시간 데이터 수집 API 오류가 발생했습니다.\n"
            content += "상세한 등록 절차는 버스 노선 수집 시 생성되는 가이드를 참고하세요.\n\n"
            content += "## 버스 운행시간 API 오류\n\n"
            for api_name, error_msg in self._api_failures.items():
                content += f"- **{api_name}**: {error_msg}\n"
            guide_path.write_text(content, encoding="utf-8")

        logger.info(f"API 오류 정보 저장: {guide_path}")

    def collect(self) -> pd.DataFrame:
        """전체 버스 운행 시간 정보 수집.

        Returns:
            수집된 운행 시간 DataFrame
        """
        logger.info("버스 운행 시간 정보 수집 시작")
        self.results = []
        self._api_failures = {}

        # API 키 상태 로깅
        if not self.api_client.data_go_kr_key:
            logger.warning(
                "DATA_GO_KR_API_KEY 미설정. data.go.kr 기반 API(경기/인천)는 사용 불가합니다."
            )
        if self.api_client._is_sample_key:
            logger.warning(
                "서울 열린데이터 API: sample 키 사용 중 (호출당 5건 제한). "
                "완전한 데이터 수집을 위해 SEOUL_DATA_API_KEY를 설정하세요."
            )

        region_collectors = {
            "서울": self._collect_seoul,
            "경기": self._collect_gyeonggi,
            "인천": self._collect_incheon,
        }

        for region in self.regions:
            if region in region_collectors:
                try:
                    region_collectors[region]()
                except Exception as e:
                    logger.error(f"{region} 운행시간 수집 오류: {e}", exc_info=True)
                    self._api_failures[f"{region}_수집오류"] = str(e)

        # API 실패가 있으면 가이드 저장
        if self._api_failures:
            self._save_failure_report()

        df = pd.DataFrame(self.results)

        if not df.empty:
            self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.OUTPUT_PATH, index=False, encoding="utf-8-sig")
            logger.info(f"운행 시간 저장 완료: {self.OUTPUT_PATH} ({len(df)}건)")
        else:
            logger.warning("수집된 운행 시간 정보가 없습니다.")

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
            "region", "route_id", "route_name", "day_type",
            "first_bus", "last_bus",
        ]
        missing_cols = [c for c in required_columns if c not in df.columns]

        issues = []
        if missing_cols:
            issues.append(f"누락 컬럼: {missing_cols}")

        # 첫차/막차 시간 유효성
        if "first_bus" in df.columns:
            empty_first = df["first_bus"].isna().sum() + (df["first_bus"] == "").sum()
            if empty_first > len(df) * 0.3:
                issues.append(f"첫차 시간 누락 과다: {empty_first}/{len(df)}건")

        if "last_bus" in df.columns:
            empty_last = df["last_bus"].isna().sum() + (df["last_bus"] == "").sum()
            if empty_last > len(df) * 0.3:
                issues.append(f"막차 시간 누락 과다: {empty_last}/{len(df)}건")

        # 지역별 분포
        region_counts = df["region"].value_counts().to_dict() if "region" in df.columns else {}

        result = {
            "valid": len(issues) == 0,
            "total_records": len(df),
            "region_counts": region_counts,
            "issues": issues,
        }
        logger.info(f"검증 결과: {result}")
        return result
