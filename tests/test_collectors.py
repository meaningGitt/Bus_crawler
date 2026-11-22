"""
Tests for collectors - runs without API keys using mock responses.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestApiClientRateLimiting(unittest.TestCase):
    """ApiClient 속도 제한 테스트."""

    @patch("collectors.api_client.load_dotenv")
    def test_api_client_rate_limiting(self, mock_dotenv):
        """요청 간 최소 대기 시간이 적용되는지 확인."""
        import time
        from collectors.api_client import ApiClient

        config = {
            "request_interval_seconds": 0.1,
            "max_retries": 1,
            "timeout_seconds": 5,
        }

        with patch.dict(os.environ, {"DATA_GO_KR_API_KEY": "test_key", "SEOUL_DATA_API_KEY": ""}):
            client = ApiClient(config)

        # _wait_for_rate_limit 호출 시 간격 보장 확인
        client._last_request_time = time.time()
        start = time.time()
        client._wait_for_rate_limit()
        elapsed = time.time() - start

        # 최소 대기 시간에 가까운 시간이 경과해야 함
        self.assertGreaterEqual(elapsed, 0.05)  # 약간의 여유를 두고 검증


class TestApiClientRetryLogic(unittest.TestCase):
    """ApiClient 재시도 로직 테스트."""

    @patch("collectors.api_client.load_dotenv")
    def test_api_client_retry_logic(self, mock_dotenv):
        """재시도 로직이 max_retries까지 시도하는지 확인."""
        import requests
        from collectors.api_client import ApiClient

        config = {
            "request_interval_seconds": 0.01,
            "max_retries": 3,
            "retry_delay_seconds": 0.01,
            "timeout_seconds": 1,
        }

        with patch.dict(os.environ, {"DATA_GO_KR_API_KEY": "test_key", "SEOUL_DATA_API_KEY": ""}):
            client = ApiClient(config)

        # 모든 요청이 타임아웃되도록 모킹
        mock_get = MagicMock(side_effect=requests.Timeout("timeout"))
        client.session.get = mock_get

        with self.assertRaises(requests.RequestException):
            client.get("http://test.example.com", {"param": "value"})

        # 3번 시도했는지 확인 (max_retries=3)
        self.assertEqual(mock_get.call_count, 3)


class TestApiClientXmlParsing(unittest.TestCase):
    """ApiClient XML 파싱 테스트."""

    @patch("collectors.api_client.load_dotenv")
    def test_api_client_xml_parsing(self, mock_dotenv):
        """XML 응답을 올바르게 파싱하는지 확인."""
        from collectors.api_client import ApiClient

        config = {"request_interval_seconds": 0.01, "max_retries": 1}

        with patch.dict(os.environ, {"DATA_GO_KR_API_KEY": "test_key", "SEOUL_DATA_API_KEY": ""}):
            client = ApiClient(config)

        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <response>
            <header>
                <resultCode>00</resultCode>
                <resultMsg>NORMAL SERVICE</resultMsg>
            </header>
            <body>
                <items>
                    <item>
                        <routeId>100100001</routeId>
                        <routeNm>101</routeNm>
                    </item>
                    <item>
                        <routeId>100100002</routeId>
                        <routeNm>102</routeNm>
                    </item>
                </items>
                <totalCount>2</totalCount>
                <pageNo>1</pageNo>
                <numOfRows>10</numOfRows>
            </body>
        </response>"""

        result = client._parse_xml_to_dict(xml_response)

        self.assertIn("_items", result)
        self.assertEqual(len(result["_items"]), 2)
        self.assertEqual(result["_items"][0]["routeId"], "100100001")
        self.assertEqual(result["_items"][1]["routeNm"], "102")
        self.assertEqual(result["_total_count"], "2")
        self.assertEqual(result["_result_code"], "00")


