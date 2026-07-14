from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


EXCEL_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_TITLE_FILL = PatternFill("solid", fgColor="1F4E3D")
_SECTION_FILL = PatternFill("solid", fgColor="DCE9E2")
_HEADER_FILL = PatternFill("solid", fgColor="E9EFEA")
_STATUS_FILLS = {
    "success": PatternFill("solid", fgColor="D9EAD3"),
    "failure": PatternFill("solid", fgColor="F4CCCC"),
    "cancelled": PatternFill("solid", fgColor="FFF2CC"),
}
_THIN_BORDER = Border(
    left=Side(style="thin", color="C8D0CA"),
    right=Side(style="thin", color="C8D0CA"),
    top=Side(style="thin", color="C8D0CA"),
    bottom=Side(style="thin", color="C8D0CA"),
)
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_DIRECTION_LABELS = {"upload": "업로드", "download": "다운로드", "full": "전체"}
_STATUS_LABELS = {"success": "성공", "failure": "실패", "cancelled": "취소"}


class SustainedExcelError(RuntimeError):
    pass


def safe_excel_text(value: Any) -> str:
    text = ILLEGAL_CHARACTERS_RE.sub("", "" if value is None else str(value))[:32767]
    if text.lstrip().startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def build_sustained_excel_filename(result: dict[str, Any]) -> str:
    completed_at = safe_excel_text(result.get("completed_at", ""))
    digits = "".join(re.findall(r"\d", completed_at))
    timestamp = f"{digits[:8]}-{digits[8:14]}" if len(digits) >= 14 else "unknown-time"
    session_id = safe_excel_text(result.get("session_id", ""))
    short_id = session_id[:8] if re.fullmatch(r"[0-9a-f]{8,32}", session_id) else "result"
    return f"network-check_{timestamp}_{short_id}.xlsx"


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _as_integer(value: Any) -> int | None:
    number = _as_number(value)
    return int(number) if number is not None else None


def _style_title(sheet, title: str, *, end_column: int) -> None:
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=end_column)
    cell = sheet.cell(1, 1, safe_excel_text(title))
    cell.fill = _TITLE_FILL
    cell.font = Font(color="FFFFFF", bold=True, size=16)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[1].height = 28


def _style_section(sheet, row: int, title: str, *, end_column: int) -> None:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=end_column)
    cell = sheet.cell(row, 1, safe_excel_text(title))
    cell.fill = _SECTION_FILL
    cell.font = Font(bold=True, color="1F3D32")
    cell.alignment = Alignment(vertical="center")


def _style_header_row(sheet, row: int, start_column: int, end_column: int) -> None:
    for column in range(start_column, end_column + 1):
        cell = sheet.cell(row, column)
        cell.fill = _HEADER_FILL
        cell.font = Font(bold=True, color="27362F")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _THIN_BORDER


def _set_label_value(sheet, row: int, label_column: int, label: str, value: Any) -> None:
    label_cell = sheet.cell(row, label_column, safe_excel_text(label))
    value_cell = sheet.cell(row, label_column + 1, value)
    label_cell.fill = _HEADER_FILL
    label_cell.font = Font(bold=True, color="27362F")
    label_cell.border = _THIN_BORDER
    value_cell.border = _THIN_BORDER
    value_cell.alignment = Alignment(vertical="top", wrap_text=True)


