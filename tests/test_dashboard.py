"""Checks the Grafana dashboard and provisioning files.

On PostgreSQL every dashboard query runs against demo data, so a broken query
fails CI instead of showing up as an empty panel.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

from app.demo import load_demo
from tests.conftest import TEST_DB

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = json.loads((ROOT / "monitoring/grafana/dashboards/helpdesk-sla.json").read_text())
DATASOURCES = yaml.safe_load(
    (ROOT / "monitoring/grafana/provisioning/datasources/datasources.yml").read_text()
)["datasources"]
PANELS = [p for p in DASHBOARD["panels"] if p["type"] != "row"]
SQL_TARGETS = [
    pytest.param(t["rawSql"], id=p["title"]) for p in PANELS for t in p["targets"] if "rawSql" in t
]


def expand_grafana_macros(sql: str) -> str:
    sql = re.sub(
        r"\$__timeFilter\((\w+)\)", r"\1 BETWEEN now() - interval '30 days' AND now()", sql
    )
    return re.sub(r"\$__timeGroupAlias\((\w+), '1d'\)", r"date_trunc('day', \1) AS time", sql)


def test_every_panel_uses_a_provisioned_datasource():
    uids = {d["uid"] for d in DATASOURCES}
    for panel in PANELS:
        assert panel["datasource"]["uid"] in uids, panel["title"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] in uids, panel["title"]


def test_panels_do_not_overlap():
    taken = set()
    for panel in DASHBOARD["panels"]:
        g = panel["gridPos"]
        assert g["x"] + g["w"] <= 24, panel["title"]
        cells = {
            (x, y) for x in range(g["x"], g["x"] + g["w"]) for y in range(g["y"], g["y"] + g["h"])
        }
        assert not cells & taken, f"{panel['title']} overlaps another panel"
        taken |= cells


def test_dashboard_only_reads_tables_grafana_can_access():
    from app.db import DASHBOARD_TABLES

    for param in SQL_TARGETS:
        # "extract(epoch FROM column)" is not a table, so skip that form.
        tables = set(re.findall(r"(?<!epoch )\bFROM (\w+)", param.values[0]))
        assert tables <= set(DASHBOARD_TABLES), param.id


@pytest.mark.parametrize("sql", SQL_TARGETS)
def test_dashboard_query_runs_on_postgres(app, sql):
    if TEST_DB == "sqlite://":
        pytest.skip("Dashboard queries use PostgreSQL syntax")
    with app.state.session_factory() as session:
        load_demo(session, tickets=120)
        rows = session.execute(text(expand_grafana_macros(sql))).all()
    assert rows, "query returned no rows on demo data"
