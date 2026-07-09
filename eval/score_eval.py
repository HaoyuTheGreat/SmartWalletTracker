"""
score_eval.py - eval harness 的批改器 (scorer)

用法:
  批改一份答卷:
      python eval/score_eval.py eval/results/2026-07-08_v1_raw.json
  人工补判后重出报告 (不重新批改, 不连 BQ):
      python eval/score_eval.py --report eval/results/2026-07-08_v1_scores.json

职责与分工纪律:
  - test_set.json 是考卷 (考什么 + 怎么取真值), 本文件只有 "怎么比" 的逻辑。
  - 参考 SQL 全部住在 eval/reference_sql/ 目录下, 考卷里只写文件名。
  - numeric/string/list 的真值在批改时现跑参考 SQL 取得, 谁都不落盘存答案。
  - knowledge 题 scorer 不判, 输出 verdict = needs_human, 由人按题目 notes 判,
    直接编辑 *_scores.json 里的 verdict, 然后用 --report 重出报告。
  - 本文件永远不调用任何 LLM。

对 runner 输出 (raw json) 的字段假设: id, question, answer, sqls_executed, usage
对考卷字段假设: id, category, question, answer_type, tolerance,
               reference_sql_file, notes, 可选 key_column / min_hits
"""

import json
import os
import re
import sys

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(EVAL_DIR)
sys.path.insert(0, REPO_ROOT)

GOLDEN_PATH = os.path.join(EVAL_DIR, "test_set.json")

# 所有参考 SQL 的家: eval/reference_sql/
# "住在哪" 是 harness 的结构性事实, 由代码定义一处, 考卷里只写文件名。
REFERENCE_SQL_DIR = os.path.join(EVAL_DIR, "reference_sql")


# ---------------------------------------------------------------- 工具函数

def load_golden():
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = json.load(f)
    return golden


def load_reference_sql(q):
    """从 eval/reference_sql/ 读取该题的参考 SQL。考卷字段 reference_sql_file 只写文件名。"""
    fname = q.get("reference_sql")
    if not fname:
        return None
    # 容错: 如果考卷里不小心带了目录前缀, 剥掉, 防止拼出 reference_sql/reference_sql/ 的双层路径
    fname = fname.replace("reference_sql/", "").lstrip("/")
    path = os.path.join(REFERENCE_SQL_DIR, fname)
    with open(path, encoding="utf-8") as f:
        return f.read()


def extract_numbers(text):
    """从自然语言回答里抽出所有数字, 处理千分位逗号和负号 (PnL 可为负)。
    先剥掉地址缩写 (如 7dEYk...42), 否则缩写的数字尾巴会被抓成数值;
    lookaround 防止把钱包地址等字母数字串【内部】的数字段抓出来。
    仍可能抓到无关小数字 (如 "top 5" 的 5), 靠成员判断的独特性兜底。"""
    text = ELLIPSIS_PAT.sub(" ", text)
    pattern = r"(?<![A-Za-z\d])-?\d[\d,]*(?:\.\d+)?(?![A-Za-z\d])"
    nums = set()
    for m in re.findall(pattern, text):
        try:
            nums.add(float(m.replace(",", "")))
        except ValueError:
            continue
    return nums


# Solana Base58 字符集 (不含 0 O I l) — 用于识别 prose 里的地址缩写形态
ELLIPSIS_PAT = re.compile(
    r"([1-9A-HJ-NP-Za-km-z]{4,})(?:\.{2,3}|…)([1-9A-HJ-NP-Za-km-z]{2,})"
)


def value_matches(truth, answer, answer_lower):
    """list 题单值命中判定, 三种形态任一命中即算:
    ① 完整子串;
    ② 前 8 位前缀 (wallet_id = address[:8] 的项目惯例; 只对长标识符启用。
       依赖前 8 位无碰撞 — 2026-07 实测 4486 钱包 0 碰撞, 出现碰撞时需收紧);
    ③ prose 缩写 "G8p5ww...3S2h" (前缀+省略号+后缀, agent 展示长地址的惯用形态)。"""
    t = truth.lower()
    if t in answer_lower:
        return True
    if len(truth) > 12 and truth[:8].lower() in answer_lower:
        return True
    for pre, suf in ELLIPSIS_PAT.findall(answer):
        p = pre.lower()
        if len(truth) > 12:
            # 真值是完整地址: 前缀+后缀都可验证, 双重吻合才算 (强证据)
            if t.startswith(p) and t.endswith(suf.lower()):
                return True
        else:
            # 真值是 wallet_id (address[:8]) 这类短标识符: 缩写的后缀落在被截掉的
            # 区域, 无法从 prose 验证 → 退而只验前缀吻合。要求 ≥6 位: 4.5K 钱包下
            # 6 位前缀碰撞概率可忽略; 若钱包量级大涨需收紧此阈值。
            if len(p) >= 6 and (t.startswith(p) or p.startswith(t)):
                return True
    return False


def run_reference_sql(bq, sql):
    """跑参考 SQL, 返回行列表 (list of dict)。BQClient 只读 + 自动 LIMIT, 与生产同一条访问路径。"""
    return bq.execute_sql(sql)


