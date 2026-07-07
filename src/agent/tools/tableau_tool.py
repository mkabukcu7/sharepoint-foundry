"""Tableau structured-data tool.

Answers live-metric questions by querying Tableau (VizQL Data Service / Metadata
API). Always returns the value together with its grain, time period, last-refresh
timestamp, and source workbook so the agent can cite it (architecture §4.6).
``requests`` and credentials are loaded lazily.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MetricResult:
    metric: str
    value: object
    grain: str
    period: str
    last_refresh: str
    source_workbook: str
    source_url: str = ""

    def citation(self) -> str:
        return (
            f"{self.metric} = {self.value} ({self.grain}, {self.period}); "
            f"source: {self.source_workbook}, refreshed {self.last_refresh}"
        )


class TableauTool:
    def __init__(
        self,
        server_url: Optional[str] = None,
        site_id: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        from ..config import get_settings

        s = get_settings()
        self._server = (server_url or s.tableau_server_url).rstrip("/")
        self._site = site_id or s.tableau_site_id
        self._api_version = api_version or s.tableau_api_version
        self._token: Optional[str] = None
        self._site_luid: Optional[str] = None

    def _auth(self) -> None:
        """Sign in to Tableau. Reads a PAT from Key Vault in deployed envs."""
        import os
        import requests

        pat_name = os.environ.get("TABLEAU_PAT_NAME", "")
        pat_secret = os.environ.get("TABLEAU_PAT_SECRET", "")
        url = f"{self._server}/api/{self._api_version}/auth/signin"
        payload = {
            "credentials": {
                "personalAccessTokenName": pat_name,
                "personalAccessTokenSecret": pat_secret,
                "site": {"contentUrl": self._site},
            }
        }
        resp = requests.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15)
        resp.raise_for_status()
        creds = resp.json()["credentials"]
        self._token = creds["token"]
        self._site_luid = creds["site"]["id"]

    def query_metric(self, datasource_luid: str, vds_query: dict) -> dict:
        """Query the VizQL Data Service for an aggregated metric.

        ``vds_query`` follows the VDS ``query-datasource`` request body
        (fields + filters + aggregation). Returns the raw VDS JSON; the caller
        maps it into a :class:`MetricResult`.
        """
        import requests

        if not self._token:
            self._auth()
        url = f"{self._server}/api/v1/vizql-data-service/query-datasource"
        body = {"datasource": {"datasourceLuid": datasource_luid}, "query": vds_query}
        resp = requests.post(
            url,
            json=body,
            headers={
                "X-Tableau-Auth": self._token or "",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def metric(
        self,
        metric_name: str,
        period: str,
        grain: str = "monthly",
        *,
        datasource_luid: Optional[str] = None,
    ) -> MetricResult:
        """High-level entry point used by the agent's Tableau function tool.

        Resolves the datasource + field mapping from settings, builds a VDS
        query, executes it, and maps the raw response into a citable
        :class:`MetricResult`. Raises ``ValueError`` when the datasource/field
        mapping has not been configured for the environment.
        """
        from ..config import get_settings

        s = get_settings()
        luid = datasource_luid or getattr(s, "tableau_datasource_luid", "")
        measure = getattr(s, "tableau_measure_field", "") or metric_name
        dimension = getattr(s, "tableau_date_field", "")
        if not luid:
            raise ValueError(
                "Tableau datasource LUID is not configured (TABLEAU_DATASOURCE_LUID)."
            )
        vds_query = build_vds_query(measure, dimension_field=dimension, period=period)
        raw = self.query_metric(luid, vds_query)
        return map_vds_result(
            raw,
            metric=metric_name,
            grain=grain,
            period=period,
            measure_field=measure,
            source_workbook=getattr(s, "tableau_source_workbook", "") or luid,
            source_url=self._server,
        )


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without network) — VizQL Data Service query shape
# --------------------------------------------------------------------------- #
def build_vds_query(
    measure_field: str,
    *,
    dimension_field: str = "",
    period: str = "",
    aggregation: str = "SUM",
) -> dict:
    """Build a VizQL Data Service ``query-datasource`` request body.

    Produces a minimal aggregated query: the measure field with an aggregation
    function, optionally grouped/filtered by a date dimension for the requested
    period. Returns the ``query`` object expected by
    :meth:`TableauTool.query_metric`.
    """
    fields: list = []
    if dimension_field:
        fields.append({"fieldCaption": dimension_field})
    fields.append({"fieldCaption": measure_field, "function": aggregation})

    query: dict = {"fields": fields}
    if dimension_field and period:
        query["filters"] = [
            {
                "field": {"fieldCaption": dimension_field},
                "filterType": "SET",
                "values": [period],
            }
        ]
    return query


def map_vds_result(
    raw: dict,
    *,
    metric: str,
    grain: str,
    period: str,
    measure_field: str,
    source_workbook: str = "",
    source_url: str = "",
) -> MetricResult:
    """Map a VDS response into a citable :class:`MetricResult`.

    The VDS ``query-datasource`` response returns ``{"data": [ {col: val} ]}``.
    The measure value is read from the last row (most recent period) and the
    last-refresh timestamp from ``data`` metadata when present.
    """
    data = raw.get("data") or []
    value: object = None
    if data:
        last = data[-1]
        value = last.get(measure_field)
        if value is None:
            # Aggregated column names may be suffixed (e.g. "SUM(Sales)").
            for k, v in last.items():
                if measure_field in k:
                    value = v
                    break
    last_refresh = (
        raw.get("lastUpdatedAt")
        or raw.get("extractRefreshedAt")
        or "unknown"
    )
    return MetricResult(
        metric=metric,
        value=value,
        grain=grain,
        period=period,
        last_refresh=str(last_refresh),
        source_workbook=source_workbook,
        source_url=source_url,
    )
