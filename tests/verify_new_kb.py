# -*- coding: utf-8 -*-
"""校验 injury-rehab.md 新写入的知识块能被 _parse_kb_doc + bigram 检索正常命中。"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(BASE, "docs", "injury-rehab.md")


def parse(path):
    chunks, cur = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("### "):
                head = line[4:].split(" · ")
                cur = {"id": head[0].strip(), "tags": [], "text": ""}
                chunks.append(cur)
            elif cur is not None and line.startswith("- tags:"):
                cur["tags"] = [t.strip() for t in line[7:].split(",") if t.strip()]
            elif cur is not None and line.startswith("- text:"):
                cur["text"] = line[7:].strip()
    return [c for c in chunks if c["id"] and c["text"]]


def bigrams(text):
    text = re.sub(r"\s+", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def score(query, chunk):
    q = bigrams(query)
    if not q:
        return 0.0
    text_bg = bigrams(chunk["text"])
    tag_str = "".join(chunk["tags"])
    hit = sum((2 if g in tag_str else 1) if (g in tag_str or g in text_bg) else 0 for g in q)
    return hit / len(q)


chunks = parse(DOC)
print(f"解析到 {len(chunks)} 个知识块")
dup = [c for c in {c['id'] for c in chunks} if sum(1 for x in chunks if x['id'] == c) > 1]
assert not dup, f"id 重复: {dup}"

NEW = {"inj_pain_scale", "inj_rice", "inj_patellar_tendon",
       "inj_trochanteric_bursiasis" if False else "inj_trochanteric_bursitis",
       "inj_calf_strain", "inj_hamstring_strain", "inj_strength_menu"}
ids = {c["id"] for c in chunks}
missing = NEW - ids
assert not missing, f"新块缺失: {missing}"

QUERIES = [
    ("膝盖下面疼，起跑发力痛", "inj_patellar_tendon"),
    ("髋外侧痛，侧卧压着疼", "inj_trochanteric_bursitis"),
    ("冲刺的时候小腿后侧像撕裂一样痛", "inj_calf_strain"),
    ("大腿后侧痛，后蹬的时候痛", "inj_hamstring_strain"),
    ("跑步疼几分要停，晚上躺着也疼怎么办", "inj_pain_scale"),
    ("刚受伤要怎么处理，冰敷吗", "inj_rice"),
    ("伤好了想恢复力量训练，做哪些动作", "inj_strength_menu"),
]
ok = True
for q, expect in QUERIES:
    top = sorted(((score(q, c), c["id"]) for c in chunks), key=lambda x: -x[0])[:3]
    hit = any(i == expect for _, i in top)
    ok &= hit
    print(f"{'✓' if hit else '✗'}  [{q}]  top3={[(round(s,3), i) for s, i in top]}")

print("全部通过" if ok else "存在未命中", file=sys.stderr)
sys.exit(0 if ok else 1)