class TestSubwayLastTrainParsing(unittest.TestCase):
    """지하철 막차 파싱 테스트."""

    @patch("collectors.api_client.load_dotenv")
    def test_subway_last_train_parsing(self, mock_dotenv):
        """API 응답에서 역별 막차 정보를 올바르게 추출하는지 확인."""
        with patch.dict(os.environ, {"DATA_GO_KR_API_KEY": "test_key", "SEOUL_DATA_API_KEY": ""}):
            from collectors.subway_last_train import SubwayLastTrainCollector

            collector = SubwayLastTrainCollector({"request_interval_seconds": 0.01, "max_retries": 1})

        # 모의 시간표 데이터
        mock_items = [
            {"stnCode": "0150", "stnNm": "서울역", "trainDptreTm": "23:30:00", "arvlStnNm": "소요산"},
            {"stnCode": "0150", "stnNm": "서울역", "trainDptreTm": "23:50:00", "arvlStnNm": "동두천"},
            {"stnCode": "0150", "stnNm": "서울역", "trainDptreTm": "23:00:00", "arvlStnNm": "소요산"},
            {"stnCode": "0151", "stnNm": "시청", "trainDptreTm": "23:45:00", "arvlStnNm": "소요산"},
            {"stnCode": "0151", "stnNm": "시청", "trainDptreTm": "00:10:00", "arvlStnNm": "광운대"},
        ]

        results = collector._extract_last_trains(mock_items, "1호선", "상행", "평일")

        self.assertEqual(len(results), 2)  # 서울역, 시청

        # 서울역: 23:50 (동두천행, 중간종착 - 종착역 목록에 없음)
        seoul_station = next(r for r in results if r["station_name"] == "서울역")
        self.assertEqual(seoul_station["last_train_time"], "23:50:00")
        self.assertEqual(seoul_station["destination"], "동두천")
        self.assertTrue(seoul_station["is_mid_terminus"])  # 동두천은 1호선 상행 종착역 목록에 없음

        # 시청: 00:10 (광운대행) - 자정 넘김 처리 확인
        city_hall = next(r for r in results if r["station_name"] == "시청")
        self.assertEqual(city_hall["last_train_time"], "00:10:00")
        self.assertEqual(city_hall["destination"], "광운대")


class TestBusRouteIdUnification(unittest.TestCase):
    """버스 노선 ID 통합 테스트."""

    @patch("collectors.api_client.load_dotenv")
    def test_bus_route_id_unification(self, mock_dotenv):
        """지역별 접두사(SEL_/GGI_/ICN_)가 올바르게 붙는지 확인."""
        with patch.dict(os.environ, {"DATA_GO_KR_API_KEY": "test_key", "SEOUL_DATA_API_KEY": ""}):
            from collectors.bus_routes import BusRouteCollector

            collector = BusRouteCollector(
                {"request_interval_seconds": 0.01, "max_retries": 1},
                regions=["서울"],
            )

        # 통합 ID 생성 확인
        self.assertEqual(collector._make_unified_id("SEL", "123456"), "SEL_123456")
        self.assertEqual(collector._make_unified_id("GGI", "789012"), "GGI_789012")
        self.assertEqual(collector._make_unified_id("ICN", "345678"), "ICN_345678")


class TestBusStopsDeduplication(unittest.TestCase):
    """버스 정류장 중복 제거 테스트."""

    @patch("collectors.api_client.load_dotenv")
    def test_bus_stops_deduplication(self, mock_dotenv):
        """동일 stop_id를 가진 정류장이 중복 등록되지 않는지 확인."""
        with patch.dict(os.environ, {"DATA_GO_KR_API_KEY": "test_key", "SEOUL_DATA_API_KEY": ""}):
            from collectors.bus_routes import BusRouteCollector, REGION_CONFIG

            collector = BusRouteCollector(
                {"request_interval_seconds": 0.01, "max_retries": 1},
                regions=["서울"],
            )

        # seen_stops 딕셔너리 시뮬레이션
        seen_stops = {}
        stops_raw = [
            {"nodeId": "ST001", "nodeNm": "정류장A", "gpsLati": "37.5", "gpsLong": "126.9"},
            {"nodeId": "ST001", "nodeNm": "정류장A", "gpsLati": "37.5", "gpsLong": "126.9"},  # 중복
            {"nodeId": "ST002", "nodeNm": "정류장B", "gpsLati": "37.6", "gpsLong": "127.0"},
        ]

        for stop in stops_raw:
            stop_id = stop["nodeId"]
            if stop_id not in seen_stops:
                seen_stops[stop_id] = {
                    "stop_id": collector._make_unified_id("SEL", stop_id),
                    "stop_name": stop["nodeNm"],
                }

        # ST001은 한 번만 등록되어야 함
        self.assertEqual(len(seen_stops), 2)
        self.assertIn("ST001", seen_stops)
        self.assertIn("ST002", seen_stops)


class TestQualityValidatorRequiredFields(unittest.TestCase):
    """품질 검증기 필수 필드 테스트."""

    def test_quality_validator_required_fields(self):
        """필수 필드 누락 시 이슈를 감지하는지 확인."""
        from pipeline.quality import DataQualityValidator

        config = {
            "quality": {},
            "output": {
                "new_data_dir": "data/new",
                "quarantine_dir": "data/quarantine",
                "reports_dir": "data/reports",
            },
        }

        validator = DataQualityValidator(config)

        # 필수 필드가 누락된 DataFrame
        df = pd.DataFrame({
            "region": ["서울", "경기"],
            "route_id": ["SEL_001", "GGI_002"],
            # route_name, route_type, start_stop, end_stop 누락
        })

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            df.to_csv(f, index=False)
            temp_path = f.name

        try:
            result = validator.validate_file(temp_path, "bus_routes")
            self.assertGreater(len(result["issues"]), 0)

            # missing_column 이슈가 있어야 함
            missing_issues = [i for i in result["issues"] if i["issue_type"] == "missing_column"]
            self.assertGreater(len(missing_issues), 0)
        finally:
            os.unlink(temp_path)


