# -*- coding: utf-8 -*-
"""检索方案对比实验：线上 bigram+IDF（server.rag）vs embedding（BGE-small-zh-v1.5）。

用法：.venv/Scripts/python.exe scripts/eval_retrieval.py
产物：打印逐题对比 + 汇总指标，并写出 docs/eval-retrieval-report.md。

要点：直接 import 线上实现（server.rag.retrieve）做 A/B，保证评测回放的就是
生产代码路径，而不是复制的逻辑。 badcase 归因 → 修复 → 用本脚本回放验证。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

MODEL = "BAAI/bge-small-zh-v1.5"
TOP_K = 3

# ---------------------------------------------------------------- 题集
# (query, 期望命中的知识块 id)——模拟真实用户口语 query，含跨文档干扰项
QUERIES = [
    # 伤病康复（injury-rehab.md）
    ("膝盖下面疼，起跑发力痛", "inj_patellar_tendon"),
    ("髋外侧痛，侧卧压着疼", "inj_trochanteric_bursitis"),
    ("冲刺的时候小腿后侧像撕裂一样痛", "inj_calf_strain"),
    ("大腿后侧痛，后蹬的时候痛", "inj_hamstring_strain"),
    ("跑步疼几分要停，晚上躺着也疼怎么办", "inj_pain_scale"),
    ("刚受伤要怎么处理，冰敷吗", "inj_rice"),
    ("伤好了想恢复力量训练，做哪些动作", "inj_strength_menu"),
    ("膝盖外侧痛，下坡的时候加重", "inj_itbs"),
    ("晨起下床第一步足跟剧痛", "inj_cause_plantar"),
    ("小腿骨内侧一片酸胀，按压一大片都酸", "inj_mtss"),
    ("跑后膝盖痛怎么办", "inj_cause_pfp"),        # 线上 badcase 回归题
    # 训练原则（training-principles.md）
    ("这周跑量可以比上周加多少", "kb_10pct"),
    ("长距离第二天可以排间歇吗", "kb_longrun_rest"),
    ("赛前一周要减量多少", "kb_taper"),
    ("雾霾天还能出门跑步吗", "kb_aqi"),
    ("落下了一次课要不要补回来", "kb_skip"),
    ("天气太热强度课怎么调整", "kb_weather"),
    ("辅助课最多能跑多长", "kb_auxcap"),
    # 跑者经验（running-experience.md）
    ("没有心率表怎么判断跑步强度", "ugc_talk_test"),
    ("最近很厌跑不想练了怎么办", "ugc_burnout_period"),
    ("睡不够还要不要硬起来晨跑", "ugc_sleep_deprivation_risk"),
]

# ---------------------------------------------------------------- 方案 A：线上实现直接回放
def bigram_rank(query):
    from server import rag
    return [(x["id"], x["score"]) for x in rag.retrieve(query, k=TOP_K)]


# ---------------------------------------------------------------- 方案 B：embedding 余弦相似度
def embed_rank(query, chunks, model):
    import numpy as np
    q_vec = model.encode(["为这个句子生成表示以用于检索相关文章：" + query],
                         normalize_embeddings=True)
    c_vecs = model.encode([c["name"] + "：" + c["text"] for c in chunks],
                          normalize_embeddings=True, show_progress_bar=False)
    sims = (c_vecs @ q_vec[0])
    order = np.argsort(-sims)[:TOP_K]
    return [(chunks[i]["id"], round(float(sims[i]), 4)) for i in order]


def hits_at(rank_list, expected):
    ids = [i for i, _ in rank_list]
    return (expected in ids[:1], expected in ids)


def main():
    from server import rag
    print(f"知识块总数：{len(rag.KB)}（含 IDF 权重，线上实现）")

    from sentence_transformers import SentenceTransformer
    print(f"加载 embedding 模型：{MODEL} ...")
    model = SentenceTransformer(MODEL)

    a1 = a3 = b1 = b3 = n = 0
    lines = []
    for q, exp in QUERIES:
        ra = bigram_rank(q)
        rb = embed_rank(q, rag.KB, model)
        a_h1, a_h3 = hits_at(ra, exp)
        b_h1, b_h3 = hits_at(rb, exp)
        a1 += a_h1; a3 += a_h3; b1 += b_h1; b3 += b_h3; n += 1
        mark = "  " if a_h3 == b_h3 else ("A " if a_h3 else " B")
        lines.append(f"[{mark}] {q}\n    期望 {exp}\n"
                     f"    bigram+IDF : {[(i, round(s,3)) for i, s in ra]}\n"
                     f"    embedding  : {rb}")

    print("\n".join(lines))
    summary = (f"\n{'='*60}\n"
               f"bigram+IDF（线上） hit@1 = {a1}/{n}  hit@3 = {a3}/{n}\n"
               f"embedding（对照组） hit@1 = {b1}/{n}  hit@3 = {b3}/{n}")
    print(summary)

    report = ["# 检索方案对比实验：bigram+IDF vs embedding\n",
              f"- 模型：{MODEL}（本地 sentence-transformers）",
              f"- 题集：{n} 条模拟真实用户口语 query（含线上 badcase 回归题），覆盖 3 个知识源",
              f"- 指标：hit@1 / hit@3（期望知识块是否进入 top-3）；bigram 列直接回放 server.rag.retrieve\n",
              "## 结果\n",
              f"bigram+IDF（线上）：hit@1 = {a1}/{n}，hit@3 = {a3}/{n}",
              f"embedding（对照组）：hit@1 = {b1}/{n}，hit@3 = {b3}/{n}\n",
              "## 逐题明细\n\n```", *lines, "```\n"]
    with open(os.path.join(BASE, "docs", "eval-retrieval-report.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"\n报告已写出：docs{os.sep}eval-retrieval-report.md")


if __name__ == "__main__":
    main()
