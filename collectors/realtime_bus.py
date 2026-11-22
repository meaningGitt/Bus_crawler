"""
P1-A: 실시간 버스 도착 API 가용성 검증
- TAGO, 경기도, 인천 실시간 도착정보 API 테스트
- 각 API의 응답 구조, 제공 필드, 인증 방식 기록
- 지속적 수집은 구현하지 않음 (검증만 수행)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from collectors.api_client import ApiClient

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# API별 테스트 설정
REALTIME_APIS = {
    "tago": {
        "name": "TAGO 버스도착정보",
        "endpoint": "https://apis.data.go.kr/1613000/ArvlInfoInqireService/getSttnAcctoArvlPrearngeInfoList",
        "auth_method": "serviceKey (data.go.kr)",
        "format": "json",
        "rate_limit": "일 10,000회",
        "sample_params": {
            "cityCode": "11",
            "nodeId": "ICB165000181",  # 샘플 정류장 ID
        },
        "expected_fields": [
            "routeId", "routeNo", "routeTp",
            "arrprevstationcnt", "arrtime",
            "vehicletp",
        ],
    },
    "gyeonggi": {
        "name": "경기도 버스도착정보",
        "endpoint": "https://apis.data.go.kr/6410000/busarrivalservice/v2/getBusArrivalItemv2",
        "auth_method": "serviceKey (data.go.kr)",
        "format": "json",
        "rate_limit": "일 10,000회",
        "sample_params": {
            "stationId": "200000078",  # 샘플 정류장 ID
        },
        "expected_fields": [
            "routeId", "locationNo1", "predictTime1",
            "locationNo2", "predictTime2",
            "flag", "plateNo1",
        ],
    },
    "incheon": {
        "name": "인천 버스도착정보",
        "endpoint": "https://apis.data.go.kr/6280000/busArrivalService/getAllRouteBusArrivalList",
        "auth_method": "serviceKey (data.go.kr)",
        "format": "xml",
        "rate_limit": "일 10,000회",
        "sample_params": {
            "routeId": "165000001",  # 샘플 노선 ID
        },
        "expected_fields": [
            "ROUTE_ID", "BUS_NUM_PLATE", "REMAIN_STOP_CNT",
            "REMAIN_MIN", "DIR",
        ],
    },
}


class RealtimeBusValidator:
    """실시간 버스 도착 API 가용성 검증기."""

    OUTPUT_PATH = Path("data/reports/realtime_api_validation.json")

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
        self.validation_results: dict = {}

    def _test_api(self, api_key: str, api_config: dict) -> dict:
        """단일 API에 대해 테스트 호출을 수행.

        Args:
            api_key: API 식별자 (tago, gyeonggi, incheon)
            api_config: API 설정

        Returns:
            테스트 결과 딕셔너리
        """
        now = datetime.now(KST)
        result = {
            "api_name": api_config["name"],
            "endpoint": api_config["endpoint"],
            "auth_method": api_config["auth_method"],
            "response_format": api_config["format"],
            "rate_limit": api_config["rate_limit"],
            "test_timestamp": now.isoformat(),
            "api_key_available": bool(self.api_client.data_go_kr_key),
            "test_success": False,
            "response_fields": [],
            "sample_response": None,
            "available_info": {},
            "error": None,
        }

        if not self.api_client.data_go_kr_key:
            result["error"] = "API 키 미설정"
            logger.warning(f"{api_config['name']}: API 키 없음. 테스트 건너뜀.")
            return result

        try:
            params = dict(api_config["sample_params"])
            params["numOfRows"] = 5  # 테스트이므로 소량만

            data = self.api_client.get(
                api_config["endpoint"],
                params,
                response_format=api_config["format"],
            )

            # 응답에서 아이템 추출
            if api_config["format"] == "xml":
                items = data.get("_items", [])
            else:
                items = self.api_client._extract_items(data, "json")

            if items:
                sample = items[0]
                result["test_success"] = True
                result["response_fields"] = list(sample.keys())
                # 샘플 응답 (민감 정보 제거)
                result["sample_response"] = {
                    k: str(v)[:100] for k, v in sample.items()
                }

                # 제공 필드 분석
                expected = set(api_config["expected_fields"])
                actual = set(sample.keys())
                result["available_info"] = {
                    "expected_fields": list(expected),
                    "found_fields": list(expected & actual),
                    "missing_fields": list(expected - actual),
                    "extra_fields": list(actual - expected),
                    "has_arrival_time": any(
                        f in actual for f in ["arrtime", "predictTime1", "REMAIN_MIN"]
                    ),
                    "has_vehicle_info": any(
                        f in actual for f in ["vehicletp", "plateNo1", "BUS_NUM_PLATE"]
                    ),
                    "has_last_bus_flag": any(
                        f in actual for f in ["lastBusYn", "flag"]
                    ),
                }

                logger.info(f"{api_config['name']}: 테스트 성공. {len(items)}건 응답, "
                            f"{len(result['response_fields'])}개 필드")
            else:
                result["test_success"] = True
                result["error"] = "응답은 성공했으나 데이터가 비어있음 (샘플 ID가 유효하지 않을 수 있음)"
                logger.info(f"{api_config['name']}: 응답 성공, 데이터 없음")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"{api_config['name']}: 테스트 실패 - {e}")

        return result

    def collect(self) -> dict:
        """모든 실시간 API를 검증.

        Returns:
            검증 결과 딕셔너리
        """
        logger.info("실시간 버스 도착 API 가용성 검증 시작")

        self.validation_results = {
            "validation_timestamp": datetime.now(KST).isoformat(),
            "apis": {},
            "summary": {},
        }

        for api_key, api_config in REALTIME_APIS.items():
            logger.info(f"테스트 중: {api_config['name']}")
            result = self._test_api(api_key, api_config)
            self.validation_results["apis"][api_key] = result

        # 요약 생성
        apis = self.validation_results["apis"]
        self.validation_results["summary"] = {
            "total_apis": len(apis),
            "successful": sum(1 for a in apis.values() if a["test_success"]),
            "failed": sum(1 for a in apis.values() if not a["test_success"]),
            "api_key_available": bool(self.api_client.data_go_kr_key),
        }

        # 저장
        self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(self.OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(self.validation_results, f, ensure_ascii=False, indent=2)
        logger.info(f"검증 결과 저장: {self.OUTPUT_PATH}")

        return self.validation_results

    def validate(self, data: dict | None = None) -> dict:
        """검증 결과의 완전성을 확인."""
        if data is None:
            if self.OUTPUT_PATH.exists():
                with open(self.OUTPUT_PATH, encoding="utf-8") as f:
                    data = json.load(f)
            else:
                return {"valid": False, "error": "검증 결과 파일 없음"}

        issues = []
        apis = data.get("apis", {})

        if len(apis) < 3:
            issues.append(f"검증된 API 수 부족: {len(apis)}/3")

        for api_key, result in apis.items():
            if not result.get("test_success") and result.get("api_key_available"):
                issues.append(f"{result['api_name']}: 테스트 실패 - {result.get('error', 'unknown')}")

        return {
            "valid": len(issues) == 0,
            "total_apis_tested": len(apis),
            "successful": data.get("summary", {}).get("successful", 0),
            "issues": issues,
        }