def _build_summary_sheet(workbook: Workbook, result: dict[str, Any]) -> None:
    sheet = workbook.active
    sheet.title = "측정 요약"
    sheet.sheet_view.showGridLines = False
    _style_title(sheet, "웹 HTTP 지속 측정 결과", end_column=10)

    requested = _as_mapping(result.get("requested"))
    latency = _as_mapping(result.get("http_latency"))
    directions = _as_mapping(result.get("directions"))
    status = safe_excel_text(result.get("status", ""))

    _style_section(sheet, 3, "측정 정보", end_column=10)
    _set_label_value(sheet, 4, 1, "측정 상태", safe_excel_text(_STATUS_LABELS.get(status, status)))
    sheet["B4"].fill = _STATUS_FILLS.get(status, PatternFill(fill_type=None))
    _set_label_value(sheet, 4, 3, "세션 ID", safe_excel_text(result.get("session_id", "")))
    sheet.merge_cells("D4:J4")

    _set_label_value(sheet, 5, 1, "오류 내용", safe_excel_text(result.get("error", "")))
    sheet.merge_cells("B5:J5")
    sheet.row_dimensions[5].height = 34

    _set_label_value(sheet, 6, 1, "시작 시각", safe_excel_text(result.get("started_at", "")))
    _set_label_value(sheet, 6, 3, "완료 시각", safe_excel_text(result.get("completed_at", "")))
    _set_label_value(sheet, 6, 5, "클라이언트 IP", safe_excel_text(result.get("client_ip", "")))
    _set_label_value(
        sheet,
        7,
        1,
        "측정 방향",
        safe_excel_text(_DIRECTION_LABELS.get(str(requested.get("direction", "")), requested.get("direction", ""))),
    )
    _set_label_value(sheet, 7, 3, "본 측정 시간(초)", _as_number(requested.get("duration_seconds")))
    _set_label_value(sheet, 7, 5, "워밍업(초)", _as_number(requested.get("warmup_seconds")))
    _set_label_value(sheet, 7, 7, "HTTP 연결 수", _as_integer(requested.get("stream_count")))

    _style_section(sheet, 9, "HTTP 응답시간", end_column=10)
    latency_headers = ("최소(ms)", "중앙값(ms)", "최대(ms)")
    latency_values = (latency.get("min_ms"), latency.get("median_ms"), latency.get("max_ms"))
    for index, (header, value) in enumerate(zip(latency_headers, latency_values), start=1):
        sheet.cell(10, index, safe_excel_text(header))
        sheet.cell(11, index, _as_number(value))
    _style_header_row(sheet, 10, 1, 3)
    for column in range(1, 4):
        sheet.cell(11, column).number_format = "0.00"
        sheet.cell(11, column).border = _THIN_BORDER

    _style_section(sheet, 13, "방향별 전송 성능", end_column=10)
    headers = (
        "방향",
        "전송량(Byte)",
        "전송량(MiB)",
        "실제 시간(초)",
        "평균(Mbps)",
        "평균(MB/s)",
        "중앙값(Mbps)",
        "최소(Mbps)",
        "최대(Mbps)",
        "변동률(%)",
    )
    for column, header in enumerate(headers, start=1):
        sheet.cell(14, column, safe_excel_text(header))
    _style_header_row(sheet, 14, 1, 10)

    row = 15
    for direction in ("upload", "download"):
        if direction not in directions:
            continue
        summary = _as_mapping(directions.get(direction))
        byte_count = _as_integer(summary.get("bytes_transferred"))
        average_mbps = _as_number(summary.get("average_mbps"))
        values = (
            safe_excel_text(_DIRECTION_LABELS[direction]),
            byte_count,
            byte_count / (1024 * 1024) if byte_count is not None else None,
            _as_number(summary.get("actual_duration_seconds")),
            average_mbps,
            average_mbps / 8 if average_mbps is not None else None,
            _as_number(summary.get("median_mbps")),
            _as_number(summary.get("min_mbps")),
            _as_number(summary.get("max_mbps")),
            _as_number(summary.get("variability_percent")),
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row, column, value)
            cell.border = _THIN_BORDER
            cell.alignment = Alignment(vertical="center")
        sheet.cell(row, 2).number_format = "#,##0"
        for column in range(3, 11):
            sheet.cell(row, column).number_format = "#,##0.00"
        row += 1

    sheet.freeze_panes = "A15"
    if row > 15:
        sheet.auto_filter.ref = f"A14:J{row - 1}"
    widths = (13, 19, 16, 16, 16, 16, 18, 16, 16, 15)
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def _interval_map(summary: Any) -> dict[int, dict[str, Any]]:
    intervals = _as_mapping(summary).get("intervals", [])
    if not isinstance(intervals, list):
        return {}
    mapped: dict[int, dict[str, Any]] = {}
    for fallback_index, item in enumerate(intervals, start=1):
        if not isinstance(item, dict):
            continue
        index = _as_integer(item.get("index")) or fallback_index
        if index > 0:
            mapped[index] = item
    return mapped


