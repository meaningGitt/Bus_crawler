"""
P1-C: 사용자 불편사항 수집 (버스/환승/막차 관련)
- 기존 subway-data-quality 프로젝트의 수집을 확장
- 네이버 검색 API를 이용하여 버스/환승/막차 관련 불편사항 수집
- 기존 데이터와 중복 제거
"""

import hashlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv
import yaml

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 검색 키워드 (기존 프로젝트에서 다루지 않는 버스/환승/막차 관련)
SEARCH_QUERIES = [
    # 버스 관련
    "버스 막차 놓침",
    "버스 막차 시간 불확실",
    "심야버스 운행 안함",
    "버스 배차간격 불규칙",
    "버스 막차 정보 부정확",
    "버스 막차 시간표 오류",
    # 환승 관련
    "지하철 버스 환승 실패",
    "환승 시간 부족 막차",
    "지하철 막차 환승 버스",
    "환승역 버스 연결 불편",
    "막차 환승 동선",
    # 막차 관련
    "지하철 막차 중간 종착",
    "막차 행선지 혼동",
    "막차 시간 역별 다름",
    "막차 놓치고 택시",
    "심야 교통 대안 없음",
    # 출발시간 불확실
    "버스 도착 예정 시간 부정확",
    "버스 언제 오는지 모름",
    "정류장 버스 정보 없음",
]