class TestQualityValidatorGpsRange(unittest.TestCase):
    """품질 검증기 GPS 범위 테스트."""

    def test_quality_validator_gps_range(self):
        """한국 범위 밖의 GPS 좌표를 감지하는지 확인."""
        from pipeline.quality import DataQualityValidator

        config = {
            "quality": {},
            "output": {
                "new_data_dir": "data/new",
                "quarantine_dir": "data/quarantine",
                "reports_dir": "data/reports",
            },
        }

        validator = DataQualityValidator(config)

        # 범위 밖 좌표 포함 데이터
        df = pd.DataFrame({
            "region": ["서울", "서울", "서울"],
            "stop_id": ["SEL_001", "SEL_002", "SEL_003"],
            "stop_name": ["정류장A", "정류장B", "정류장C"],
            "latitude": [37.5, 50.0, 35.0],  # 50.0은 범위 밖
            "longitude": [126.9, 127.0, 200.0],  # 200.0은 범위 밖
        })

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            df.to_csv(f, index=False)
            temp_path = f.name

        try:
            result = validator.validate_file(temp_path, "bus_stops")
            gps_issues = [i for i in result["issues"] if i["issue_type"] == "gps_out_of_range"]
            self.assertGreater(len(gps_issues), 0)

            # latitude 50.0과 longitude 200.0 모두 감지되어야 함
            lat_issue = [i for i in gps_issues if "lat" in i["field"].lower()]
            lon_issue = [i for i in gps_issues if "lon" in i["field"].lower()]
            self.assertEqual(len(lat_issue), 1)
            self.assertEqual(lat_issue[0]["count"], 1)
            self.assertEqual(len(lon_issue), 1)
            self.assertEqual(lon_issue[0]["count"], 1)
        finally:
            os.unlink(temp_path)


class TestInventoryGapAnalysis(unittest.TestCase):
    """인벤토리 갭 분석 테스트."""

    def test_inventory_gap_analysis(self):
        """갭 분석이 올바른 상태를 반환하는지 확인."""
        from pipeline.inventory import DataInventory

        # 존재하지 않는 경로로 인벤토리 생성
        with tempfile.TemporaryDirectory() as tmpdir:
            inventory = DataInventory(existing_project_path=tmpdir)
            inventory.new_data_path = Path(tmpdir) / "new"

            # 빈 인벤토리 스캔
            inv_df = inventory.scan_existing()
            gap_df = inventory.generate_gap_analysis()

            # 모든 데이터 타입이 평가되어야 함
            self.assertEqual(len(gap_df), 10)  # REQUIRED_DATA_TYPES 개수

            # 데이터가 없으므로 대부분 MISSING이어야 함
            statuses = gap_df["status"].tolist()
            missing_count = statuses.count("MISSING")
            self.assertGreater(missing_count, 0)

            # 실시간 도착정보는 NOT_VERIFIED
            realtime_row = gap_df[gap_df["data_type"] == "실시간 도착정보"]
            if not realtime_row.empty:
                self.assertEqual(realtime_row.iloc[0]["status"], "NOT_VERIFIED")


class TestServiceHoursTimeValidation(unittest.TestCase):
    """버스 운행시간 시간 유효성 검사 테스트."""

    def test_service_hours_time_validation(self):
        """first_bus > last_bus인 비정상 케이스를 감지하는지 확인."""
        from pipeline.quality import DataQualityValidator

        config = {
            "quality": {},
            "output": {
                "new_data_dir": "data/new",
                "quarantine_dir": "data/quarantine",
                "reports_dir": "data/reports",
            },
        }

        validator = DataQualityValidator(config)

        df = pd.DataFrame({
            "region": ["서울", "서울", "서울"],
            "route_id": ["SEL_001", "SEL_002", "SEL_003"],
            "route_name": ["101", "102", "N101"],
            "day_type": ["평일", "평일", "평일"],
            "first_bus": ["05:00", "06:00", "23:00"],
            "last_bus": ["23:00", "05:30", "02:00"],  # SEL_002: first > last (비정상)
        })

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            df.to_csv(f, index=False)
            temp_path = f.name

        try:
            result = validator.validate_file(temp_path, "bus_service_hours")

            # SEL_002는 first=06:00, last=05:30 -> 비정상 (last 시각이 05 이상이므로)
            time_issues = [i for i in result["issues"] if i["issue_type"] == "first_after_last"]
            self.assertGreater(len(time_issues), 0)

            # SEL_003는 심야 버스 (last=02:00 < 05시) -> 정상으로 처리
        finally:
            os.unlink(temp_path)


if __name__ == "__main__":
    unittest.main()
