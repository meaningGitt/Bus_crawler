"""
교통 서비스 변경 공지 수집
- 서울 도시철도 운행 변경 공지
- 서울 버스 노선 변경 공지
- 노선 변경, 임시 중단, 우회, 시간표 변경 감지
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup
import yaml

from collectors.api_client import ApiClient

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 공지 수집 대상 URL
NOTICE_SOURCES = {
    "서울메트로": {
        "url": "https://www.seoulmetro.co.kr/kr/board.do?menuIdx=551",
        "name": "서울교통공사 공지사항",
        "type": "subway",
    },
    "서울버스": {
        "url": "https://bus.go.kr/noticeList",
        "name": "서울시 버스정보시스템 공지",
        "type": "bus",
    },
    "경기버스": {
        "url": "https://www.gbis.go.kr/gbis2014/schNotice.action",
        "name": "경기도 버스정보 공지",
        "type": "bus",
    },
}

# 서비스 변경 관련 키워드
CHANGE_KEYWORDS = [
    "노선변경", "노선 변경", "운행변경", "운행 변경",
    "임시운행", "임시 운행", "우회운행", "우회 운행",
    "운행중단", "운행 중단", "폐선", "신설",
    "시간표", "첫차", "막차", "배차간격",
    "환승", "정류장 이전", "역명 변경",
    "감차", "증차", "단축", "연장",
]


class ServiceUpdatesCollector:
    """교통 서비스 변경 공지 수집기."""

    OUTPUT_PATH = Path("data/new/service_updates.csv")

    def __init__(self, config: dict | None = None):
        if config is None:
            config_path = Path("config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    full_config = yaml.safe_load(f)
                config = full_config.get("collection", {})
            else:
                config = {}

        self.config = config
        self.timeout = config.get("timeout_seconds", 30)
        self.user_agent = config.get("user_agent", "AnjeulJido-DataCollector/1.0")
        self.results: list[dict] = []

    def _is_service_change(self, title: str) -> bool:
        """제목이 서비스 변경 관련인지 판별."""
        return any(kw in title for kw in CHANGE_KEYWORDS)

    def _classify_notice_type(self, title: str) -> str:
        """공지 유형을 분류."""
        if any(kw in title for kw in ["노선변경", "노선 변경", "신설", "폐선"]):
            return "route_change"
        if any(kw in title for kw in ["운행중단", "운행 중단", "임시"]):
            return "temporary_suspension"
        if any(kw in title for kw in ["우회", "우회운행"]):
            return "detour"
        if any(kw in title for kw in ["시간표", "첫차", "막차", "배차"]):
            return "schedule_change"
        if any(kw in title for kw in ["환승"]):
            return "transfer_change"
        return "other"

    def _extract_affected_routes(self, text: str) -> str:
        """텍스트에서 영향받는 노선을 추출."""
        # 버스 노선번호 패턴 (예: 143번, 간선01, 지선02, N13)
        bus_patterns = re.findall(r'[간선지선광역급행N]?\d{1,4}[번호]?[-]?\d*', text)
        # 지하철 호선 패턴
        subway_patterns = re.findall(r'\d호선', text)

        all_routes = list(set(bus_patterns + subway_patterns))
        return ", ".join(all_routes[:10]) if all_routes else ""

    def _scrape_notice_page(self, source_key: str, source_config: dict) -> list[dict]:
        """공지 페이지를 스크래핑하여 서비스 변경 공지를 추출.

        Args:
            source_key: 소스 키
            source_config: 소스 설정

        Returns:
            공지 딕셔너리 리스트
        """
        url = source_config["url"]
        now = datetime.now(KST)
        notices = []

        try:
            response = requests.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"

            soup = BeautifulSoup(response.text, "lxml")

            # 공통 패턴: 게시판 테이블의 행, 또는 리스트 아이템
            # 여러 패턴 시도
            rows = (
                soup.select("table tbody tr") or
                soup.select(".board-list li") or
                soup.select(".list-group-item") or
                soup.select(".notice-list li") or
                soup.select("ul.list li")
            )

            for row in rows:
                # 제목 추출
                title_elem = (
                    row.select_one("td.title a") or
                    row.select_one("a.title") or
                    row.select_one("a") or
                    row.select_one("td:nth-child(2) a")
                )

                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)

                if not self._is_service_change(title):
                    continue

                # 링크 추출
                link = title_elem.get("href", "")
                if link and not link.startswith("http"):
                    from urllib.parse import urljoin
                    link = urljoin(url, link)

                # 날짜 추출
                date_elem = row.select_one("td.date") or row.select_one(".date")
                post_date = date_elem.get_text(strip=True) if date_elem else ""

                notices.append({
                    "source": source_key,
                    "notice_type": self._classify_notice_type(title),
                    "title": title,
                    "description": "",
                    "affected_routes": self._extract_affected_routes(title),
                    "start_date": "",
                    "end_date": "",
                    "notice_url": link,
                    "collected_at": now.isoformat(),
                })

            logger.info(f"{source_key}: {len(notices)}건의 서비스 변경 공지 발견")

        except requests.RequestException as e:
            logger.warning(f"{source_key} 스크래핑 실패: {e}")
        except Exception as e:
            logger.error(f"{source_key} 처리 오류: {e}", exc_info=True)

        return notices

    def collect(self) -> pd.DataFrame:
        """서비스 변경 공지를 수집.

        Returns:
            수집된 공지 DataFrame
        """
        logger.info("서비스 변경 공지 수집 시작")
        self.results = []

        for source_key, source_config in NOTICE_SOURCES.items():
            logger.info(f"수집 중: {source_config['name']} ({source_config['url']})")
            notices = self._scrape_notice_page(source_key, source_config)
            self.results.extend(notices)

        df = pd.DataFrame(self.results)

        # 빈 DataFrame이더라도 컬럼 구조는 유지
        if df.empty:
            df = pd.DataFrame(columns=[
                "source", "notice_type", "title", "description",
                "affected_routes", "start_date", "end_date",
                "notice_url", "collected_at",
            ])
            logger.info("수집된 서비스 변경 공지가 없습니다. (정상일 수 있음)")
        else:
            logger.info(f"총 {len(df)}건의 서비스 변경 공지 수집됨")

        self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.OUTPUT_PATH, index=False, encoding="utf-8-sig")
        logger.info(f"공지 저장 완료: {self.OUTPUT_PATH}")

        return df

    def validate(self, df: pd.DataFrame | None = None) -> dict:
        """수집된 데이터의 품질을 검증."""
        if df is None:
            if self.OUTPUT_PATH.exists():
                df = pd.read_csv(self.OUTPUT_PATH)
            else:
                return {"valid": False, "error": "데이터 파일 없음"}

        required_columns = [
            "source", "notice_type", "title", "notice_url", "collected_at",
        ]
        missing_cols = [c for c in required_columns if c not in df.columns]

        issues = []
        if missing_cols:
            issues.append(f"누락 컬럼: {missing_cols}")

        # 공지 유형 분포 확인
        type_counts = df["notice_type"].value_counts().to_dict() if "notice_type" in df.columns else {}

        result = {
            "valid": len(issues) == 0,
            "total_records": len(df),
            "notice_type_counts": type_counts,
            "sources": sorted(df["source"].unique().tolist()) if "source" in df.columns else [],
            "issues": issues,
            "note": "스크래핑 기반 수집으로, 웹페이지 구조 변경 시 수집 실패 가능",
        }
        logger.info(f"검증 결과: {result}")
        return result