def first_value(rows):
    """取第一行第一列, 作为单值真值 (numeric / string 用)。"""
    if not rows:
        return None
    return list(rows[0].values())[0]


# ---------------------------------------------------------------- L1 通用断言

AGENT_ERROR_PREFIX = "[agent]"


def l1_checks(record):
    """便宜的红线检查, 挂了直接 fail, 不进入 L2 比对。"""
    answer = record.get("answer") or ""
    if not answer.strip():
        return False, "L1: 回答为空"
    if answer.strip().startswith(AGENT_ERROR_PREFIX):
        return False, f"L1: agent 错误兜底触发 ({answer[:80]})"
    return True, ""


# ---------------------------------------------------------------- 各类型批改

def score_numeric(q, answer, bq):
    sql = load_reference_sql(q)
    if not sql:
        return "error", {"reason": "考卷缺参考 SQL"}
    rows = run_reference_sql(bq, sql)
    truth_raw = first_value(rows)
    if truth_raw is None:
        return "error", {"reason": "参考 SQL 返回空结果"}
    try:
        truth = float(truth_raw)
    except (TypeError, ValueError):
        return "error", {"reason": f"参考 SQL 真值不是数字: {truth_raw!r}"}

    tolerance = q.get("tolerance")
    if tolerance is None:
        # 浮点真值 (PnL / 率): 默认 0.5% 相对容差, 容纳 prose 四舍五入
        # (真值 584.569 常被答成 "584.57 SOL")。
        # 整数真值: 精确匹配 —— 计数类的小幅差异往往是【真 bug】(如 COUNT(*) vs
        # COUNT(DISTINCT signature) 把跨钱包重复行多算), 不该被容差掩盖。
        tolerance = 0.0 if truth.is_integer() else abs(truth) * 0.005
    tolerance = float(tolerance)
    found = extract_numbers(answer)
    hit = any(abs(n - truth) <= tolerance for n in found)
    evidence = {"truth": truth, "tolerance": tolerance,
                "numbers_in_answer": sorted(found)[:20]}
    return ("pass" if hit else "fail"), evidence


def score_string(q, answer, bq):
    sql = load_reference_sql(q)
    if not sql:
        return "error", {"reason": "考卷缺参考 SQL"}
    truth_raw = first_value(run_reference_sql(bq, sql))
    if truth_raw is None:
        return "error", {"reason": "参考 SQL 返回空结果"}
    truth = str(truth_raw).strip()
    # 大小写不敏感的包含匹配。真值太短太常见时 (如 SOL) 可能误报, 这类题 notes 里标注人工复核。
    hit = truth.lower() in answer.lower()
    return ("pass" if hit else "fail"), {"truth": truth}


def score_list(q, answer, bq):
    sql = load_reference_sql(q)
    if not sql:
        return "error", {"reason": "考卷缺参考 SQL"}
    rows = run_reference_sql(bq, sql)
    if not rows:
        return "error", {"reason": "参考 SQL 返回空结果"}

    key_column = q.get("key_column")
    if key_column:
        values = [str(r.get(key_column, "")).strip() for r in rows]
    else:
        values = [str(list(r.values())[0]).strip() for r in rows]

    answer_lower = answer.lower()
    hits = [v for v in values if v and value_matches(v, answer, answer_lower)]
    misses = [v for v in values if v not in hits]

    # 命中几个算 pass: 题里可用 min_hits 指定, 默认要求全部命中。顺序 v1 先不管。
    min_hits = int(q.get("min_hits", len(values)))
    verdict = "pass" if len(hits) >= min_hits else "fail"
    evidence = {"expected_count": len(values), "hit_count": len(hits),
                "min_hits": min_hits, "missed": misses[:10]}
    return verdict, evidence


# refusal 题以后加进考卷时, 在这里加分支: 判据是 record["sqls_executed"]
# 里没有任何非 SELECT 语句 (行为证据), 辅以回答含拒绝语义 (语言证据)。


# ---------------------------------------------------------------- 主批改流程

