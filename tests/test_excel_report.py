from datetime import datetime

from openpyxl import Workbook

from excel_report import add_speed_chart, chart_label_positions, nice_axis_maximum, to_kst_excel_datetime


def test_kst_conversion_handles_offset_and_legacy_naive_time():
    utc_value = to_kst_excel_datetime("2026-07-14 05:30:00 +0000")
    naive_value = to_kst_excel_datetime("2026-07-14 14:30:00")

    assert utc_value == datetime(2026, 7, 14, 14, 30)
    assert naive_value == datetime(2026, 7, 14, 14, 30)
    assert to_kst_excel_datetime("invalid") is None


def test_chart_labels_show_all_short_values_and_selected_long_values():
    short_indexes = list(range(1, 11))
    partial_indexes = list(range(1, 12))
    long_indexes = list(range(1, 31))
    short_values = [float(value) for value in short_indexes]
    long_values = [float(value) for value in long_indexes]
    long_values[1] = 0.5
    long_values[16] = 99.0

    assert chart_label_positions(short_indexes, short_values) == list(range(10))
    assert chart_label_positions(partial_indexes, [float(value) for value in partial_indexes]) == list(range(11))
    assert chart_label_positions(long_indexes, long_values) == [1, 4, 9, 14, 16, 19, 24, 29]
    assert chart_label_positions(long_indexes, [10.0] * 30) == [0, 4, 9, 14, 19, 24, 29]


def test_nice_axis_maximum_starts_at_zero_friendly_round_ceiling():
    assert nice_axis_maximum(8) == 10
    assert nice_axis_maximum(124) == 150
    assert nice_axis_maximum(480) == 500


def test_speed_chart_axis_includes_reported_average_for_legacy_results():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["시간", "속도", "평균", "표시값"])
    sheet.append([1, 100.0, 600.0, None])
    sheet.append([2, 200.0, 600.0, None])

    chart = add_speed_chart(
        sheet,
        title="속도 변화",
        header_row=1,
        start_row=2,
        end_row=3,
        time_column=1,
        speed_column=2,
        average_column=3,
        label_column=4,
        anchor="F1",
        color="246B54",
        indexes=[1, 2],
        values=[100.0, 200.0],
        average_value=600.0,
    )

    assert chart.y_axis.scaling.min == 0
    assert chart.y_axis.scaling.max >= 600
