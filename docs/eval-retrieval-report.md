# 检索方案对比实验：bigram vs embedding

- 模型：BAAI/bge-small-zh-v1.5（本地 sentence-transformers）
- 题集：20 条模拟真实用户口语 query，覆盖 3 个知识源，含跨文档干扰项
- 指标：hit@1 / hit@3（期望知识块是否进入 top-3）

## 结果

bigram（线上方案）：hit@1 = 15/20，hit@3 = 19/20
embedding（对照组）：hit@1 = 17/20，hit@3 = 20/20

## 逐题明细

```
[  ] 膝盖下面疼，起跑发力痛
    期望 inj_patellar_tendon
    bigram    : [('inj_patellar_tendon', 0.3), ('kb_race_pacing', 0.2), ('kb_race_warmup', 0.2)]
    embedding : [('inj_cause_pfp', 0.6526), ('inj_patellar_tendon', 0.6267), ('inj_cause_chain', 0.6091)]
[  ] 髋外侧痛，侧卧压着疼
    期望 inj_trochanteric_bursitis
    bigram    : [('inj_trochanteric_bursitis', 1.222), ('inj_itbs', 0.667), ('inj_cause_chain', 0.333)]
    embedding : [('inj_trochanteric_bursitis', 0.6109), ('inj_itbs', 0.5825), ('inj_cause_chain', 0.5819)]
[  ] 冲刺的时候小腿后侧像撕裂一样痛
    期望 inj_calf_strain
    bigram    : [('inj_calf_strain', 0.571), ('inj_hamstring_strain', 0.286), ('kb_during_run', 0.143)]
    embedding : [('inj_calf_strain', 0.603), ('inj_patellar_tendon', 0.5378), ('inj_rice', 0.5334)]
[  ] 大腿后侧痛，后蹬的时候痛
    期望 inj_hamstring_strain
    bigram    : [('inj_hamstring_strain', 0.727), ('inj_trochanteric_bursitis', 0.273), ('kb_during_run', 0.182)]
    embedding : [('inj_hamstring_strain', 0.6319), ('inj_trochanteric_bursitis', 0.6009), ('inj_itbs', 0.5994)]
[  ] 跑步疼几分要停，晚上躺着也疼怎么办
    期望 inj_pain_scale
    bigram    : [('inj_pain_scale', 0.312), ('inj_rice', 0.188), ('kb_after_run', 0.125)]
    embedding : [('kb_taboo_pain_through', 0.7238), ('inj_pain_scale', 0.7137), ('ugc_stretch_after', 0.6928)]
[  ] 刚受伤要怎么处理，冰敷吗
    期望 inj_rice
    bigram    : [('inj_rice', 0.909), ('kb_rehab_early', 0.273), ('inj_mtss', 0.273)]
    embedding : [('inj_rice', 0.6301), ('kb_deload_rule', 0.5923), ('kb_rehab_early', 0.5681)]
[  ] 伤好了想恢复力量训练，做哪些动作
    期望 inj_strength_menu
    bigram    : [('inj_strength_menu', 0.667), ('kb_soreness', 0.267), ('ugc_sleep_train_part', 0.267)]
    embedding : [('inj_strength_menu', 0.7164), ('kb_rehab_return', 0.6706), ('ugc_miss_one_ok', 0.6234)]
[  ] 膝盖外侧痛，下坡的时候加重
    期望 inj_itbs
    bigram    : [('inj_itbs', 0.75), ('inj_trochanteric_bursitis', 0.417), ('inj_cause_pfp', 0.25)]
    embedding : [('inj_itbs', 0.6351), ('inj_cause_chain', 0.6082), ('inj_patellar_tendon', 0.6012)]
[  ] 晨起下床第一步足跟剧痛
    期望 inj_cause_plantar
    bigram    : [('inj_cause_plantar', 1.2), ('inj_achilles', 0.2), ('inj_load_return_rule', 0.2)]
    embedding : [('inj_cause_plantar', 0.4918), ('ugc_sleep_deprivation_risk', 0.4468), ('inj_when_to_see_doctor', 0.4235)]
[  ] 小腿骨内侧一片酸胀，按压一大片都酸
    期望 inj_mtss
    bigram    : [('inj_mtss', 0.812), ('ugc_pain_vs_soreness', 0.125), ('inj_overuse_review', 0.125)]
    embedding : [('kb_soreness', 0.6076), ('inj_calf_strain', 0.6029), ('inj_mtss', 0.5983)]
[  ] 这周跑量可以比上周加多少
    期望 kb_10pct
    bigram    : [('inj_cause_load_spike', 0.455), ('kb_10pct', 0.364), ('kb_stimulus', 0.273)]
    embedding : [('kb_10pct', 0.7131), ('kb_auxcap', 0.6291), ('kb_taper', 0.625)]
[  ] 长距离第二天可以排间歇吗
    期望 kb_longrun_rest
    bigram    : [('kb_keysession', 0.545), ('kb_longrun_rest', 0.545), ('kb_after_run', 0.455)]
    embedding : [('kb_longrun_rest', 0.6347), ('ugc_why_long_run', 0.62), ('kb_fuel', 0.5226)]
[  ] 赛前一周要减量多少
    期望 kb_taper
    bigram    : [('ugc_why_taper_feel_bad', 0.75), ('kb_taper', 0.5), ('kb_race72', 0.375)]
    embedding : [('kb_taper', 0.737), ('kb_race72', 0.6481), ('ugc_why_taper_feel_bad', 0.6038)]
[  ] 雾霾天还能出门跑步吗
    期望 kb_aqi
    bigram    : [('kb_aqi', 0.444), ('kb_longrun_rest', 0.111), ('kb_winter', 0.111)]
    embedding : [('kb_aqi', 0.5782), ('ugc_why_alternate_days', 0.5503), ('ugc_why_long_run', 0.4964)]
[  ] 落下了一次课要不要补回来
    期望 kb_skip
    bigram    : [('kb_skip', 0.273), ('ugc_miss_one_ok', 0.273), ('kb_taboo_cram_train', 0.182)]
    embedding : [('kb_skip', 0.6492), ('ugc_miss_one_ok', 0.6338), ('kb_gap', 0.5822)]
[  ] 天气太热强度课怎么调整
    期望 kb_weather
    bigram    : [('kb_weather', 0.5), ('kb_taboo_daily_intensity', 0.4), ('kb_taper', 0.3)]
    embedding : [('kb_weather', 0.7364), ('ugc_bad_day_easy', 0.5952), ('kb_deload_rule', 0.5478)]
[  ] 辅助课最多能跑多长
    期望 kb_auxcap
    bigram    : [('kb_compress', 0.5), ('kb_auxcap', 0.5), ('kb_8020', 0.25)]
    embedding : [('kb_auxcap', 0.7127), ('kb_8020', 0.5885), ('kb_fuel', 0.5729)]
[  ] 没有心率表怎么判断跑步强度
    期望 ugc_talk_test
    bigram    : [('ugc_talk_test', 0.667), ('kb_hr_easy', 0.417), ('ugc_pain_vs_soreness', 0.333)]
    embedding : [('ugc_talk_test', 0.7561), ('kb_hr_easy', 0.7311), ('ugc_compare_others', 0.5962)]
[  ] 最近很厌跑不想练了怎么办
    期望 ugc_burnout_period
    bigram    : [('ugc_burnout_period', 0.364), ('ugc_bad_day_easy', 0.182), ('inj_rice', 0.182)]
    embedding : [('ugc_burnout_period', 0.7331), ('ugc_side_stitch', 0.5815), ('ugc_sleep_deprivation_risk', 0.5774)]
[ B] 睡不够还要不要硬起来晨跑
    期望 ugc_sleep_deprivation_risk
    bigram    : [('ugc_morning_run_check', 0.273), ('kb_compress', 0.182), ('kb_during_run', 0.182)]
    embedding : [('ugc_sleep_deprivation_risk', 0.6788), ('ugc_morning_run_check', 0.6629), ('ugc_sleep_train_part', 0.5773)]
```
