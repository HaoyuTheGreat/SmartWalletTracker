"""
Read-only BigQuery client wrapper for the QuerySmith agent.

Defense in depth:
  1. Bound to a dedicated read-only service account (smartwallets-querysmith-reader)
     via QUERYSMITH_SA_KEY env var — IAM rejects any non-SELECT statement.
  2. Auto-appends LIMIT 1000 if the LLM forgets one.
  3. Hard 30s server + client query timeout.
  4. Result iterator cuts off at max_rows even if BQ returns more.

Falls back to Application Default Credentials when QUERYSMITH_SA_KEY is unset
(used in Cloud Run where a service account is bound to the container directly).
"""

import os
import re

from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account

load_dotenv()

_LIMIT_PATTERN = re.compile(r"\blimit\s+\d+\b", re.IGNORECASE)


def _format_field_type(field) -> str:
    if field.fields:
        inner = ", ".join(f"{f.name} {_format_field_type(f)}" for f in field.fields)
        base = f"STRUCT<{inner}>"
    else:
        base = field.field_type
    if field.mode == "REPEATED":
        return f"ARRAY<{base}>"
    return base


class BQClient:
    def __init__(
        self,
        project_id: str | None = None,
        dataset: str | None = None,
        timeout_sec: int = 30,
        max_rows: int = 1000,
    ):
        self.project_id = project_id or os.environ.get("GCP_PROJECT", "smart-wallets-tracker")
        self.dataset = dataset or os.environ.get("BQ_DATASET", "whale_tracker")
        self.timeout_sec = timeout_sec
        self.max_rows = max_rows

        sa_key_path = os.environ.get("QUERYSMITH_SA_KEY")
        if sa_key_path:
            credentials = service_account.Credentials.from_service_account_file(sa_key_path)
            self._client = bigquery.Client(project=self.project_id, credentials=credentials)
        else:
            # Production: SA bound to Cloud Run Service, ADC picks it up.
            self._client = bigquery.Client(project=self.project_id)

    def describe_table(self, table_name: str) -> str:
        table_ref = f"{self.project_id}.{self.dataset}.{table_name}"
        try:
            table = self._client.get_table(table_ref)
        except Exception:
            return f"Table `{table_name}` not found in dataset `{self.dataset}`."

        lines = [f"Table: {table_ref}", ""]
        for field in table.schema:
            lines.append(f"  {field.name}: {_format_field_type(field)}")
        return "\n".join(lines)

    def _ensure_limit(self, sql: str) -> str:
        if _LIMIT_PATTERN.search(sql):
            return sql
        stripped = sql.rstrip().rstrip(";")
        return f"{stripped}\nLIMIT {self.max_rows}"

    def execute_sql(self, sql: str) -> list[dict]:
        safe_sql = self._ensure_limit(sql)

        job_config = bigquery.QueryJobConfig(
            job_timeout_ms=self.timeout_sec * 1000,
        )
        query_job = self._client.query(safe_sql, job_config=job_config)
        iterator = query_job.result(timeout=self.timeout_sec)

        rows = []
        for row in iterator:
            if len(rows) >= self.max_rows:
                break
            rows.append(dict(row))
        return rows
