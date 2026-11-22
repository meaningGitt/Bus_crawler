"""
공공데이터포털(data.go.kr) API 클라이언트
- 인증키 관리, 속도 제한, 재시도 로직, JSON/XML 응답 처리
"""

import os
import time
import logging
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, date

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class ApiClient:
    """공공데이터포털 API 호출을 위한 기본 클라이언트."""

    def __init__(self, config: dict):
        """
        Args:
            config: 설정 딕셔너리. 아래 키를 사용:
                - request_interval_seconds (float): 요청 간 최소 대기 시간 (기본 0.5)
                - max_retries (int): 최대 재시도 횟수 (기본 3)
                - retry_delay_seconds (float): 재시도 대기 시간 기본값 (기본 5)
                - timeout_seconds (int): 요청 타임아웃 (기본 30)
                - user_agent (str): User-Agent 헤더
                - daily_quota (int): 일일 쿼터 (기본 10000)
        """
        load_dotenv()

        self.data_go_kr_key = os.getenv("DATA_GO_KR_API_KEY", "")
        self.seoul_data_key = os.getenv("SEOUL_DATA_API_KEY", "")

        if not self.data_go_kr_key:
            logger.warning("DATA_GO_KR_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
        if not self.seoul_data_key:
            logger.warning("SEOUL_DATA_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

        self.request_interval = config.get("request_interval_seconds", 0.5)
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay_seconds", 5)
        self.timeout = config.get("timeout_seconds", 30)
        self.user_agent = config.get("user_agent", "AnjeulJido-DataCollector/1.0")
        self.daily_quota = config.get("daily_quota", 10000)

        self._last_request_time = 0.0
        self._request_counts: dict[str, int] = defaultdict(int)
        self._total_requests_today = 0
        self._quota_date = date.today()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def _wait_for_rate_limit(self):
        """요청 간 최소 대기 시간을 보장."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_interval:
            wait_time = self.request_interval - elapsed
            logger.debug(f"속도 제한 대기: {wait_time:.2f}초")
            time.sleep(wait_time)

    def _reset_quota_if_new_day(self):
        """날짜가 바뀌면 쿼터 카운트를 초기화."""
        today = date.today()
        if today != self._quota_date:
            logger.info(f"날짜 변경 감지 ({self._quota_date} -> {today}). 쿼터 카운트 초기화.")
            self._total_requests_today = 0
            self._quota_date = today

    def check_remaining_quota(self) -> dict:
        """남은 일일 쿼터 정보를 반환.

        Returns:
            dict: {
                "date": 오늘 날짜,
                "total_used": 오늘 사용한 요청 수,
                "daily_quota": 일일 쿼터,
                "remaining": 남은 요청 수,
                "per_endpoint": 엔드포인트별 요청 수,
            }
        """
        self._reset_quota_if_new_day()
        return {
            "date": str(self._quota_date),
            "total_used": self._total_requests_today,
            "daily_quota": self.daily_quota,
            "remaining": max(0, self.daily_quota - self._total_requests_today),
            "per_endpoint": dict(self._request_counts),
        }

    def _parse_xml_to_dict(self, xml_text: str) -> dict:
        """XML 응답을 딕셔너리로 변환.

        Args:
            xml_text: XML 문자열

        Returns:
            파싱된 딕셔너리. body > items > item 구조를 자동 감지.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f"XML 파싱 실패: {e}")
            raise

        def element_to_dict(element):
            result = {}
            for child in element:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if len(child) > 0:
                    child_dict = element_to_dict(child)
                    if tag in result:
                        if not isinstance(result[tag], list):
                            result[tag] = [result[tag]]
                        result[tag].append(child_dict)
                    else:
                        result[tag] = child_dict
                else:
                    text = child.text.strip() if child.text else ""
                    if tag in result:
                        if not isinstance(result[tag], list):
                            result[tag] = [result[tag]]
                        result[tag].append(text)
                    else:
                        result[tag] = text
            return result

        parsed = element_to_dict(root)

        # 표준 공공데이터포털 응답 구조 처리
        if "body" in parsed:
            body = parsed["body"]
            if "items" in body:
                items = body["items"]
                if isinstance(items, dict) and "item" in items:
                    item_data = items["item"]
                    if not isinstance(item_data, list):
                        item_data = [item_data]
                    parsed["_items"] = item_data
                elif isinstance(items, str) and items == "":
                    parsed["_items"] = []
            parsed["_total_count"] = body.get("totalCount", "0")
            parsed["_page_no"] = body.get("pageNo", "1")
            parsed["_num_of_rows"] = body.get("numOfRows", "10")

        if "header" in parsed:
            parsed["_result_code"] = parsed["header"].get("resultCode", "")
            parsed["_result_msg"] = parsed["header"].get("resultMsg", "")

        return parsed

    def get(self, url: str, params: dict, response_format: str = "json") -> dict:
        """API GET 요청을 수행.

        Args:
            url: 요청 URL
            params: 쿼리 파라미터 (serviceKey는 자동 추가)
            response_format: 응답 형식 ("json" 또는 "xml")

        Returns:
            파싱된 응답 딕셔너리

        Raises:
            requests.RequestException: 모든 재시도 실패 시
        """
        self._reset_quota_if_new_day()

        # serviceKey 자동 추가
        if "serviceKey" not in params:
            params["serviceKey"] = self.data_go_kr_key

        # 엔드포인트 추적
        from urllib.parse import urlparse
        endpoint = urlparse(url).path
        self._request_counts[endpoint] += 1
        self._total_requests_today += 1

        last_exception = None

        for attempt in range(1, self.max_retries + 1):
            self._wait_for_rate_limit()

            try:
                logger.info(f"API 요청 [{attempt}/{self.max_retries}]: {url}")
                logger.debug(f"파라미터: {params}")

                response = self.session.get(url, params=params, timeout=self.timeout)
                self._last_request_time = time.time()

                response.raise_for_status()

                if response_format == "json":
                    try:
                        data = response.json()
                    except ValueError:
                        # JSON 파싱 실패 시 XML로 시도
                        logger.warning("JSON 파싱 실패, XML로 재시도")
                        data = self._parse_xml_to_dict(response.text)
                else:
                    data = self._parse_xml_to_dict(response.text)

                # 공공데이터포털 에러 코드 확인
                result_code = None
                if isinstance(data, dict):
                    # JSON 응답의 에러코드
                    header = data.get("response", {}).get("header", {})
                    result_code = header.get("resultCode", data.get("_result_code"))

                if result_code and result_code not in ("00", "0", "0000", None, ""):
                    result_msg = header.get("resultMsg", data.get("_result_msg", "Unknown"))
                    logger.warning(f"API 에러 응답: code={result_code}, msg={result_msg}")
                    if result_code in ("22", "99"):  # 서비스 키 오류, 서버 오류
                        raise requests.RequestException(f"API 오류: {result_code} - {result_msg}")

                logger.debug(f"응답 수신 완료 (attempt {attempt})")
                return data

            except requests.Timeout as e:
                last_exception = e
                logger.warning(f"요청 타임아웃 (attempt {attempt}/{self.max_retries}): {url}")
            except requests.ConnectionError as e:
                last_exception = e
                logger.warning(f"연결 오류 (attempt {attempt}/{self.max_retries}): {e}")
            except requests.HTTPError as e:
                last_exception = e
                status_code = e.response.status_code if e.response else None
                logger.warning(f"HTTP 오류 {status_code} (attempt {attempt}/{self.max_retries}): {e}")
                if status_code and 400 <= status_code < 500 and status_code != 429:
                    raise  # 클라이언트 오류는 재시도하지 않음 (429 제외)
            except ET.ParseError as e:
                last_exception = e
                logger.warning(f"응답 파싱 오류 (attempt {attempt}/{self.max_retries}): {e}")

            if attempt < self.max_retries:
                backoff = self.retry_delay * (2 ** (attempt - 1))
                logger.info(f"재시도 대기: {backoff:.1f}초")
                time.sleep(backoff)

        logger.error(f"모든 재시도 실패: {url}")
        raise requests.RequestException(f"모든 재시도 실패 ({self.max_retries}회): {last_exception}")

    def get_paginated(
        self,
        url: str,
        params: dict,
        response_format: str = "json",
        page_param: str = "pageNo",
        size_param: str = "numOfRows",
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list:
        """페이지네이션된 API를 순회하며 모든 아이템을 수집.

        Args:
            url: 요청 URL
            params: 기본 쿼리 파라미터
            response_format: 응답 형식
            page_param: 페이지 번호 파라미터명
            size_param: 페이지 크기 파라미터명
            page_size: 페이지당 아이템 수
            max_pages: 최대 페이지 수

        Returns:
            수집된 모든 아이템의 리스트
        """
        all_items = []
        params = dict(params)
        params[size_param] = page_size

        for page in range(1, max_pages + 1):
            params[page_param] = page

            try:
                data = self.get(url, params, response_format)
            except requests.RequestException as e:
                logger.error(f"페이지 {page} 요청 실패: {e}")
                break

            # 아이템 추출
            items = self._extract_items(data, response_format)

            if not items:
                logger.info(f"페이지 {page}: 더 이상 데이터 없음. 수집 종료.")
                break

            all_items.extend(items)

            # 전체 개수 확인
            total_count = self._extract_total_count(data, response_format)
            if total_count is not None and len(all_items) >= total_count:
                logger.info(f"전체 {total_count}건 수집 완료.")
                break

            logger.info(f"페이지 {page}: {len(items)}건 수집 (누적 {len(all_items)}건)")

        logger.info(f"페이지네이션 수집 완료: 총 {len(all_items)}건")
        return all_items

    def _extract_items(self, data: dict, response_format: str) -> list:
        """응답에서 아이템 리스트를 추출."""
        if response_format == "xml" or "_items" in data:
            return data.get("_items", [])

        # JSON 표준 구조: response > body > items > item
        try:
            body = data.get("response", {}).get("body", {})
            items = body.get("items", {})
            if isinstance(items, dict):
                item_data = items.get("item", [])
                if isinstance(item_data, dict):
                    return [item_data]
                return item_data if isinstance(item_data, list) else []
            elif isinstance(items, list):
                return items
            return []
        except (AttributeError, TypeError):
            return []

    def _extract_total_count(self, data: dict, response_format: str) -> int | None:
        """응답에서 전체 건수를 추출."""
        if "_total_count" in data:
            try:
                return int(data["_total_count"])
            except (ValueError, TypeError):
                return None

        try:
            total = data.get("response", {}).get("body", {}).get("totalCount")
            return int(total) if total is not None else None
        except (AttributeError, TypeError, ValueError):
            return None
