from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from excel_report import (
    EXCEL_DATETIME_FORMAT,
    KST_NOTE,
    add_speed_chart,
    kst_filename_timestamp,
    to_kst_excel_datetime,
)
from sustained_excel import EXCEL_MIME_TYPE, safe_excel_text


_TITLE_FILL = PatternFill("solid", fgColor="1F4E3D")
_SECTION_FILL = PatternFill("solid", fgColor="DCE9E2")
_HEADER_FILL = PatternFill("solid", fgColor="E9EFEA")
_STATUS_FILLS = {
    "completed": PatternFill("solid", fgColor="D9EAD3"),
    "failed": PatternFill("solid", fgColor="F4CCCC"),
    "cancelled": PatternFill("solid", fgColor="FFF2CC"),
}
_THIN_BORDER = Border(
    left=Side(style="thin", color="C8D0CA"),
    right=Side(style="thin", color="C8D0CA"),
    top=Side(style="thin", color="C8D0CA"),
    bottom=Side(style="thin", color="C8D0CA"),
)
_DIRECTION_LABELS = {"upload": "업로드", "download": "다운로드", "full": "전체"}
_STATUS_LABELS = {"completed": "완료", "failed": "실패", "cancelled": "취소"}
_NOT_MEASURED = "측정 안 함"
_TELEMETRY_UNAVAILABLE = "운영체제에서 제공하지 않음"


class ProbeExcelError(RuntimeError):
    pass


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


def _style_title(sheet, title: str, end_column: int) -> None:
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=end_column)
    cell = sheet.cell(1, 1, safe_excel_text(title))
    cell.fill = _TITLE_FILL
    cell.font = Font(color="FFFFFF", bold=True, size=16)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    sheet.row_dimensions[1].height = 28


def _style_note(sheet, text: str, end_column: int) -> None:
    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=end_column)
    cell = sheet.cell(2, 1, safe_excel_text(text))
    cell.font = Font(color="626B5F", italic=True, size=10)
    cell.alignment = Alignment(horizontal="right", vertical="center")


def _style_section(sheet, row: int, title: str, end_column: int) -> None:
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=end_column)
    cell = sheet.cell(row, 1, safe_excel_text(title))
    cell.fill = _SECTION_FILL
    cell.font = Font(bold=True, color="1F3D32")


def _style_header(sheet, row: int, end_column: int) -> None:
    for column in range(1, end_column + 1):
        cell = sheet.cell(row, column)
        cell.fill = _HEADER_FILL
        cell.font = Font(bold=True, color="27362F")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _THIN_BORDER


def _set_label_value(sheet, row: int, column: int, label: str, value: Any) -> None:
    label_cell = sheet.cell(row, column, safe_excel_text(label))
    value_cell = sheet.cell(row, column + 1, value)
    label_cell.fill = _HEADER_FILL
    label_cell.font = Font(bold=True, color="27362F")
    label_cell.border = _THIN_BORDER
    value_cell.border = _THIN_BORDER
    value_cell.alignment = Alignment(vertical="top", wrap_text=True)
    if isinstance(value, datetime):
        value_cell.number_format = EXCEL_DATETIME_FORMAT


def _time_value(value: Any) -> datetime | str:
    parsed = to_kst_excel_datetime(value)
    return parsed if parsed is not None else safe_excel_text(value)


def _phase_endpoints(direction: str) -> tuple[str, str]:
    if direction == "upload":
        return "클라이언트", "서버"
    return "서버", "클라이언트"


def _phase_path(direction: str) -> str:
    return "측정 PC → 서버" if direction == "upload" else "서버 → 측정 PC"


def _telemetry_value(telemetry: dict[str, Any], key: str, *, milliseconds: bool = False) -> Any:
    if not telemetry.get("available"):
        return _TELEMETRY_UNAVAILABLE
    value = _as_number(telemetry.get(key))
    if value is None:
        return _TELEMETRY_UNAVAILABLE
    return value / 1000 if milliseconds else int(value)


def _retransmission_rate(sender: dict[str, Any], telemetry: dict[str, Any]) -> float | str:
    if not telemetry.get("available"):
        return _TELEMETRY_UNAVAILABLE
    retransmitted = _as_number(telemetry.get("bytes_retrans"))
    sent = _as_number(sender.get("bytes"))
    if retransmitted is None:
        return _TELEMETRY_UNAVAILABLE
    if sent is None or sent <= 0:
        return "계산 불가"
    return retransmitted / sent * 100