class UserPainPointsCollector:
    """사용자 불편사항 수집기."""

    OUTPUT_PATH = Path("data/new/new_user_pain_points.csv")
    EXISTING_DATA_DIR = Path.home() / "subway-data-quality" / "data" / "processed"

    # 네이버 검색 API
    NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"

    def __init__(self, config: dict | None = None):
        load_dotenv()

        if config is None:
            config_path = Path("config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    full_config = yaml.safe_load(f)
                config = full_config.get("collection", {})
            else:
                config = {}

        self.config = config
        self.naver_client_id = os.getenv("NAVER_CLIENT_ID", "")
        self.naver_client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
        self.results: list[dict] = []
        self._existing_hashes: set[str] = set()

    def _load_existing_hashes(self):
        """기존 수집 데이터의 해시값을 로드하여 중복 제거에 사용."""
        self._existing_hashes = set()

        # 기존 프로젝트 데이터
        if self.EXISTING_DATA_DIR.exists():
            for csv_file in self.EXISTING_DATA_DIR.glob("*.csv"):
                try:
                    df = pd.read_csv(csv_file, encoding="utf-8")
                    if "original_url" in df.columns:
                        for url in df["original_url"].dropna():
                            self._existing_hashes.add(self._hash_url(str(url)))
                    if "evidence_text" in df.columns:
                        for text in df["evidence_text"].dropna():
                            self._existing_hashes.add(self._hash_text(str(text)))
                except Exception as e:
                    logger.warning(f"기존 데이터 로드 실패 ({csv_file}): {e}")

        # 이전 수집 데이터
        if self.OUTPUT_PATH.exists():
            try:
                df = pd.read_csv(self.OUTPUT_PATH, encoding="utf-8-sig")
                if "original_url" in df.columns:
                    for url in df["original_url"].dropna():
                        self._existing_hashes.add(self._hash_url(str(url)))
            except Exception:
                pass

        logger.info(f"기존 데이터 해시 {len(self._existing_hashes)}개 로드됨")

    def _hash_url(self, url: str) -> str:
        """URL 해시 생성."""
        return hashlib.md5(url.strip().encode()).hexdigest()

    def _hash_text(self, text: str) -> str:
        """텍스트 해시 생성 (정규화 후)."""
        normalized = re.sub(r'\s+', '', text.strip())
        return hashlib.md5(normalized[:200].encode()).hexdigest()

    def _is_duplicate(self, url: str, text: str) -> bool:
        """중복 여부 확인."""
        return (self._hash_url(url) in self._existing_hashes or
                self._hash_text(text) in self._existing_hashes)

    def _classify_travel_type(self, text: str) -> str:
        """불편사항의 이동 유형을 분류."""
        if any(kw in text for kw in ["버스", "정류장", "배차"]):
            if any(kw in text for kw in ["지하철", "환승", "전철"]):
                return "bus_subway_transfer"
            return "bus"
        if any(kw in text for kw in ["환승"]):
            return "transfer"
        if any(kw in text for kw in ["막차", "심야", "마지막 열차"]):
            return "last_train"
        return "general_transit"

    def _classify_pain_situation(self, text: str) -> str:
        """불편 상황을 분류."""
        if any(kw in text for kw in ["놓침", "놓쳤", "놓치"]):
            return "missed_service"
        if any(kw in text for kw in ["몰랐", "불확실", "모르", "정보 없"]):
            return "information_gap"
        if any(kw in text for kw in ["환승 실패", "연결 안", "환승 못"]):
            return "transfer_failure"
        if any(kw in text for kw in ["대기", "기다", "오래 걸"]):
            return "long_wait"
        if any(kw in text for kw in ["택시", "대안", "대체"]):
            return "no_alternative"
        return "other"

    def _remove_pii(self, text: str) -> str:
        """개인정보를 제거."""
        # 전화번호
        text = re.sub(r'01[0-9]-?\d{3,4}-?\d{4}', '[전화번호]', text)
        # 이메일
        text = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '[이메일]', text)
        # 이름 패턴 (한글 2-4자 + 님/씨)
        text = re.sub(r'[가-힣]{2,4}(님|씨)', '[이름]', text)
        return text

    def _search_naver(self, query: str, display: int = 20) -> list[dict]:
        """네이버 검색 API로 블로그 검색.

        Args:
            query: 검색어
            display: 결과 수

        Returns:
            검색 결과 리스트
        """
        if not self.naver_client_id or not self.naver_client_secret:
            logger.warning("네이버 API 키 미설정")
            return []

        headers = {
            "X-Naver-Client-Id": self.naver_client_id,
            "X-Naver-Client-Secret": self.naver_client_secret,
        }
        params = {
            "query": query,
            "display": display,
            "sort": "date",
        }

        try:
            response = requests.get(
                self.NAVER_SEARCH_URL,
                headers=headers,
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("items", [])
        except Exception as e:
            logger.warning(f"네이버 검색 실패 (query='{query}'): {e}")
            return []

    def _clean_html(self, text: str) -> str:
        """HTML 태그를 제거."""
        return re.sub(r'<[^>]+>', '', text)

    def collect(self) -> pd.DataFrame:
        """불편사항을 수집.

        Returns:
            수집된 불편사항 DataFrame
        """
        if not self.naver_client_id or not self.naver_client_secret:
            logger.warning("네이버 API 키 미설정 (NAVER_CLIENT_ID, NAVER_CLIENT_SECRET). "
                           "불편사항 수집을 건너뜁니다.")
            return pd.DataFrame()

        logger.info("사용자 불편사항 수집 시작")
        self._load_existing_hashes()
        self.results = []
        now = datetime.now(KST)

        for query in SEARCH_QUERIES:
            logger.info(f"검색 중: '{query}'")
            items = self._search_naver(query, display=20)

            for item in items:
                url = item.get("link", item.get("bloggerlink", ""))
                title = self._clean_html(item.get("title", ""))
                description = self._clean_html(item.get("description", ""))
                post_date = item.get("postdate", "")

                evidence_text = f"{title}. {description}"

                # 중복 확인
                if self._is_duplicate(url, evidence_text):
                    continue

                # PII 제거
                evidence_text = self._remove_pii(evidence_text)

                # 분류
                travel_type = self._classify_travel_type(evidence_text)
                pain_situation = self._classify_pain_situation(evidence_text)

                self.results.append({
                    "source_platform": "naver_blog",
                    "original_url": url,
                    "post_date": post_date,
                    "collected_at": now.isoformat(),
                    "travel_type": travel_type,
                    "user_type": "NOT_VERIFIED",
                    "pain_situation": pain_situation,
                    "pain_cause": "NOT_VERIFIED",
                    "coping_behavior": "",
                    "evidence_text": evidence_text[:500],
                    "verification_status": "unverified",
                })

                # 해시 추가 (같은 배치 내 중복 방지)
                self._existing_hashes.add(self._hash_url(url))
                self._existing_hashes.add(self._hash_text(evidence_text))

        df = pd.DataFrame(self.results)

        if not df.empty:
            self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(self.OUTPUT_PATH, index=False, encoding="utf-8-sig")
            logger.info(f"불편사항 저장: {self.OUTPUT_PATH} ({len(df)}건)")
        else:
            logger.info("수집된 신규 불편사항이 없습니다.")

        return df

    def validate(self, df: pd.DataFrame | None = None) -> dict:
        """수집된 데이터의 품질을 검증."""
        if df is None:
            if self.OUTPUT_PATH.exists():
                df = pd.read_csv(self.OUTPUT_PATH, encoding="utf-8-sig")
            else:
                return {"valid": False, "error": "데이터 파일 없음"}

        if df.empty:
            return {"valid": True, "total_records": 0, "note": "신규 수집 건이 없을 수 있음", "issues": []}

        required_columns = [
            "source_platform", "original_url", "collected_at",
            "travel_type", "pain_situation", "evidence_text",
        ]
        missing_cols = [c for c in required_columns if c not in df.columns]

        issues = []
        if missing_cols:
            issues.append(f"누락 컬럼: {missing_cols}")

        # 중복 URL 확인
        if "original_url" in df.columns:
            dupes = df["original_url"].duplicated().sum()
            if dupes > 0:
                issues.append(f"중복 URL: {dupes}건")

        # PII 잔류 확인
        if "evidence_text" in df.columns:
            pii_patterns = [r'01[0-9]-?\d{3,4}-?\d{4}', r'[\w.+-]+@[\w-]+\.[\w.-]+']
            pii_found = 0
            for text in df["evidence_text"].dropna():
                for pattern in pii_patterns:
                    if re.search(pattern, str(text)):
                        pii_found += 1
                        break
            if pii_found > 0:
                issues.append(f"PII 잔류 의심: {pii_found}건")

        # 유형별 분포
        type_counts = df["travel_type"].value_counts().to_dict() if "travel_type" in df.columns else {}

        result = {
            "valid": len(issues) == 0,
            "total_records": len(df),
            "travel_type_counts": type_counts,
            "issues": issues,
        }
        logger.info(f"검증 결과: {result}")
        return result
