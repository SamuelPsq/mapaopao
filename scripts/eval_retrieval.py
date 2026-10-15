# -*- coding: utf-8 -*-
"""检索方案对比实验：bigram（线上方案）vs embedding（BGE-small-zh-v1.5）。

用法：.venv/Scripts/python.exe scripts/eval_retrieval.py
产物：打印逐题对比 + 汇总指标，并写出 docs/eval-retrieval-report.md。

目的：量化两种检索方案在真实用户口语 query 上的 hit@1 / hit@3 差异，
为「为什么线上用 bigram + 标签加权」的设计决策提供数据支撑。
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(BASE, "docs")
MODEL = "BAAI/bge-small-zh-v1.5"
TOP_K = 3

# ---------------------------------------------------------------- 题集
# (query, 期望命中的知识块 id)——模拟真实用户口语问法，含跨文档干扰项
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

# ---------------------------------------------------------------- 知识库加载（与 server/rag.py 同逻辑）
def parse_doc(path, source):
    chunks, cur = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("### "):
                head = line[4:].split(" · ")
                cur = {"id": head[0].strip(), "name": head[1].strip() if len(head) > 1 else "",
                       "source": source, "tags": [], "text": ""}
                chunks.append(cur)
            elif cur is not None and line.startswith("- tags:"):
                cur["tags"] = [t.strip() for t in line[7:].split(",") if t.strip()]
            elif cur is not None and line.startswith("- text:"):
                cur["text"] = line[7:].strip()
    return [c for c in chunks if c["id"] and c["text"]]


def load_kb():
    kb = []
    for fname, source in [("training-principles.md", "训练原则"),
                          ("running-experience.md", "跑者经验"),
                          ("injury-rehab.md", "伤病康复")]:
        kb.extend(parse_doc(os.path.join(DOCS, fname), source))
    return kb


# ---------------------------------------------------------------- 方案 A：bigram（线上实现逐行复制）
def _bigrams(text):
    text = re.sub(r"\s+", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def bigram_rank(query, chunks):
    q = _bigrams(query)
    scored = []
    for c in chunks:
        text_bg = _bigrams(c["text"])
        tag_str = "".join(c["tags"])
        hit = sum((2 if g in tag_str else 1) if (g in tag_str or g in text_bg) else 0 for g in q)
        scored.append((hit / len(q) if q else 0.0, c["id"]))
    scored.sort(key=lambda x: -x[0])
    return [(i, s) for s, i in scored[:TOP_K] if s > 0.05]


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


# ---------------------------------------------------------------- 评测
def hits_at(rank_list, expected):
    ids = [i for i, _ in rank_list]
    return (expected in ids[:1], expected in ids)


def main():
    kb = load_kb()
    print(f"知识块总数：{len(kb)}")

    from sentence_transformers import SentenceTransformer
    print(f"加载 embedding 模型：{MODEL} ...")
    model = SentenceTransformer(MODEL)

    rows, a1 = a3 = b1 = b3 = n = 0
    lines = []
    for q, exp in QUERIES:
        ra = bigram_rank(q, kb)
        rb = embed_rank(q, kb, model)
        a_h1, a_h3 = hits_at(ra, exp)
        b_h1, b_h3 = hits_at(rb, exp)
        a1 += a_h1; a3 += a_h3; b1 += b_h1; b3 += b_h3; n += 1
        mark = "  " if a_h3 == b_h3 else ("A " if a_h3 else " B")
        lines.append(f"[{mark}] {q}\n    期望 {exp}\n"
                     f"    bigram    : {[(i, round(s,3)) for i, s in ra]}\n"
                     f"    embedding : {rb}")

    print("\n".join(lines))
    summary = (f"\n{'='*60}\n"
               f"bigram    hit@1 = {a1}/{n}  hit@3 = {a3}/{n}\n"
               f"embedding hit@1 = {b1}/{n}  hit@3 = {b3}/{n}")
    print(summary)

    report = ["# 检索方案对比实验：bigram vs embedding\n",
              f"- 模型：{MODEL}（本地 sentence-transformers）",
              f"- 题集：{n} 条模拟真实用户口语 query，覆盖 3 个知识源，含跨文档干扰项",
              f"- 指标：hit@1 / hit@3（期望知识块是否进入 top-3）\n",
              f"## 结果\n\nbigram（线上方案）：hit@1 = {a1}/{n}，hit@3 = {a3}/{n}",
              f"embedding（对照组）：hit@1 = {b1}/{n}，hit@3 = {b3}/{n}\n",
              "## 逐题明细\n\n```", *lines, "```\n"]
    with open(os.path.join(DOCS, "eval-retrieval-report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"\n报告已写出：{os.path.join('docs', 'eval-retrieval-report.md')}")


if __name__ == "__main__":
    main()
