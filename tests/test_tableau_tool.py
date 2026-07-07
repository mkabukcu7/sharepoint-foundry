from src.agent.tools.tableau_tool import build_vds_query, map_vds_result


def test_build_vds_query_measure_only():
    q = build_vds_query("Sales")
    assert q["fields"] == [{"fieldCaption": "Sales", "function": "SUM"}]
    assert "filters" not in q


def test_build_vds_query_with_dimension_and_period():
    q = build_vds_query("Sales", dimension_field="Month", period="2024-05")
    captions = [f["fieldCaption"] for f in q["fields"]]
    assert captions == ["Month", "Sales"]
    assert q["fields"][1]["function"] == "SUM"
    assert q["filters"][0]["field"]["fieldCaption"] == "Month"
    assert q["filters"][0]["values"] == ["2024-05"]


def test_build_vds_query_custom_aggregation():
    q = build_vds_query("Latency", aggregation="AVG")
    assert q["fields"][0]["function"] == "AVG"


def test_map_vds_result_reads_last_row_value():
    raw = {
        "data": [
            {"Month": "2024-04", "Sales": 100},
            {"Month": "2024-05", "Sales": 250},
        ],
        "lastUpdatedAt": "2024-05-31T00:00:00Z",
    }
    result = map_vds_result(
        raw,
        metric="Total Sales",
        grain="monthly",
        period="2024-05",
        measure_field="Sales",
        source_workbook="RevenueWB",
        source_url="https://tableau.example.com",
    )
    assert result.value == 250
    assert result.metric == "Total Sales"
    assert result.last_refresh == "2024-05-31T00:00:00Z"
    assert "RevenueWB" in result.citation()


def test_map_vds_result_handles_aggregated_column_name():
    raw = {"data": [{"SUM(Sales)": 999}]}
    result = map_vds_result(
        raw, metric="Sales", grain="monthly", period="2024-05", measure_field="Sales"
    )
    assert result.value == 999
    assert result.last_refresh == "unknown"


def test_map_vds_result_empty_data_yields_none_value():
    result = map_vds_result(
        {"data": []}, metric="Sales", grain="monthly", period="2024-05", measure_field="Sales"
    )
    assert result.value is None
