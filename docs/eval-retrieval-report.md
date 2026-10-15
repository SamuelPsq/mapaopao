# 检索方案对比实验：bigram+IDF vs embedding

- 模型：BAAI/bge-small-zh-v1.5（本地 sentence-transformers）
- 题集：21 条模拟真实用户口语 query（含线上 badcase 回归题），覆盖 3 个知识源
- 指标：hit@1 / hit@3（期望知识块是否进入 top-3）；bigram 列直接回放 server.rag.retrieve

## 结果

bigram+IDF（线上）：hit@1 = 18/21，hit@3 = 21/21
embedding（对照组）：hit@1 = 18/21，hit@3 = 21/21

## 逐题明细

```
[  ] 膝盖下面疼，起跑发力痛
    期望 inj_patellar_tendon
    bigram+IDF : [('inj_patellar_tendon', 0.396), ('kb_race_pacing', 0.15), ('kb_race_warmup', 0.15)]
    embedding  : [('inj_cause_pfp', 0.6526), ('inj_patellar_tendon', 0.6267), ('inj_cause_chain', 0.6091)]
[  ] 髋外侧痛，侧卧压着疼
    期望 inj_trochanteric_bursitis
    bigram+IDF : [('inj_trochanteric_bursitis', 1.133), ('inj_itbs', 0.795), ('inj_cause_chain', 0.263)]
    embedding  : [('inj_trochanteric_bursitis', 0.6109), ('inj_itbs', 0.5825), ('inj_cause_chain', 0.5819)]
[  ] 冲刺的时候小腿后侧像撕裂一样痛
    期望 inj_calf_strain
    bigram+IDF : [('inj_calf_strain', 0.493), ('inj_hamstring_strain', 0.262), ('kb_during_run', 0.123)]
    embedding  : [('inj_calf_strain', 0.603), ('inj_patellar_tendon', 0.5378), ('inj_rice', 0.5334)]
[  ] 大腿后侧痛，后蹬的时候痛
    期望 inj_hamstring_strain
    bigram+IDF : [('inj_hamstring_strain', 0.682), ('inj_itbs', 0.322), ('inj_trochanteric_bursitis', 0.239)]
    embedding  : [('inj_hamstring_strain', 0.6319), ('inj_trochanteric_bursitis', 0.6009), ('inj_itbs', 0.5994)]
[  ] 跑步疼几分要停，晚上躺着也疼怎么办
    期望 inj_pain_scale
    bigram+IDF : [('inj_pain_scale', 0.47), ('inj_cause_pfp', 0.196), ('inj_rice', 0.117)]
    embedding  : [('kb_taboo_pain_through', 0.7238), ('inj_pain_scale', 0.7137), ('ugc_stretch_after', 0.6928)]
[  ] 刚受伤要怎么处理，冰敷吗
    期望 inj_rice
    bigram+IDF : [('inj_rice', 0.717), ('inj_mtss', 0.216), ('inj_patellar_tendon', 0.216)]
    embedding  : [('inj_rice', 0.6301), ('kb_deload_rule', 0.5923), ('kb_rehab_early', 0.5681)]
[  ] 伤好了想恢复力量训练，做哪些动作
    期望 inj_strength_menu
    bigram+IDF : [('inj_strength_menu', 0.472), ('inj_cause_chain', 0.169), ('inj_itbs', 0.155)]
    embedding  : [('inj_strength_menu', 0.7164), ('kb_rehab_return', 0.6706), ('ugc_miss_one_ok', 0.6234)]
[  ] 膝盖外侧痛，下坡的时候加重
    期望 inj_itbs
    bigram+IDF : [('inj_itbs', 1.042), ('inj_trochanteric_bursitis', 0.526), ('inj_cause_pfp', 0.348)]
    embedding  : [('inj_itbs', 0.6351), ('inj_cause_chain', 0.6082), ('inj_patellar_tendon', 0.6012)]
[  ] 晨起下床第一步足跟剧痛
    期望 inj_cause_plantar
    bigram+IDF : [('inj_cause_plantar', 1.118), ('inj_achilles', 0.185), ('inj_load_return_rule', 0.158)]
    embedding  : [('inj_cause_plantar', 0.4918), ('ugc_sleep_deprivation_risk', 0.4468), ('inj_when_to_see_doctor', 0.4235)]
[  ] 小腿骨内侧一片酸胀，按压一大片都酸
    期望 inj_mtss
    bigram+IDF : [('inj_mtss', 0.723), ('inj_overuse_review', 0.114), ('inj_stress_fracture', 0.106)]
    embedding  : [('kb_soreness', 0.6076), ('inj_calf_strain', 0.6029), ('inj_mtss', 0.5983)]
[  ] 跑后膝盖痛怎么办
    期望 inj_cause_pfp
    bigram+IDF : [('inj_cause_pfp', 1.388), ('inj_patellar_tendon', 0.536), ('inj_pain_scale', 0.527)]
    embedding  : [('inj_cause_pfp', 0.6735), ('ugc_stretch_after', 0.6712), ('kb_after_run', 0.6524)]
[  ] 这周跑量可以比上周加多少
    期望 kb_10pct
    bigram+IDF : [('inj_cause_load_spike', 0.404), ('kb_10pct', 0.256), ('kb_stimulus', 0.217)]
    embedding  : [('kb_10pct', 0.7131), ('kb_auxcap', 0.6291), ('kb_taper', 0.625)]
[  ] 长距离第二天可以排间歇吗
    期望 kb_longrun_rest
    bigram+IDF : [('kb_longrun_rest', 0.402), ('kb_keysession', 0.36), ('kb_after_run', 0.358)]
    embedding  : [('kb_longrun_rest', 0.6347), ('ugc_why_long_run', 0.62), ('kb_fuel', 0.5226)]
[  ] 赛前一周要减量多少
    期望 kb_taper
    bigram+IDF : [('ugc_why_taper_feel_bad', 0.639), ('kb_taper', 0.426), ('inj_pain_scale', 0.341)]
    embedding  : [('kb_taper', 0.737), ('kb_race72', 0.6481), ('ugc_why_taper_feel_bad', 0.6038)]
[  ] 雾霾天还能出门跑步吗
    期望 kb_aqi
    bigram+IDF : [('kb_aqi', 0.359), ('ugc_first_km_slow', 0.083), ('ugc_bad_day_easy', 0.083)]
    embedding  : [('kb_aqi', 0.5782), ('ugc_why_alternate_days', 0.5503), ('ugc_why_long_run', 0.4964)]
[  ] 落下了一次课要不要补回来
    期望 kb_skip
    bigram+IDF : [('kb_skip', 0.235), ('ugc_miss_one_ok', 0.235), ('kb_taboo_cram_train', 0.174)]
    embedding  : [('kb_skip', 0.6492), ('ugc_miss_one_ok', 0.6338), ('kb_gap', 0.5822)]
[  ] 天气太热强度课怎么调整
    期望 kb_weather
    bigram+IDF : [('kb_weather', 0.389), ('kb_taboo_daily_intensity', 0.231), ('kb_taper', 0.214)]
    embedding  : [('kb_weather', 0.7364), ('ugc_bad_day_easy', 0.5952), ('kb_deload_rule', 0.5478)]
[  ] 辅助课最多能跑多长
    期望 kb_auxcap
    bigram+IDF : [('kb_compress', 0.381), ('kb_auxcap', 0.381), ('kb_8020', 0.191)]
    embedding  : [('kb_auxcap', 0.7127), ('kb_8020', 0.5885), ('kb_fuel', 0.5729)]
[  ] 没有心率表怎么判断跑步强度
    期望 ugc_talk_test
    bigram+IDF : [('ugc_talk_test', 0.541), ('kb_hr_easy', 0.347), ('ugc_pain_vs_soreness', 0.244)]
    embedding  : [('ugc_talk_test', 0.7561), ('kb_hr_easy', 0.7311), ('ugc_compare_others', 0.5962)]
[  ] 最近很厌跑不想练了怎么办
    期望 ugc_burnout_period
    bigram+IDF : [('ugc_burnout_period', 0.34), ('inj_cause_pfp', 0.287), ('inj_pain_scale', 0.287)]
    embedding  : [('ugc_burnout_period', 0.7331), ('ugc_side_stitch', 0.5815), ('ugc_sleep_deprivation_risk', 0.5774)]
[  ] 睡不够还要不要硬起来晨跑
    期望 ugc_sleep_deprivation_risk
    bigram+IDF : [('ugc_sleep_deprivation_risk', 0.308), ('ugc_morning_run_check', 0.251), ('ugc_sleep_train_part', 0.154)]
    embedding  : [('ugc_sleep_deprivation_risk', 0.6788), ('ugc_morning_run_check', 0.6629), ('ugc_sleep_train_part', 0.5773)]
```
