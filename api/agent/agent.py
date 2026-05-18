"""
Claude tool-use agent loop.

Ties together:
  - SYSTEM_PROMPT (prompts.py)  — schema context + rules
  - TOOLS + dispatch_tool (tools.py)  — describe_table, execute_sql
  - BQClient (bq_client.py)  — read-only BQ access via QUERYSMITH_SA_KEY
  - Anthropic SDK  — Claude API client

Loop: Claude responds → if tool_use, dispatch & append result → if end_turn, return text.
Self-heals on SQL errors via is_error flag. max_iterations caps runaway loops.
"""

import os

from anthropic import Anthropic

from api.agent.bq_client import BQClient
from api.agent.prompts import SYSTEM_PROMPT
from api.agent.tools import TOOLS, dispatch_tool

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 4096

# Sonnet 4.6 on-demand pricing (USD per 1M tokens). Verify at anthropic.com/pricing.
INPUT_PRICE_PER_MTOK = 3.00
OUTPUT_PRICE_PER_MTOK = 15.00


def compute_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * INPUT_PRICE_PER_MTOK
        + output_tokens * OUTPUT_PRICE_PER_MTOK
    ) / 1_000_000


def run_agent(
    user_message: str,
    bq: BQClient,
    client: Anthropic,
    messages: list | None = None,
    max_iterations: int = 10,
    verbose: bool = False,
) -> tuple[str, list, dict]:
    """Run one user turn through the agentic loop.

    Args:
        user_message: the user's natural-language question.
        bq: BQClient instance (injected for testability).
        client: Anthropic SDK client.
        messages: prior conversation history (None for a fresh conversation).
        max_iterations: hard cap on tool-use rounds to prevent infinite loops.
        verbose: if True, print each tool call + result (for debugging).

    Returns:
        (assistant_text, updated_messages, usage_stats)
    """
    messages = messages if messages is not None else []
    messages.append({"role": "user", "content": user_message})

    total_input_tokens = 0
    total_output_tokens = 0

    for iteration in range(max_iterations):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            text_blocks = [b.text for b in response.content if b.type == "text"]
            final_text = "\n".join(text_blocks).strip()
            return final_text, messages, {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "iterations": iteration + 1,
                "cost_usd": compute_cost_usd(total_input_tokens, total_output_tokens),
            }

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if verbose:
                    print(f"[tool] {block.name}({block.input})")
                content, is_error = dispatch_tool(block.name, block.input, bq)
                if verbose:
                    preview = content if len(content) < 300 else content[:300] + "..."
                    tag = "ERROR" if is_error else "ok"
                    print(f"[{tag}] {preview}\n")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        return (
            f"[agent] Unexpected stop_reason: {response.stop_reason}",
            messages,
            {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "iterations": iteration + 1,
                "cost_usd": compute_cost_usd(total_input_tokens, total_output_tokens),
            },
        )

    return (
        f"[agent] Max iterations ({max_iterations}) reached without completion. "
        f"The query may be too complex or Claude got stuck in a retry loop.",
        messages,
        {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "iterations": max_iterations,
            "cost_usd": compute_cost_usd(total_input_tokens, total_output_tokens),
        },
    )
