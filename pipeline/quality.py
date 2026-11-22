"""
데이터 품질 검증 모듈
- CSV 파일별 품질 규칙 검증
- 필수 필드, 중복, 형식, GPS 범위, ID 일관성, 연결 무결성 검사
- 문제 레코드 격리(quarantine) 및 보고서 생성
"""

import csv
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

# 한국 GPS 좌표 범위
KOREA_LAT_MIN = 33.0
KOREA_LAT_MAX = 39.0
KOREA_LON_MIN = 124.0
KOREA_LON_MAX = 132.0

# 데이터 타입별 필수 필드
DEFAULT_REQUIRED_FIELDS = {
    "subway_last_train": [
        "line", "station_code", "station_name", "direction",
        "day_type", "last_train_time", "destination",
    ],
    "bus_routes": [
        "region", "route_id", "route_name", "route_type",
        "start_stop", "end_stop",
    ],
    "bus_stops": [
        "region", "stop_id", "stop_name", "latitude", "longitude",
    ],
    "bus_route_stops": [
        "region", "route_id", "stop_id", "stop_sequence", "direction",
    ],
    "bus_service_hours": [
        "region", "route_id", "day_type", "first_bus", "last_bus",
    ],
    "subway_transfer": [
        "transfer_station_name", "from_line", "to_line",
    ],
}

# 데이터 타입과 파일 이름 매핑
DATA_TYPE_FILE_MAP = {
    "subway_last_train": "subway_last_train_supplement.csv",
    "bus_routes": "bus_routes.csv",
    "bus_stops": "bus_stops.csv",
    "bus_route_stops": "bus_route_stops.csv",
    "bus_service_hours": "bus_service_hours.csv",
    "subway_transfer": "subway_transfer_connections.csv",
}

# HH:MM:SS 또는 HH:MM 패턴
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


