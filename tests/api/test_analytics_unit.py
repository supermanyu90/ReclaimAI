from datetime import date
from types import SimpleNamespace as NS

import pytest

from reclaim_api.analytics import service, warehouse
from reclaim_api.providers.base import ProviderUnavailable


@pytest.mark.parametrize("name", sorted(p.stem for p in warehouse.SQL_DIR.glob("*.sql")))
def test_every_bigquery_query_transpiles_to_postgres(name):
    out = warehouse.to_postgres(name)
    assert "@" not in out and "QUALIFY" not in out.upper()


class FakeBQ:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def query(self, sql, job_config):
        self.calls.append((sql, job_config))
        if self.error:
            raise self.error
        return NS(result=lambda: [NS(items=lambda: {"deliveries": 3, "distinct_events": 2}.items())])


def test_bigquery_runs_native_sql_with_typed_parameters():
    fake = FakeBQ()
    bq = warehouse.BigQueryWarehouse(project="demo", dataset="reclaim", location="asia-south1", client=fake)
    rows = bq.query("duplicate_rate", {"tenant": "t1", "start": date(2026, 1, 1), "end": date(2026, 2, 1)})
    sql, cfg = fake.calls[0]
    assert rows == [{"deliveries": 3, "distinct_events": 2}] and "@tenant" in sql
    assert cfg.default_dataset.dataset_id == "reclaim"
    assert {p.name: p.type_ for p in cfg.query_parameters} == {"tenant": "STRING", "start": "DATE", "end": "DATE"}
    assert bq.status().state == "live"


def test_bigquery_unconfigured_or_failing_is_unavailable():
    from google.api_core import exceptions as gexc

    with pytest.raises(ProviderUnavailable):
        warehouse.BigQueryWarehouse(project=None, dataset="d", location="l", client=FakeBQ()).query("duplicate_rate", {})
    bq = warehouse.BigQueryWarehouse(project="demo", dataset="d", location="l", client=FakeBQ(error=gexc.Forbidden("no access to demo.d")))
    with pytest.raises(ProviderUnavailable) as exc:
        bq.query("duplicate_rate", {})
    assert "demo.d" not in str(exc.value) and bq.status().state == "unavailable"


def test_gap_detection_needs_three_months_and_stops_at_the_series_start():
    rows = [{"account_token": "a", "category": "investment_contribution", "currency": "INR", "month": date(2026, m, 1)} for m in (3, 4, 6)]
    flags = service._recurring_gaps(rows, {"a": "Savings"}, date(2026, 1, 1), date(2026, 6, 30))
    assert [f["month"] for f in flags] == ["2026-05"]  # not Jan/Feb: the series began in March
    assert service._recurring_gaps(rows[:2], {"a": "Savings"}, date(2026, 1, 1), date(2026, 6, 30)) == []


def test_unusual_amount_needs_history():
    row = {"amount_minor": 10_000_000, "prior_mean": 5_000.0, "prior_sd": 1_000.0, "prior_n": 5, "account_token": "a", "currency": "INR", "category": "other", "booked_at": date(2026, 1, 1)}
    assert service._unusual([row], {}, {}) == []
    assert len(service._unusual([row | {"prior_n": 8}], {}, {})) == 1