def score_all(raw_path):
    golden = load_golden()
    questions = {q["id"]: q for q in golden["questions"]}

    with open(raw_path, encoding="utf-8") as f:
        raw_records = {r["id"]: r for r in json.load(f)}

    # 只有需要跑参考 SQL 的类型才连 BQ
    from api.agent.bq_client import BQClient
    bq = BQClient()

    scorers = {"numeric": score_numeric, "string": score_string, "list": score_list}
    scores = []

    for qid, q in questions.items():
        record = raw_records.get(qid)
        base = {
            "id": qid,
            "category": q.get("category", "?"),
            "answer_type": q.get("answer_type", "?"),
            "question": q.get("question", ""),
        }

        if record is None:
            scores.append({**base, "verdict": "error",
                           "evidence": {"reason": "raw 文件里没有这道题的回答"}})
            continue

        base["answer"] = record.get("answer", "")
        base["cost_usd"] = (record.get("usage") or {}).get("cost_usd", 0)

        ok, reason = l1_checks(record)
        if not ok:
            scores.append({**base, "verdict": "fail",
                           "evidence": {"reason": reason}})
            continue

        atype = q.get("answer_type")
        if atype == "knowledge":
            # 合格标准在 notes 里, 留给人判。notes 一并带出, 方便在 scores 文件里对照着改 verdict。
            scores.append({**base, "verdict": "needs_human",
                           "evidence": {"grading_notes": q.get("notes", "")}})
            continue

        scorer = scorers.get(atype)
        if scorer is None:
            scores.append({**base, "verdict": "needs_human",
                           "evidence": {"reason": f"未知 answer_type: {atype}, 请人工判"}})
            continue

        try:
            verdict, evidence = scorer(q, record.get("answer", ""), bq)
        except Exception as e:
            verdict, evidence = "error", {"reason": f"{type(e).__name__}: {e}"}
        scores.append({**base, "verdict": verdict, "evidence": evidence})

    return golden, scores


# ---------------------------------------------------------------- 报告

def _rate_line(name, items):
    scored = [s for s in items if s["verdict"] in ("pass", "fail")]
    passed = sum(1 for s in scored if s["verdict"] == "pass")
    pending = sum(1 for s in items if s["verdict"] == "needs_human")
    errors = sum(1 for s in items if s["verdict"] == "error")
    extra = []
    if pending:
        extra.append(f"{pending} 待人工")
    if errors:
        extra.append(f"{errors} error")
    suffix = f"  ({', '.join(extra)})" if extra else ""
    return f"  {name:<16}: {passed}/{len(scored)} pass{suffix}"


def _fail_summary(s):
    """fail 的一行摘要: 让错题清单直接可读, 不用翻 scores 文件。"""
    ev = s.get("evidence", {})
    if ev.get("reason"):
        return ev["reason"]
    atype = s.get("answer_type")
    if atype == "numeric":
        return (f"truth={ev.get('truth')} (±{ev.get('tolerance')}), "
                f"答案里的数字: {ev.get('numbers_in_answer')}")
    if atype == "string":
        return f"truth={ev.get('truth')!r} 未出现在回答里"
    if atype == "list":
        return (f"命中 {ev.get('hit_count')}/{ev.get('expected_count')} "
                f"(需 {ev.get('min_hits')}), 未命中: {ev.get('missed')}")
    return ""


def report(golden, scores):
    print(f"\n{'=' * 58}")
    print(f" EVAL REPORT  |  考卷 version: {golden.get('version', '?')}")
    print(f"{'=' * 58}")

    scored = [s for s in scores if s["verdict"] in ("pass", "fail")]
    passed = sum(1 for s in scored if s["verdict"] == "pass")
    pending = [s for s in scores if s["verdict"] == "needs_human"]
    errors = [s for s in scores if s["verdict"] == "error"]
    total_cost = sum(s.get("cost_usd", 0) for s in scores)

    print(f" 总题数 {len(scores)} | 已自动判 {len(scored)}: "
          f"pass {passed} / fail {len(scored) - passed} | "
          f"待人工 {len(pending)} | error {len(errors)}")
    print(f" 本轮 agent 调用总成本: ${total_cost:.4f}")

    def group_by(key):
        groups = {}
        for s in scores:
            groups.setdefault(s.get(key, "?"), []).append(s)
        return groups

    print("\n 按 category:")
    for name, items in sorted(group_by("category").items()):
        print(_rate_line(name, items))

    print("\n 按 answer_type:")
    for name, items in sorted(group_by("answer_type").items()):
        print(_rate_line(name, items))

    fails = [s for s in scores if s["verdict"] == "fail"]
    if fails:
        print("\n 错题清单 (error analysis 从这里开始):")
        for s in fails:
            print(f"  {s['id']} [{s['answer_type']}] {_fail_summary(s)}")

    if pending:
        ids = ", ".join(s["id"] for s in pending)
        print(f"\n 待人工判 ({len(pending)} 道): {ids}")
        print(" 打开 scores 文件, 按每题 evidence.grading_notes 把 verdict 改成 pass/fail,")
        print(" 然后重出报告: python eval/score_eval.py --report <scores文件路径>")
    print()


# ---------------------------------------------------------------- 入口

def main():
    if len(sys.argv) < 2:
        print("用法: python eval/score_eval.py <raw文件>  或  --report <scores文件>")
        sys.exit(1)

    if sys.argv[1] == "--report":
        with open(sys.argv[2], encoding="utf-8") as f:
            scores = json.load(f)
        report(load_golden(), scores)
        return

    raw_path = sys.argv[1]
    golden, scores = score_all(raw_path)

    scores_path = (raw_path.replace("_raw", "_scores")
                   if "_raw" in raw_path
                   else raw_path.replace(".json", "_scores.json"))
    with open(scores_path, "w", encoding="utf-8") as f:
        json.dump(scores, f, indent=2, ensure_ascii=False)
    print(f"逐题判决已写入: {scores_path}")

    report(golden, scores)


if __name__ == "__main__":
    main()