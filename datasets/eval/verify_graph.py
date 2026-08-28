# ============================================================
# 3.4 图谱数据层验证（对齐 2.4 §九 可测指标，9 项断言）
# - 常量已同步 3.x 定案：λ=0.1 / k=0.5 / fail_decay=0.5 / newerr=0.5 / c2=0.5
# - priority 已修复：error_count 封顶 min(err,5) + 双重计数修正
# 运行：python datasets/eval/verify_graph.py
# 无 LLM 依赖，纯确定性数据层断言。
# ============================================================

import json
import os
import sys
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import (
    ErrorGraph, Node, LAMBDA_AGING, MASTERY_K, MASTERY_FAIL_DECAY,
    MASTERY_NEW_ERROR, CONFIRM_REVISE_C2, ERROR_COUNT_CAP,
)

RESULTS = []   # (指标, 结果, 详情)

def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))


def assert_close(actual, expected, tol=1e-6):
    return abs(actual - expected) <= tol


def make_kp(kp_id, error_count=0, mastery=0.0, last=None, created=None):
    return Node(id=kp_id, knowledge_point=kp_id, level="HSK1",
                error_count=error_count, mastery=mastery,
                last_learnt_at=last, created_at=created or "2026-08-01T00:00:00Z")


# ---------------- 指标 1：优先级计算正确率（对拍人工预期） ----------------
def test_priority():
    g = ErrorGraph("t1")
    # 通过直接构造节点，绕开时间依赖，验证公式展开：
    # priority = min(err,5) * (1-mastery) * (1 + λ*days)
    # 用 created_at 距今固定构造，days 无法精确控制，故改用真空比较：
    n1 = make_kp("A", error_count=3, mastery=0.0)     # 3 次错，0 掌握
    n2 = make_kp("B", error_count=10, mastery=0.0)    # 10 次错（应封顶为 5）
    g._nodes = {"A": n1, "B": n2}
    p1 = g._priority(n1)
    p2 = g._priority(n2)
    # A: 3*1*aging ; B: min(10,5)*1*aging
    # 同 created_at → aging 相同，则 B 应为 A 的 5/3 倍（封顶生效）
    ok = assert_close(p2 / p1, 5.0 / 3.0, 1e-6) if p1 else False
    record("优先级计算/封顶", ok, f"A(3,没封顶)p={p1:.3f}, B(10→封5)p={p2:.3f}, 比={p2/p1:.3f} 期望1.667")

    # 双计数修正验证：错误数翻倍但 mastery 也低时，不应平方放大
    n3 = make_kp("C", error_count=4, mastery=0.0)
    n4 = make_kp("D", error_count=4, mastery=0.5)     # 掌握一半
    g._nodes = {"C": n3, "D": n4}
    p3 = g._priority(n3)
    p4 = g._priority(n4)
    # 期望 D 的优先级是 C 的一半（(1-0.5)=0.5），即掌握越好优先级越低
    ok = assert_close(p4 / p3, 0.5, 1e-6) if p3 else False
    record("优先级/掌握度线性", ok, f"C(m=0)p={p3:.3f}, D(m=0.5)p={p4:.3f}, 比={p4/p3:.3f} 期望0.5")


# ---------------- 指标 2：队列排序正确率 ----------------
def test_queue_sort():
    g = ErrorGraph("t2")
    # 用 created_at 控制 aging：早期创建的 aging 更大
    older = "2026-08-01T00:00:00Z"
    newer = "2026-08-26T00:00:00Z"
    g._nodes = {
        "A": make_kp("A", error_count=1, mastery=0.0, created=older),   # aging 最大 → 最高
        "B": make_kp("B", error_count=1, mastery=0.0, created=newer),   # aging 小 → 最低
    }
    q = g.get_review_queue()
    order = [r["kp_id"] for r in q]
    ok = (order == ["A", "B"])
    record("队列排序降序", ok, f"出队顺序={order} 期望['A','B'] (age 由 created_at 起算)")

    # 同创建时间下（aging 相等），错多次优先
    same = "2026-08-01T00:00:00Z"
    g2 = ErrorGraph("t2b")
    g2._nodes = {
        "X": make_kp("X", error_count=5, mastery=0.0, created=same),   # 5次错
        "Y": make_kp("Y", error_count=2, mastery=0.0, created=same),   # 2次错
    }
    q2 = g2.get_review_queue()
    ok2 = (q2[0]["kp_id"] == "X")   # 同 aging 下 error_count 大者优先
    record("队列排序/频次优先", ok2, f"顺序={[r['kp_id'] for r in q2]} (X=5错>Y=2错，同创建同aging)")