class DataQualityValidator:
    """데이터 품질 검증기."""

    def __init__(self, config: dict):
        """
        Args:
            config: 전체 설정 딕셔너리 (config.yaml 로드 결과)
        """
        self.config = config
        self.issues: list[dict] = []

        quality_config = config.get("quality", {})
        self.required_fields = quality_config.get("required_fields", {})
        # config.yaml의 필드명과 내부 매핑 통합
        self._required_fields_map = {**DEFAULT_REQUIRED_FIELDS}
        if "bus_route" in self.required_fields:
            self._required_fields_map["bus_routes"] = self.required_fields["bus_route"]
        if "bus_stop" in self.required_fields:
            self._required_fields_map["bus_stops"] = self.required_fields["bus_stop"]
        if "bus_route_stop" in self.required_fields:
            self._required_fields_map["bus_route_stops"] = self.required_fields["bus_route_stop"]
        if "bus_service_hours" in self.required_fields:
            self._required_fields_map["bus_service_hours"] = self.required_fields["bus_service_hours"]
        if "subway_last_train" in self.required_fields:
            self._required_fields_map["subway_last_train"] = self.required_fields["subway_last_train"]
        if "subway_transfer" in self.required_fields:
            self._required_fields_map["subway_transfer"] = self.required_fields["subway_transfer"]

        output_config = config.get("output", {})
        self.new_data_dir = Path(output_config.get("new_data_dir", "data/new"))
        self.quarantine_dir = Path(output_config.get("quarantine_dir", "data/quarantine"))
        self.reports_dir = Path(output_config.get("reports_dir", "data/reports"))

    def _check_required_fields(self, df: pd.DataFrame, data_type: str) -> list[dict]:
        """필수 필드 존재 여부 및 null 값 검사."""
        issues = []
        required = self._required_fields_map.get(data_type, [])

        # 컬럼 존재 여부
        missing_cols = [col for col in required if col not in df.columns]
        if missing_cols:
            issues.append({
                "field": ",".join(missing_cols),
                "issue_type": "missing_column",
                "count": len(missing_cols),
                "examples": missing_cols[:5],
            })

        # null 값 검사
        for col in required:
            if col in df.columns:
                null_count = df[col].isna().sum()
                empty_count = (df[col].astype(str).str.strip() == "").sum()
                total_missing = null_count + empty_count
                if total_missing > 0:
                    examples = df[df[col].isna() | (df[col].astype(str).str.strip() == "")].index.tolist()[:5]
                    issues.append({
                        "field": col,
                        "issue_type": "null_or_empty",
                        "count": int(total_missing),
                        "examples": [f"row_{i}" for i in examples],
                    })

        return issues

    def _check_duplicates(self, df: pd.DataFrame, data_type: str) -> list[dict]:
        """중복 행 검사."""
        issues = []
        dup_count = df.duplicated().sum()
        if dup_count > 0:
            dup_indices = df[df.duplicated()].index.tolist()[:5]
            issues.append({
                "field": "_all_columns",
                "issue_type": "duplicate_row",
                "count": int(dup_count),
                "examples": [f"row_{i}" for i in dup_indices],
            })
        return issues

    def _check_time_format(self, df: pd.DataFrame, columns: list[str]) -> list[dict]:
        """시간 형식(HH:MM:SS 또는 HH:MM) 검사."""
        issues = []
        for col in columns:
            if col not in df.columns:
                continue
            non_null = df[col].dropna()
            non_empty = non_null[non_null.astype(str).str.strip() != ""]
            if non_empty.empty:
                continue
            invalid_mask = ~non_empty.astype(str).str.match(r"^\d{2}:\d{2}(:\d{2})?$")
            invalid_count = invalid_mask.sum()
            if invalid_count > 0:
                bad_values = non_empty[invalid_mask].head(5).tolist()
                issues.append({
                    "field": col,
                    "issue_type": "invalid_time_format",
                    "count": int(invalid_count),
                    "examples": [str(v) for v in bad_values],
                })
        return issues

    def _check_gps_range(self, df: pd.DataFrame) -> list[dict]:
        """GPS 좌표가 한국 범위(lat 33-39, lon 124-132) 내인지 검사."""
        issues = []
        lat_col = None
        lon_col = None
        for c in ["latitude", "lat", "gpsLati", "y"]:
            if c in df.columns:
                lat_col = c
                break
        for c in ["longitude", "lon", "lng", "gpsLong", "x"]:
            if c in df.columns:
                lon_col = c
                break

        if lat_col:
            try:
                lat_vals = pd.to_numeric(df[lat_col], errors="coerce")
                out_of_range = ((lat_vals < KOREA_LAT_MIN) | (lat_vals > KOREA_LAT_MAX)) & lat_vals.notna()
                count = out_of_range.sum()
                if count > 0:
                    bad_vals = lat_vals[out_of_range].head(5).tolist()
                    issues.append({
                        "field": lat_col,
                        "issue_type": "gps_out_of_range",
                        "count": int(count),
                        "examples": [str(v) for v in bad_vals],
                    })
            except Exception:
                pass

        if lon_col:
            try:
                lon_vals = pd.to_numeric(df[lon_col], errors="coerce")
                out_of_range = ((lon_vals < KOREA_LON_MIN) | (lon_vals > KOREA_LON_MAX)) & lon_vals.notna()
                count = out_of_range.sum()
                if count > 0:
                    bad_vals = lon_vals[out_of_range].head(5).tolist()
                    issues.append({
                        "field": lon_col,
                        "issue_type": "gps_out_of_range",
                        "count": int(count),
                        "examples": [str(v) for v in bad_vals],
                    })
            except Exception:
                pass

        return issues

    def _check_id_format(self, df: pd.DataFrame, data_type: str) -> list[dict]:
        """ID 형식 일관성 검사 (예: SEL_xxx, GGI_xxx, ICN_xxx)."""
        issues = []
        id_col = None
        if data_type in ("bus_routes", "bus_route_stops", "bus_service_hours"):
            id_col = "route_id"
        elif data_type == "bus_stops":
            id_col = "stop_id"

        if id_col and id_col in df.columns:
            # 지역 접두사가 있는 ID의 형식 일관성 검사
            id_vals = df[id_col].dropna().astype(str)
            prefixed = id_vals[id_vals.str.contains("_", na=False)]
            if not prefixed.empty:
                valid_prefixes = {"SEL_", "GGI_", "ICN_"}
                invalid_prefix = prefixed[~prefixed.str[:4].isin(valid_prefixes)]
                if len(invalid_prefix) > 0:
                    issues.append({
                        "field": id_col,
                        "issue_type": "invalid_id_prefix",
                        "count": len(invalid_prefix),
                        "examples": invalid_prefix.head(5).tolist(),
                    })
        return issues

    def _check_subway_last_train(self, df: pd.DataFrame) -> list[dict]:
        """subway_last_train 전용 품질 검사."""
        issues = []

        # direction이 line에 맞는지 검사 (2호선만 내선/외선 허용)
        if "direction" in df.columns and "line" in df.columns:
            line2 = df[df["line"] == "2호선"]
            if not line2.empty:
                invalid_dir = line2[~line2["direction"].isin(["내선", "외선"])]
                if len(invalid_dir) > 0:
                    issues.append({
                        "field": "direction",
                        "issue_type": "invalid_direction_for_line2",
                        "count": len(invalid_dir),
                        "examples": invalid_dir["direction"].unique().tolist()[:5],
                    })

            other_lines = df[df["line"] != "2호선"]
            if not other_lines.empty:
                invalid_dir = other_lines[other_lines["direction"].isin(["내선", "외선"])]
                if len(invalid_dir) > 0:
                    issues.append({
                        "field": "direction",
                        "issue_type": "inner_outer_on_non_line2",
                        "count": len(invalid_dir),
                        "examples": invalid_dir[["line", "direction"]].head(5).values.tolist(),
                    })

        # last_train_time 형식 검사
        issues.extend(self._check_time_format(df, ["last_train_time"]))

        # day_type 유효값 검사
        if "day_type" in df.columns:
            valid_day_types = {"평일", "주말", "공휴일"}
            invalid = df[~df["day_type"].isin(valid_day_types)]
            if len(invalid) > 0:
                issues.append({
                    "field": "day_type",
                    "issue_type": "invalid_day_type",
                    "count": len(invalid),
                    "examples": invalid["day_type"].unique().tolist()[:5],
                })

        return issues

    def _check_bus_routes(self, df: pd.DataFrame) -> list[dict]:
        """bus_routes 전용 품질 검사."""
        issues = []

        # route_id 비어있는지
        if "route_id" in df.columns:
            empty_ids = df["route_id"].isna().sum() + (df["route_id"].astype(str).str.strip() == "").sum()
            if empty_ids > 0:
                issues.append({
                    "field": "route_id",
                    "issue_type": "empty_route_id",
                    "count": int(empty_ids),
                    "examples": [],
                })

        # region 유효값 검사
        if "region" in df.columns:
            valid_regions = {"서울", "경기", "인천"}
            invalid = df[~df["region"].isin(valid_regions)]
            if len(invalid) > 0:
                issues.append({
                    "field": "region",
                    "issue_type": "invalid_region",
                    "count": len(invalid),
                    "examples": invalid["region"].unique().tolist()[:5],
                })

        return issues

    def _check_bus_stops(self, df: pd.DataFrame) -> list[dict]:
        """bus_stops 전용 품질 검사."""
        return self._check_gps_range(df)

    def _check_bus_route_stops(self, df: pd.DataFrame) -> list[dict]:
        """bus_route_stops 전용 품질 검사: stop_sequence 단조 증가 확인."""
        issues = []
        if "stop_sequence" not in df.columns or "route_id" not in df.columns:
            return issues

        non_monotonic_routes = 0
        example_routes = []

        group_cols = ["route_id"]
        if "direction" in df.columns:
            group_cols.append("direction")

        for group_key, group in df.groupby(group_cols):
            try:
                seqs = group["stop_sequence"].astype(int).tolist()
                # 단조 증가 확인
                if any(seqs[i] >= seqs[i + 1] for i in range(len(seqs) - 1)):
                    non_monotonic_routes += 1
                    route_name = group_key if isinstance(group_key, str) else str(group_key)
                    if len(example_routes) < 5:
                        example_routes.append(route_name)
            except (ValueError, TypeError):
                continue

        if non_monotonic_routes > 0:
            issues.append({
                "field": "stop_sequence",
                "issue_type": "non_monotonic_sequence",
                "count": non_monotonic_routes,
                "examples": example_routes,
            })

        # 노선-정류장 연결 무결성: route_id가 bus_routes에 존재하는지
        bus_routes_path = self.new_data_dir / "bus_routes.csv"
        if bus_routes_path.exists() and "route_id" in df.columns:
            try:
                routes_df = pd.read_csv(bus_routes_path)
                if "route_id" in routes_df.columns:
                    valid_route_ids = set(routes_df["route_id"].astype(str))
                    rs_route_ids = set(df["route_id"].astype(str))
                    orphan_ids = rs_route_ids - valid_route_ids
                    if orphan_ids:
                        issues.append({
                            "field": "route_id",
                            "issue_type": "orphan_route_id",
                            "count": len(orphan_ids),
                            "examples": list(orphan_ids)[:5],
                        })
            except Exception as e:
                logger.warning(f"bus_routes.csv 로드 실패 (연결 검증 생략): {e}")

        return issues

    def _check_bus_service_hours(self, df: pd.DataFrame) -> list[dict]:
        """bus_service_hours 전용 품질 검사."""
        issues = []

        # 시간 형식 검사
        issues.extend(self._check_time_format(df, ["first_bus", "last_bus"]))

        # first_bus < last_bus 검사 (심야 버스 제외)
        if "first_bus" in df.columns and "last_bus" in df.columns:
            valid_rows = df.dropna(subset=["first_bus", "last_bus"])
            valid_rows = valid_rows[
                (valid_rows["first_bus"].astype(str).str.strip() != "") &
                (valid_rows["last_bus"].astype(str).str.strip() != "")
            ]
            if not valid_rows.empty:
                # HH:MM 형식인 경우만 비교
                time_valid = valid_rows[
                    valid_rows["first_bus"].astype(str).str.match(r"^\d{2}:\d{2}") &
                    valid_rows["last_bus"].astype(str).str.match(r"^\d{2}:\d{2}")
                ]
                if not time_valid.empty:
                    first_hours = time_valid["first_bus"].astype(str).str[:2].astype(int)
                    last_hours = time_valid["last_bus"].astype(str).str[:2].astype(int)
                    # 심야 운행이 아닌데 first >= last인 경우 (심야: last_bus 00~04시)
                    invalid_order = time_valid[
                        (first_hours >= last_hours) &
                        (last_hours >= 5)  # 00~04시가 아닌 경우에만 오류로 판단
                    ]
                    if len(invalid_order) > 0:
                        examples = invalid_order[["route_id", "first_bus", "last_bus"]].head(5).values.tolist()
                        issues.append({
                            "field": "first_bus/last_bus",
                            "issue_type": "first_after_last",
                            "count": len(invalid_order),
                            "examples": [str(e) for e in examples],
                        })

        return issues

    def _check_subway_transfer(self, df: pd.DataFrame) -> list[dict]:
        """subway_transfer 전용 품질 검사."""
        issues = []

        # from_line != to_line
        if "from_line" in df.columns and "to_line" in df.columns:
            same_line = df[df["from_line"] == df["to_line"]]
            if len(same_line) > 0:
                examples = same_line[["transfer_station_name", "from_line", "to_line"]].head(5).values.tolist()
                issues.append({
                    "field": "from_line/to_line",
                    "issue_type": "same_line_transfer",
                    "count": len(same_line),
                    "examples": [str(e) for e in examples],
                })

        return issues

    def validate_file(self, filepath: str, data_type: str) -> dict:
        """CSV 파일을 지정된 data_type의 품질 규칙에 따라 검증.

        Args:
            filepath: CSV 파일 경로
            data_type: 데이터 유형 (subway_last_train, bus_routes, etc.)

        Returns:
            {total_rows, valid_rows, issues: [{field, issue_type, count, examples}]}
        """
        filepath = Path(filepath)
        if not filepath.exists():
            return {
                "filepath": str(filepath),
                "data_type": data_type,
                "total_rows": 0,
                "valid_rows": 0,
                "issues": [{"field": "_file", "issue_type": "file_not_found", "count": 1, "examples": [str(filepath)]}],
            }

        try:
            df = pd.read_csv(filepath)
        except Exception as e:
            return {
                "filepath": str(filepath),
                "data_type": data_type,
                "total_rows": 0,
                "valid_rows": 0,
                "issues": [{"field": "_file", "issue_type": "read_error", "count": 1, "examples": [str(e)]}],
            }

        all_issues = []

        # 공통 검사
        all_issues.extend(self._check_required_fields(df, data_type))
        all_issues.extend(self._check_duplicates(df, data_type))
        all_issues.extend(self._check_id_format(df, data_type))

        # 데이터 타입별 전용 검사
        type_checks = {
            "subway_last_train": self._check_subway_last_train,
            "bus_routes": self._check_bus_routes,
            "bus_stops": self._check_bus_stops,
            "bus_route_stops": self._check_bus_route_stops,
            "bus_service_hours": self._check_bus_service_hours,
            "subway_transfer": self._check_subway_transfer,
        }

        if data_type in type_checks:
            all_issues.extend(type_checks[data_type](df))

        # 문제가 있는 행 수 계산
        problem_row_count = 0
        for issue in all_issues:
            if issue["issue_type"] not in ("missing_column", "file_not_found", "read_error"):
                problem_row_count += issue["count"]
        # 중복 카운팅 방지를 위해 total_rows 이하로 제한
        problem_row_count = min(problem_row_count, len(df))

        self.issues.extend(all_issues)

        result = {
            "filepath": str(filepath),
            "data_type": data_type,
            "total_rows": len(df),
            "valid_rows": len(df) - problem_row_count,
            "issues": all_issues,
        }

        logger.info(
            f"검증 완료: {filepath.name} ({data_type}) - "
            f"총 {len(df)}행, 이슈 {len(all_issues)}건"
        )

        return result

    def validate_all(self) -> dict:
        """data/new/ 디렉토리의 모든 파일을 검증.

        Returns:
            {files: [{filepath, data_type, total_rows, valid_rows, issues}], summary: {...}}
        """
        results = []

        for data_type, filename in DATA_TYPE_FILE_MAP.items():
            filepath = self.new_data_dir / filename
            if filepath.exists():
                result = self.validate_file(str(filepath), data_type)
                results.append(result)
            else:
                logger.debug(f"파일 없음 (건너뜀): {filepath}")

        total_rows = sum(r["total_rows"] for r in results)
        valid_rows = sum(r["valid_rows"] for r in results)
        total_issues = sum(len(r["issues"]) for r in results)

        summary = {
            "files_checked": len(results),
            "total_rows": total_rows,
            "valid_rows": valid_rows,
            "total_issues": total_issues,
            "validation_time": datetime.now().isoformat(),
        }

        logger.info(
            f"전체 검증 완료: {len(results)}개 파일, "
            f"{total_rows}행 중 {valid_rows}행 유효, "
            f"이슈 {total_issues}건"
        )

        return {"files": results, "summary": summary}

    def quarantine(self, filepath: str, issues: list[dict]):
        """문제가 있는 레코드를 data/quarantine/으로 이동.

        Args:
            filepath: 원본 CSV 파일 경로
            issues: 발견된 이슈 리스트
        """
        filepath = Path(filepath)
        if not filepath.exists():
            logger.warning(f"격리 대상 파일 없음: {filepath}")
            return

        try:
            df = pd.read_csv(filepath)
        except Exception as e:
            logger.error(f"격리 처리 중 파일 읽기 실패: {e}")
            return

        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        # 문제 행 인덱스 수집
        problem_indices = set()
        for issue in issues:
            if issue["issue_type"] == "duplicate_row":
                dup_idx = df[df.duplicated()].index.tolist()
                problem_indices.update(dup_idx)
            elif issue["issue_type"] == "null_or_empty" and issue["field"] in df.columns:
                null_idx = df[df[issue["field"]].isna() | (df[issue["field"]].astype(str).str.strip() == "")].index.tolist()
                problem_indices.update(null_idx)
            elif issue["issue_type"] == "gps_out_of_range":
                field = issue["field"]
                if field in df.columns:
                    vals = pd.to_numeric(df[field], errors="coerce")
                    if "lat" in field.lower() or field == "y":
                        bad_idx = vals[(vals < KOREA_LAT_MIN) | (vals > KOREA_LAT_MAX)].dropna().index.tolist()
                    else:
                        bad_idx = vals[(vals < KOREA_LON_MIN) | (vals > KOREA_LON_MAX)].dropna().index.tolist()
                    problem_indices.update(bad_idx)

        if not problem_indices:
            logger.info(f"격리할 레코드 없음: {filepath.name}")
            return

        # 문제 레코드 분리
        quarantine_df = df.loc[sorted(problem_indices)]
        clean_df = df.drop(index=sorted(problem_indices))

        # 격리 파일 저장
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine_path = self.quarantine_dir / f"{filepath.stem}_quarantine_{timestamp}.csv"
        quarantine_df.to_csv(quarantine_path, index=False, encoding="utf-8-sig")
        logger.info(f"격리 완료: {len(quarantine_df)}행 -> {quarantine_path}")

        # 원본 파일을 정리된 데이터로 덮어쓰기
        clean_df.to_csv(filepath, index=False, encoding="utf-8-sig")
        logger.info(f"원본 갱신: {len(clean_df)}행 유지 ({filepath.name})")

    def generate_report(self) -> str:
        """데이터 품질 보고서를 CSV로 생성.

        Returns:
            생성된 보고서 파일 경로
        """
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # 전체 검증 실행
        validation_result = self.validate_all()

        # 보고서 행 구성
        report_rows = []
        for file_result in validation_result["files"]:
            for issue in file_result["issues"]:
                report_rows.append({
                    "file": Path(file_result["filepath"]).name,
                    "data_type": file_result["data_type"],
                    "total_rows": file_result["total_rows"],
                    "valid_rows": file_result["valid_rows"],
                    "field": issue["field"],
                    "issue_type": issue["issue_type"],
                    "issue_count": issue["count"],
                    "examples": str(issue["examples"][:3]),
                    "checked_at": datetime.now().isoformat(),
                })

        if not report_rows:
            # 이슈가 없어도 요약 행 생성
            for file_result in validation_result["files"]:
                report_rows.append({
                    "file": Path(file_result["filepath"]).name,
                    "data_type": file_result["data_type"],
                    "total_rows": file_result["total_rows"],
                    "valid_rows": file_result["valid_rows"],
                    "field": "",
                    "issue_type": "PASS",
                    "issue_count": 0,
                    "examples": "",
                    "checked_at": datetime.now().isoformat(),
                })

        report_df = pd.DataFrame(report_rows)
        report_path = self.reports_dir / "data_quality_report.csv"
        report_df.to_csv(report_path, index=False, encoding="utf-8-sig")

        logger.info(f"품질 보고서 생성: {report_path}")
        return str(report_path)
