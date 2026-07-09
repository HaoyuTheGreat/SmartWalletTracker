import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.secrets import get_secret

from anthropic import Anthropic
from api.agent.agent import run_agent
from api.agent.bq_client import BQClient

HERE = os.path.dirname(os.path.abspath(__file__))
golden = json.load(open(os.path.join(HERE, "test_set.json"), encoding="utf-8"))
bq = BQClient()
client = Anthropic(api_key=get_secret("CLAUDE_API_KEY"))
results = []

def extract_sqls(messages):
    sqls = []
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        content = msg["content"]
        if not isinstance(content, list):
            continue
        for block in content:
            # block 可能是 SDK 对象或 dict, 两种都兼容
            btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            if btype == "tool_use":
                name = getattr(block, "name", None) or block.get("name")
                if name == "execute_sql":
                    binput = getattr(block, "input", None) or block.get("input")
                    sqls.append(binput.get("sql"))
    return sqls

for q in golden["questions"]:
    print(f"\n=== {q['id']} [{q.get('answer_type','?')}]: {q['question']}")
    answer, message, usage = run_agent(q["question"], bq, client)
    print(f"--- {answer[:300]}")
    print(f"--- ${usage['cost_usd']:.4f} | {usage['iterations']} iters")
    results.append({
        "id": q["id"],
        "question": q["question"],
        "answer": answer,
        "sql_executed": extract_sqls(message),
        "usage": usage
        })

os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
out = os.path.join(HERE, "results", "2026-07-08_v2_raw.json")
json.dump(results, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print(f"\nDone. {len(results)} answers -> {out}")