# ---------------- 指标 3：计数更新正确率（四象限） ----------------
def test_ingest_quadrants():
    g = ErrorGraph("t3")
    bias = {"fragment": "我有一个猫", "type": "量词",
            "knowledge_point_id": "HSK1-measure", "knowledge_point_name": "量词'只'",
            "level": "HSK1", "uncertain": False}
    # pass/fail × uncertain 四象限
    b_p = dict(bias)
    b_f = dict(bias)
    b_up = dict(bias, uncertain=True)
    b_uf = dict(bias, uncertain=True)

    r1 = g.ingest_verdict(b_p, "pass", False, "ev-p1")
    node1 = g._nodes["HSK1-measure"]
    # pass: mastery += (1-m)*0.5
    exp_mastery = 0.0 + (1 - 0.0) * MASTERY_K
    ok1 = assert_close(node1.mastery, exp_mastery)
    record("计数/四象限 pass", ok1, f"pass后 mastery={node1.mastery:.3f} 期望{exp_mastery:.3f}, last_learnt_at set")

    # fail: mastery *= 0.5
    g2 = ErrorGraph("t3f")
    g2.ingest_verdict(b_f, "fail", False, "ev-f1")
    node2 = g2._nodes["HSK1-measure"]
    ok2 = assert_close(node2.mastery, 0.0 * MASTERY_FAIL_DECAY)
    record("计数/四象限 fail", ok2, f"fail后 mastery={node2.mastery} (新节点0, fail衰减0不变)")

    # 旧节点 fail：mastery 减半，last_learnt_at 不 touch
    g3 = ErrorGraph("t3fo")
    g3.ingest_verdict(b_p, "pass", False, "ev-p3a")   # 先 pass 一次
    node3 = g3._nodes["HSK1-measure"]
    m_before = node3.mastery
    ll_before = node3.last_learnt_at
    g3.ingest_verdict(b_f, "fail", False, "ev-f3")
    ok3 = assert_close(node3.mastery, m_before * MASTERY_FAIL_DECAY) and node3.last_learnt_at == ll_before
    record("计数/旧节点fail不touch", ok3,
           f"前pass mastery={m_before:.3f}→fail后={node3.mastery:.3f} 期望{m_before*0.5:.3f}; last_learnt_at 未变={node3.last_learnt_at==ll_before}")

    # uncertain=pass/fail → 挂队列不碰节点
    g4 = ErrorGraph("t3u")
    r_u = g4.ingest_verdict(b_up, "pass", True, "ev-u1")
    node_c = g4._nodes.get("HSK1-measure")
    ok4 = (r_u["status"] == "verdict_to_queue") and (node_c is None)
    record("四象限/uncertain挂队列", ok4, f"uncertain pass → {r_u['status']}, 节点未创建={node_c is None}")