def build_probe_excel_filename(result: dict[str, Any]) -> str:
    timestamp = kst_filename_timestamp(result.get("completed_at"))
    return f"TCP_전송측정_{timestamp}.xlsx"


def _build_summary_sheet(workbook: Workbook, result: dict[str, Any]) -> None:
    sheet = workbook.active
    sheet.title = "결과 요약"
    sheet.sheet_view.showGridLines = False
    _style_title(sheet, "TCP 전송 측정 결과", 10)
    _style_note(sheet, KST_NOTE, 10)

    requested = _as_mapping(result.get("requested"))
    agent = _as_mapping(result.get("agent"))
    phases = _as_mapping(result.get("phases"))
    status = safe_excel_text(result.get("status", ""))

    _style_section(sheet, 3, "측정 정보", 10)
    _set_label_value(sheet, 4, 1, "상태", safe_excel_text(_STATUS_LABELS.get(status, status)))
    sheet["B4"].fill = _STATUS_FILLS.get(status, PatternFill(fill_type=None))
    _set_label_value(
        sheet,
        4,
        3,
        "방향",
        safe_excel_text(_DIRECTION_LABELS.get(str(requested.get("direction", "")), requested.get("direction", ""))),
    )
    _set_label_value(sheet, 4, 5, "TCP 스트림", _as_integer(requested.get("stream_count")))

    _set_label_value(sheet, 5, 1, "측정 시작", _time_value(result.get("started_at")))
    sheet.merge_cells("B5:C5")
    _set_label_value(sheet, 5, 4, "측정 완료", _time_value(result.get("completed_at")))
    sheet.merge_cells("E5:F5")
    _set_label_value(sheet, 5, 7, "측정 시간(초)", _as_number(requested.get("duration_seconds")))
    _set_label_value(sheet, 5, 9, "워밍업(초)", _as_number(requested.get("warmup_seconds")))

    _set_label_value(sheet, 6, 1, "측정 PC", safe_excel_text(agent.get("hostname", "")))
    sheet.merge_cells("B6:C6")
    _set_label_value(sheet, 6, 4, "측정 PC IP", safe_excel_text(agent.get("client_ip", "")))
    sheet.merge_cells("E6:F6")
    _set_label_value(sheet, 6, 7, "서버 주소", safe_excel_text(result.get("server_host", "")))
    sheet.merge_cells("H6:J6")

    error = safe_excel_text(result.get("error", ""))
    if error:
        _set_label_value(sheet, 7, 1, "오류", error)
        sheet.merge_cells("B7:J7")
        sheet.row_dimensions[7].height = 30

    _style_section(sheet, 9, "핵심 결과", 10)
    headers = (
        "방향",
        "측정 경로",
        "평균 속도(Mbps)",
        "초당 전송량(MB/s)",
        "최저 속도(Mbps)",
        "최고 속도(Mbps)",
        "왕복 지연(RTT, ms)",
        "재전송률(%)",
    )
    for column, header in enumerate(headers, start=1):
        sheet.cell(10, column, safe_excel_text(header))
    _style_header(sheet, 10, len(headers))

    row = 11
    for direction in ("upload", "download"):
        if direction not in phases:
            values = (_DIRECTION_LABELS[direction], _phase_path(direction)) + (_NOT_MEASURED,) * 6
        else:
            phase = _as_mapping(phases.get(direction))
            sender = _as_mapping(phase.get("sender"))
            receiver = _as_mapping(phase.get("receiver"))
            telemetry = _as_mapping(sender.get("telemetry"))
            average_mbps = _as_number(receiver.get("average_mbps"))
            values = (
                _DIRECTION_LABELS[direction],
                _phase_path(direction),
                average_mbps,
                average_mbps / 8 if average_mbps is not None else None,
                _as_number(receiver.get("min_mbps")),
                _as_number(receiver.get("max_mbps")),
                _telemetry_value(telemetry, "rtt_us", milliseconds=True),
                _retransmission_rate(sender, telemetry),
            )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row, column, value)
            cell.border = _THIN_BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for column in range(3, 7):
            sheet.cell(row, column).number_format = "#,##0.00"
        sheet.cell(row, 7).number_format = "#,##0.00"
        sheet.cell(row, 8).number_format = "0.000"
        row += 1

    _style_section(sheet, 14, "참고", 10)
    sheet.cell(
        15,
        1,
        "속도는 반대편 장비가 실제로 받은 데이터 기준입니다. RTT와 재전송률은 운영체제가 제공할 때만 표시하며 정상·비정상을 자동 판정하지 않습니다.",
    )
    sheet.merge_cells("A15:J16")
    sheet["A15"].alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[15].height = 32

    widths = (11, 19, 20, 21, 19, 19, 21, 17, 12, 12)
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A11"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def _intervals(side: Any) -> list[dict[str, Any]]:
    values = _as_mapping(side).get("intervals", [])
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _build_interval_sheet(workbook: Workbook, result: dict[str, Any]) -> None:
    sheet = workbook.create_sheet("속도 변화")
    sheet.sheet_view.showGridLines = False
    _style_title(sheet, "1초 단위 TCP 수신 속도 변화", 6)
    headers = ("방향", "시간(초)", "속도(Mbps)", "초당 전송량(MB/s)")
    for column, header in enumerate(headers, start=1):
        sheet.cell(3, column, safe_excel_text(header))
    _style_header(sheet, 3, len(headers))

    phases = _as_mapping(result.get("phases"))
    row = 4
    chart_ranges: dict[str, tuple[int, int, list[int], list[float], int, int, float]] = {}
    for direction, average_column, label_column in (("upload", 5, 7), ("download", 6, 8)):
        phase = _as_mapping(phases.get(direction))
        receiver = _as_mapping(phase.get("receiver"))
        intervals = _intervals(receiver)
        start_row = row
        indexes: list[int] = []
        values: list[float] = []
        for fallback_index, item in enumerate(intervals, start=1):
            index = _as_integer(item.get("index")) or fallback_index
            mbps = _as_number(item.get("mbps"))
            if mbps is None or index <= 0:
                continue
            indexes.append(index)
            values.append(mbps)
            row_values = (_DIRECTION_LABELS[direction], index, mbps, mbps / 8)
            for column, value in enumerate(row_values, start=1):
                cell = sheet.cell(row, column, value)
                cell.border = _THIN_BORDER
                cell.alignment = Alignment(horizontal="right" if column > 1 else "center")
            sheet.cell(row, 3).number_format = "#,##0.00"
            sheet.cell(row, 4).number_format = "#,##0.00"
            row += 1
        if values:
            reported_average = _as_number(receiver.get("average_mbps"))
            average = reported_average if reported_average is not None else sum(values) / len(values)
            sheet.cell(3, average_column, f"평균 {average:.1f} Mbps")
            for average_row in range(start_row, row):
                sheet.cell(average_row, average_column, average)
            chart_ranges[direction] = (
                start_row,
                row - 1,
                indexes,
                values,
                average_column,
                label_column,
                average,
            )

    sheet.freeze_panes = "A4"
    sheet.auto_filter.ref = f"A3:D{max(3, row - 1)}"
    for column, width in enumerate((12, 13, 18, 22), start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.column_dimensions["E"].hidden = True
    sheet.column_dimensions["F"].hidden = True
    sheet.column_dimensions["G"].hidden = True
    sheet.column_dimensions["H"].hidden = True

    chart_number = 0
    for direction in ("upload", "download"):
        if direction not in chart_ranges:
            continue
        start_row, end_row, indexes, values, average_column, label_column, average = chart_ranges[direction]
        add_speed_chart(
            sheet,
            title=f"{_DIRECTION_LABELS[direction]} 속도 변화",
            header_row=3,
            start_row=start_row,
            end_row=end_row,
            time_column=2,
            speed_column=3,
            average_column=average_column,
            label_column=label_column,
            anchor="J3" if chart_number == 0 else "J21",
            color="246B54" if direction == "upload" else "C15F2E",
            indexes=indexes,
            values=values,
            average_value=average,
        )
        chart_number += 1

    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def _build_stream_sheet(workbook: Workbook, result: dict[str, Any]) -> None:
    sheet = workbook.create_sheet("기술 상세")
    sheet.sheet_view.showGridLines = False
    headers = (
        "방향", "측정 역할", "측정 지점", "스트림 번호", "전송량(Byte)", "전송량(MiB)", "시간(초)",
        "평균 속도(Mbps)", "통계 제공", "왕복 지연(RTT, ms)", "최소 지연(ms)",
        "혼잡 윈도우(Byte)", "재전송량(Byte)", "빠른 재전송(회)",
        "중복 ACK(회)", "타임아웃(회)", "비고",
    )
    _style_title(sheet, "TCP 기술 상세", len(headers))
    for column, header in enumerate(headers, start=1):
        sheet.cell(3, column, safe_excel_text(header))
    _style_header(sheet, 3, len(headers))

    phases = _as_mapping(result.get("phases"))
    row = 4
    for direction in ("upload", "download"):
        phase = _as_mapping(phases.get(direction))
        sender_endpoint, receiver_endpoint = _phase_endpoints(direction)
        for role, endpoint in (("sender", sender_endpoint), ("receiver", receiver_endpoint)):
            streams = _as_mapping(phase.get(role)).get("streams", [])
            if not isinstance(streams, list):
                continue
            for stream in streams:
                if not isinstance(stream, dict):
                    continue
                telemetry = _as_mapping(stream.get("telemetry"))
                available = bool(telemetry.get("available"))
                byte_count = _as_integer(stream.get("bytes"))
                values = (
                    _DIRECTION_LABELS[direction], "송신 측" if role == "sender" else "수신 측", endpoint,
                    _as_integer(stream.get("stream_id")), byte_count,
                    byte_count / (1024 * 1024) if byte_count is not None else None,
                    _as_number(stream.get("duration_seconds")), _as_number(stream.get("mbps")),
                    "제공" if available else _TELEMETRY_UNAVAILABLE,
                    _telemetry_value(telemetry, "rtt_us", milliseconds=True),
                    _telemetry_value(telemetry, "min_rtt_us", milliseconds=True),
                    _telemetry_value(telemetry, "cwnd_bytes"), _telemetry_value(telemetry, "bytes_retrans"),
                    _telemetry_value(telemetry, "fast_retransmits"), _telemetry_value(telemetry, "duplicate_acks"),
                    _telemetry_value(telemetry, "timeout_episodes"),
                    "" if available else safe_excel_text(telemetry.get("error", "TCP 상세 통계를 사용할 수 없습니다.")),
                )
                for column, value in enumerate(values, start=1):
                    cell = sheet.cell(row, column, value)
                    cell.border = _THIN_BORDER
                    cell.alignment = Alignment(vertical="center", wrap_text=True)
                for column in (4, 5, 12, 13, 14, 15, 16):
                    sheet.cell(row, column).number_format = "#,##0"
                for column in (6, 7, 8, 10, 11):
                    sheet.cell(row, column).number_format = "#,##0.00"
                row += 1

    sheet.freeze_panes = "A4"
    sheet.auto_filter.ref = f"A3:Q{max(3, row - 1)}"
    widths = (11, 11, 14, 13, 18, 16, 13, 18, 13, 18, 15, 20, 18, 15, 13, 13, 34)
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True


def build_probe_excel(result: dict[str, Any]) -> bytes:
    if not isinstance(result, dict):
        raise ProbeExcelError("Excel로 변환할 TCP 측정 결과 형식이 올바르지 않습니다.")
    if not isinstance(result.get("phases"), dict):
        raise ProbeExcelError("TCP 측정 결과에 방향별 통계가 없습니다.")
    try:
        workbook = Workbook()
        workbook.properties.creator = "InternalUpload"
        workbook.properties.title = "TCP 전송 측정 결과"
        _build_summary_sheet(workbook, result)
        _build_interval_sheet(workbook, result)
        _build_stream_sheet(workbook, result)
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    except ProbeExcelError:
        raise
    except Exception as exc:
        raise ProbeExcelError("TCP Excel 측정 결과를 생성할 수 없습니다.") from exc


__all__ = [
    "EXCEL_MIME_TYPE",
    "ProbeExcelError",
    "build_probe_excel",
    "build_probe_excel_filename",
]
