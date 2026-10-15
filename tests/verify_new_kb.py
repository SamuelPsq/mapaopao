# -*- coding: utf-8 -*-
"""校验 injury-rehab.md 知识块能被线上检索实现（server.rag.retrieve）正常命中。

直接 import 生产代码路径回放，不再复制打分逻辑——打分算法演进（如 IDF 加权）
后本脚本自动跟随，避免评测与线上分叉。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

QUERIES = [
    ("膝盖下面疼，起跑发力痛", "inj_patellar_tendon"),
    ("髋外侧痛，侧卧压着疼", "inj_trochanteric_bursitis"),
    ("冲刺的时候小腿后侧像撕裂一样痛", "inj_calf_strain"),
    ("大腿后侧痛，后蹬的时候痛", "inj_hamstring_strain"),
    ("跑步疼几分要停，晚上躺着也疼怎么办", "inj_pain_scale"),
    ("刚受伤要怎么处理，冰敷吗", "inj_rice"),
    ("伤好了想恢复力量训练，做哪些动作", "inj_strength_menu"),
    ("跑后膝盖痛怎么办", "inj_cause_pfp"),
]


def main():
    from server import rag
    print(f"KB 块数：{len(rag.KB)}")
    ok = True
    for q, expect in QUERIES:
        top = rag.retrieve(q, k=3)
        ids = [x["id"] for x in top]
        hit = expect in ids
        ok &= hit
        print(f"{'✓' if hit else '✗'}  [{q}]  top3={[(x['id'], round(x['score'],3)) for x in top]}")
    print("全部通过" if ok else "存在未命中", file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