# ---------------- 指标 4：边创建正确率（含幂等） ----------------
def test_edges():
    g = ErrorGraph("t4")
    g.ingest_verdict({"knowledge_point_id": "A", "fragment": "s", "type": "t"},
                     "pass", False, "ev-e1")
    g.ingest_verdict({"knowledge_point_id": "B", "fragment": "s", "type": "t"},
                     "pass", False, "ev-e1")   # 同句同一事件
    g.link_errors_in_sentence(["A", "B"], "ev-e1")
    edges = g._edges
    ok1 = len(edges) == 1
    e = list(edges.values())[0]
    ok2 = e.edge_weight == 1 and "ev-e1" in e.event_keys
    record("边创建+同事件幂等", ok1 and ok2,
           f"边数={len(edges)} 期望1; weight={e.edge_weight} 期望1, event_count={len(e.event_keys)}")

    # 不同事件累计
    g2 = ErrorGraph("t4b")
    g2.ingest_verdict({"knowledge_point_id": "A", "fragment": "s", "type": "t"}, "pass", False, "ev-e2")
    g2.ingest_verdict({"knowledge_point_id": "B", "fragment": "s", "type": "t"}, "pass", False, "ev-e2")
    g2.link_errors_in_sentence(["A", "B"], "ev-e2")
    g2.link_errors_in_sentence(["A", "B"], "ev-e3")   # 另一事件
    e2 = list(g2._edges.values())[0]
    ok3 = e2.edge_weight == 2
    record("边/多事件累计", ok3, f"weight={e2.edge_weight} 期望2")


# ---------------- 指标 5：提升条件正确率（c2 门槛四情形） ----------------
def test_confirm_c2():
    g = ErrorGraph("t5")
    g.ingest_verdict({"knowledge_point_id": "", "fragment": "frag", "type": "量词"},
                     "pass", True, "ev-c1")  # uncertain 挂队列
    key = "frag|量词|"
    # c2 < 0.5 → 仍 pending
    r1 = g.confirm_item(key, valid=False, c2=0.3)
    ok1 = r1["status"] == "still_pending"
    # c2 >= 0.5 → confirmed + 提升
    r2 = g.confirm_item(key, valid=False, c2=0.6)
    ok2 = r2["status"] == "confirmed"
    # kp_candidate 为空（KP 未命中/pending_mapping）确认后不建空 key 节点——符合 2.4 §七 pending_mapping 出口
    node = g._nodes.get("")
    ok3 = node is None           # 空 kp 确认不应污染图谱为空 key 节点
    record("确认门槛c2", ok1 and ok2 and ok3,
           f"c2=0.3→{r1['status']}(应still_pending), c2=0.6→{r2['status']}(应confirmed), 空kp确认后建空节点={node is not None}(应False，防污染)")

    # valid=true 直接过
    g3 = ErrorGraph("t5b")
    g3.ingest_verdict({"knowledge_point_id": "", "fragment": "f2", "type": "语法"}, "pass", True, "ev-c2")
    r3 = g3.confirm_item("f2|语法|", valid=True, applied_valid=True)
    ok4 = r3["status"] == "confirmed"
    record("确认/valid直过", ok4, f"valid=true→{r3['status']}")

    # 驳回
    g4 = ErrorGraph("t5c")
    g4.ingest_verdict({"knowledge_point_id": "", "fragment": "f3", "type": "语用"}, "pass", True, "ev-c3")
    r4 = g4.reject_item("f3|语用|")
    ok5 = r4["status"] == "rejected" and "f3|语用|" not in g4._queue
    record("驳回清除", ok5, f"reject→{r4['status']}, 队列已清={r4['status']=='rejected'}")


# ---------------- 指标 6：同签名聚合正确率 ----------------
def test_aggregate():
    g = ErrorGraph("t6")
    sig = {"fragment": "很很快", "type": "语法", "knowledge_point_id": "HSK1-adv"}
    g.ingest_verdict(sig, "pass", True, "ev-a1")
    g.ingest_verdict(sig, "pass", True, "ev-a2")   # 同签名两次
    g.ingest_verdict(sig, "pass", True, "ev-a3")
    key = "很很快|语法|HSK1-adv"
    item = g._queue[key]
    n_seen = len(item.seen_event_keys)
    g.confirm_item(key, valid=True, applied_valid=True)
    node = g._nodes["HSK1-adv"]
    ok = node.error_count == n_seen and n_seen == 3
    record("同签名聚合", ok, f"seen={n_seen} 事件→error_count={node.error_count} (期望相等=3)")


