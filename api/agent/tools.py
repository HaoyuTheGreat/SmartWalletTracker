"""
Tool definitions for the Anthropic tool-use API + dispatcher.

Two tools exposed to Claude:
  - describe_table: look up a table's schema (cold-path tables only)
  - execute_sql:    run a read-only BigQuery SELECT, return rows as JSON
"""

import json

from api.agent.bq_client import BQClient

DESCRIBE_TABLE_TOOL = {
    "name": "describe_table",
    "description": (
        "Get the schema (column names, types) of a table in the whale_tracker "
        "dataset. Use this when you need the structure of a cold-path table — "
        "raw_swaps, sol_prices, wallet_sources, exchange_wallets, "
        "wallet_candidates, or ingestion_runs. The core tables (wallets, "
        "analyzed_swaps, wallet_classifications) are already described in the "
        "system prompt, so calling this tool for them wastes a round trip."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": (
                    "The table's short name without the project/dataset prefix "
                    "(e.g. 'raw_swaps', NOT 'whale_tracker.raw_swaps')."
                ),
            },
        },
        "required": ["table_name"],
    },
}

EXECUTE_SQL_TOOL = {
    "name": "execute_sql",
    "description": (
        "Execute a read-only SELECT query against BigQuery. Returns up to 1000 "
        "rows as a JSON array of objects. The BQ layer enforces read-only access "
        "and auto-appends LIMIT 1000 if missing. On error, the raw BigQuery "
        "message is returned so you can fix the SQL and retry. "
        "Always use fully qualified table names with backticks: "
        "`smart-wallets-tracker.whale_tracker.<table>`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A valid BigQuery SELECT statement.",
            },
        },
        "required": ["sql"],
    },
}

TOOLS = [DESCRIBE_TABLE_TOOL, EXECUTE_SQL_TOOL]


def dispatch_tool(tool_name: str, tool_input: dict, bq: BQClient) -> tuple[str, bool]:
    """Route a Claude tool_use call to the right BQClient method.

    Returns (content, is_error). Errors become strings so Claude can read them
    and self-correct on the next iteration of the agent loop.
    """
    try:
        if tool_name == "describe_table":
            return bq.describe_table(tool_input["table_name"]), False

        if tool_name == "execute_sql":
            rows = bq.execute_sql(tool_input["sql"])
            if not rows:
                return "Query returned 0 rows.", False
            return json.dumps(rows, indent=2, default=str), False

        return f"Unknown tool: {tool_name}", True

    except Exception as e:
        return f"{type(e).__name__}: {e}", True