def _build_intervals_sheet(workbook: Workbook, result: dict[str, Any]) -> None:
    sheet = workbook.create_sheet("구간별 속도")
    sheet.sheet_view.showGridLines = False
    _style_title(sheet, "1초 구간별 웹 HTTP 처리량", end_column=5)

    headers = ("구간(초)", "업로드(Byte)", "업로드(Mbps)", "다운로드(Byte)", "다운로드(Mbps)")
    for column, header in enumerate(headers, start=1):
        sheet.cell(3, column, safe_excel_text(header))
    _style_header_row(sheet, 3, 1, 5)

    directions = _as_mapping(result.get("directions"))
    upload_intervals = _interval_map(directions.get("upload"))
    download_intervals = _interval_map(directions.get("download"))
    indexes = sorted(set(upload_intervals) | set(download_intervals))

    for row, index in enumerate(indexes, start=4):
        upload = upload_intervals.get(index)
        download = download_intervals.get(index)
        values = (
            index,
            _as_integer(upload.get("bytes_transferred")) if upload else None,
            _as_number(upload.get("mbps")) if upload else None,
            _as_integer(download.get("bytes_transferred")) if download else None,
            _as_number(download.get("mbps")) if download else None,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row, column, value)
            cell.border = _THIN_BORDER
            cell.alignment = Alignment(horizontal="right" if column > 1 else "center")
        sheet.cell(row, 2).number_format = "#,##0"
        sheet.cell(row, 3).number_format = "#,##0.00"
        sheet.cell(row, 4).number_format = "#,##0"
        sheet.cell(row, 5).number_format = "#,##0.00"

    last_row = max(3, len(indexes) + 3)
    sheet.freeze_panes = "A4"
    sheet.auto_filter.ref = f"A3:E{last_row}"
    for column, width in enumerate((13, 19, 18, 19, 18), start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width

    if indexes:
        chart = LineChart()
        chart.title = "1초 구간 처리량"
        chart.y_axis.title = "Mbps"
        chart.x_axis.title = "구간(초)"
        chart.height = 8.5
        chart.width = 16
        chart.legend.position = "b"
        categories = Reference(sheet, min_col=1, min_row=4, max_row=last_row)
        for column in (3, 5):
            if (column == 3 and upload_intervals) or (column == 5 and download_intervals):
                data = Reference(sheet, min_col=column, min_row=3, max_row=last_row)
                chart.add_data(data, titles_from_data=True)
        chart.set_categories(categories)
        for series in chart.series:
            series.graphicalProperties.line.width = 22000
        sheet.add_chart(chart, "G3")

    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def build_sustained_excel(result: dict[str, Any]) -> bytes:
    if not isinstance(result, dict):
        raise SustainedExcelError("Excel로 변환할 측정 결과 형식이 올바르지 않습니다.")
    if not isinstance(result.get("directions"), dict):
        raise SustainedExcelError("측정 결과에 방향별 통계가 없습니다.")

    try:
        workbook = Workbook()
        workbook.properties.creator = "InternalUpload"
        workbook.properties.title = "웹 HTTP 지속 측정 결과"
        _build_summary_sheet(workbook, result)
        _build_intervals_sheet(workbook, result)
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    except SustainedExcelError:
        raise
    except Exception as exc:
        raise SustainedExcelError("Excel 측정 결과를 생성할 수 없습니다.") from exc