# ---------------- 指标 7：四象限回写正确率 ----------------
def test_rewrite_quadrant():
    g = ErrorGraph("t7")
    b = {"knowledge_point_id": "KP-Q", "fragment": "s", "type": "t"}
    g.ingest_verdict(b, "pass", False, "ev-q1")    # 落节点
    g.ingest_verdict(b, "fail", False, "ev-q2")    # 落节点
    g.ingest_verdict(b, "pass", True, "ev-q3")     # 挂队列
    node = g._nodes.get("KP-Q")
    q = g._queue
    ok = node is not None and len([i for i in q.values() if i.status in ("pending",)]) == 1
    record("四象限回写", ok, f"节点={node.to_dict() if node else None}, 队列pending数={len([i for i in q.values() if i.status=='pending'])}")


# ---------------- 指标 8：并发写丢失率（压测） ----------------
def test_concurrency():
    import threading
    g = ErrorGraph("t8")
    errs = []

    def writer(wid):
        try:
            for i in range(30):
                ev = f"ev-{wid}-{i}"
                g.ingest_verdict({"knowledge_point_id": f"KP-{wid}", "fragment": "s", "type": "t"},
                                 "pass", False, ev)
        except Exception as e:
            errs.append(e)

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_nodes = len(g._nodes)
    total_seen = len(g._seen_events)
    ok = (len(errs) == 0) and (total_nodes == 4) and (total_seen == 120)
    record("并发写不丢失", ok, f"线程4×30事件, 节点={total_nodes}(期望4), 幂等集={total_seen}(期望120), 异常={len(errs)}")


# ---------------- 指标 9：持久化恢复一致性 ----------------
def test_persistence():
    g = ErrorGraph("t9")
    b = {"knowledge_point_id": "KP-P", "fragment": "s", "type": "语法", "knowledge_point_name": "KP-P"}
    g.ingest_verdict(b, "pass", False, "ev-p1")
    g.ingest_verdict(b, "fail", False, "ev-p2")
    g.link_errors_in_sentence(["KP-P", "KP-P2"], "ev-p1")   # link 只建边，不自动建 KP-P2 节点
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "graph_t9.json")
        g.save(path)
        g2 = ErrorGraph("t9")
        g2.load(path)
        ok1 = len(g2._nodes) == 1 and len(g2._edges) == 1     # 1 节点(KP-P) + 1 混淆边
        n = g2._nodes["KP-P"]
        ok2 = (n.mastery == g._nodes["KP-P"].mastery
               and n.last_learnt_at is not None
               and g2._seen_events == g._seen_events)          # 主要状态与幂等集一致
        record("持久化恢复一致性", ok1 and ok2,
               f"恢复nodes={len(g2._nodes)}(期望1), edges={len(g2._edges)}(期望1), mastery={n.mastery:.3f}, 幂等集一致={g2._seen_events==g._seen_events}")


# ---------------- 附加：待确认/未确认完全排除出队 ----------------
def test_exclude_pending():
    g = ErrorGraph("tx")
    g.ingest_verdict({"knowledge_point_id": "KP-OUT", "fragment": "s", "type": "t"}, "pass", True, "ev-x1")
    g.ingest_verdict({"knowledge_point_id": "KP-IN", "fragment": "s2", "type": "t"}, "pass", False, "ev-x2")
    q = g.get_review_queue()
    ids = [r["kp_id"] for r in q]
    ok = ("KP-OUT" not in ids) and ("KP-IN" in ids)
    record("待确认排除出队", ok, f"出队={ids} (KP-OUT[uncertain]应被排除, KP-IN[确定]应保留)")


def main():
    test_priority()
    test_queue_sort()
    test_ingest_quadrants()
    test_edges()
    test_confirm_c2()
    test_aggregate()
    test_rewrite_quadrant()
    test_concurrency()
    test_persistence()
    test_exclude_pending()

    print("=" * 70)
    print(f"{'3.4 图谱数据层验证（2.4 §九 9 项 + 附加）':^60}")
    print("=" * 70)
    all_ok = True
    for name, ok, detail in RESULTS:
        mark = "✅" if ok else "❌"
        all_ok = all_ok and ok
        print(f"  {mark} [{name}]")
        if detail:
            print(f"      {detail}")
    print("-" * 70)
    print(f"结果：{sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} 通过")
    print("PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())