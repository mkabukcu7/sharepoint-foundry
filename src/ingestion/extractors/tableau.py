"""Tableau metadata extractor for the curated index.

Pulls workbook/datasource/KPI **metadata** (definitions, owners, last refresh)
to ground "what does this dashboard mean / who owns it" questions. Live metric
*values* are never indexed — those are fetched at query time by the Tableau tool
(architecture §4.6). Reuses the auth/query plumbing in the agent Tableau tool.
"""
from __future__ import annotations

from typing import Dict, List, Optional


class TableauMetadataExtractor:
    def __init__(self):
        from ...agent.tools.tableau_tool import TableauTool

        self._tool = TableauTool()

    def list_kpi_records(self) -> List[Dict[str, object]]:
        """Return small semantic KPI metadata records for indexing.

        Calls the Tableau Metadata API (GraphQL). Returns one dict per KPI with
        its definition, owner, source workbook, and last-refresh time.
        """
        import requests

        # Sign in via the shared tool to reuse the session token.
        self._tool._auth()  # noqa: SLF001 - intentional reuse
        url = f"{self._tool._server}/api/metadata/graphql"
        query = """
        query {
          workbooks {
            name
            owner { name }
            updatedAt
            sheets { name }
          }
        }
        """
        resp = requests.post(
            url,
            json={"query": query},
            headers={"X-Tableau-Auth": self._tool._token or ""},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("workbooks", [])
        records: List[Dict[str, object]] = []
        for wb in data:
            for sheet in wb.get("sheets", []) or []:
                records.append(
                    {
                        "kpi": sheet.get("name", ""),
                        "workbook": wb.get("name", ""),
                        "owner": (wb.get("owner") or {}).get("name", ""),
                        "last_refresh": wb.get("updatedAt", ""),
                    }
                )
        return records
