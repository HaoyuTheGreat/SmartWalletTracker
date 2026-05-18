"""
QuerySmith agent — Claude tool-use loop for natural-language SQL over BigQuery.

Copied from the standalone QuerySmith repo (https://github.com/HaoyuTheGreat/QuerySmith)
and adapted for in-process use by the FastAPI chat endpoint:
  - bq_client now uses a read-only service account (QUERYSMITH_SA_KEY) for
    defense-in-depth — even if Claude generates DELETE/DROP, GCP rejects.
  - CLI / __main__ blocks removed (this is library code, not a runnable script).
"""

from api.agent.agent import run_agent, compute_cost_usd
from api.agent.bq_client import BQClient

__all__ = ["run_agent", "compute_cost_usd", "BQClient"]
