# 马跑跑 · AI 马拉松训练规划助手

针对校园马拉松社团新人跑者：运动知识欠缺、专业训练建议与智能硬件强绑定、计划容易被天气/身体/时间打断。以 VDOT 强度体系为核心，规则引擎生成 16 周科学课表，AI 在严格边界内参与解释与微调，护栏层独立校验——**数值计算确定性，模型输出必验尸**。

## 为什么是"规则引擎为主、LLM 为辅"

训练量的数值错误会直接造成运动伤害，不允许概率性输出。所以：

- **规则引擎（VDOT 查表 + 周期模板）**：唯一数值来源，生成和修改课表
- **LLM 只做两件事**：① 把结果翻译成人话（解释层）② 在硬边界内微调课表（微调层）
- **不信任 prompt，只信任校验**：LLM 输出必须过代码硬校验，越界整体拒收、自动降级规则模板，体验不断档

## 架构

```
交互层  React H5（今日卡 / 周审视 / 指标看板 / 注册登录 / 城市切换） → app/
应用层  FastAPI 接口 + 定时任务（周快照冻结 / 天气刷新）           → server/main.py
知识层  rules（VDOT 查表/配速区间/增幅/80/20）                     → server/knowledge/
        explain（LLM 解释层：4 场景文案 + 数值断言防幻觉）           → server/explain/
        tune（LLM 微调层：非关键课 ±10%、周总量 ±3%、关键课禁动）    → server/tune.py
适应层  adapt（候选生成 → 护栏过滤 → 打分排序）                    → server/adapt/
护栏层  guard（三条红线纯函数，独立包，不依赖 adapt）               → server/guard/
数据层  SQLAlchemy（开发默认 SQLite，切库改 MAPAOPAO_DB_URL）
外部    Open-Meteo 天气（体感温度口径 + 失败降级）
```

## AI 的两层参与（本项目的核心设计）

**解释层 `server/explain/`**：今日卡原因行、周审视点评、劝降话术、重排解释 4 个场景。所有数字来自规则引擎，LLM 输出逐条过数值断言（`assert_no_hallucination`：输出里每个数字必须存在于给定 facts），编一个数就整体作废降级。

**微调层 `server/tune.py`**：每周首次打开今日卡时，把本周课表 + 坏天气日子 + 酸痛情况喂给模型，只允许两种操作——非关键课移到本周另一天 / 非关键课跑量 ±10%（0.5km 取整）。硬校验：关键课禁动、周总量变化 ≤3%、每周最多 3 个操作。**任何一条越界，整个方案拒收**。被调过的课在周审视页显示「AI 微调」徽标。

## 运行

```bash
npm install                 # 根目录（concurrently）
npm --prefix app install    # 前端依赖
python -m venv .venv && .venv/Scripts/pip install fastapi "uvicorn[standard]" sqlalchemy apscheduler httpx pytest pydantic pyyaml

npm run dev                 # 同时拉起后端(8000) + 前端(7100)，/v1 /jobs 由 Vite 代理
npm run dev -- --port 7200  # host/port 参数转发给 Vite
npm test                    # 74 个 pytest 用例（规则/护栏/重排锚点/LLM 数值断言/AI 微调边界）
npm run build               # 前端生产构建
```

Windows 也可以直接双击 `start.bat`（自动拉起前后端并打开浏览器），`stop.bat` 关闭。

## 部署上线

```bash
npm --prefix app run build      # 产出静态文件到 app/dist/
npm --prefix app run preview    # 本地验证产物（默认 4173 端口）
```

- **前端**：`app/dist` 是纯静态文件，交给任意静态托管即可（Nginx / Vercel / 宝塔面板）。注意 SPA 路由需配置 fallback：所有 404 路径回退到 `index.html`。
- **后端**：任意支持 ASGI 的主机运行 `uvicorn server.main:app --host 0.0.0.0 --port 8000`，生产库通过环境变量 `MAPAOPAO_DB_URL` 切到 PostgreSQL / MySQL。
- **反向代理**：把 `/v1`、`/jobs` 转发到 8000 端口，静态资源走 dist；前端 `src/lib/api.ts` 里的 API 地址按需改为线上域名。
- **安全提醒**：`.env` 只留在服务器上且权限收紧；生产环境务必关闭 Vite 代理，用 Nginx 统一收口，避免 8000 端口直接对外暴露。

## 接入 LLM（可选，不配也能跑）

复制 `.env.example` 为 `.env`，填入 key 即可（OpenAI 兼容协议，DeepSeek / Kimi 等零改动切换）：

```bash
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-你的key
LLM_MODEL=deepseek-flash
```

未配置时所有 AI 文案与微调自动降级为规则模板。每次调用都埋点留痕（source=llm/template），指标页可视化「AI 文案使用率」与「护栏有效性」。

### RAG + 案例召回 + Prompt 版本化

用户打卡填入实际训练量后（超额完成 >110% / 不足一半），系统自动触发三层 AI 决策增强：

1. **RAG 知识检索**（`server/rag.py`）：训练学原则切块带标签（超量恢复、10% 增量原则、80/20、缺口处理等），中文 bigram 检索 top-3 注入 prompt——零外部依赖，不引 embedding 服务；
2. **案例召回**（`ai_case` 表）：每次偏差触发都记录「情境 + AI 决策 + 检索上下文」，周末按执行率回填 good/bad；相似情境（同阶段 + 同触发 + 偏差率接近）召回历史案例，AI 优先复用被验证有效的调整；
3. **Prompt 版本化调优**（`server/prompts.py`）：prompt 带版本号（tune_v1/v2）埋点，离线评测 `scripts/eval_tune.py` 回放黄金集对比通过率。

主权边界不变：检索结果只进 prompt 做参考，课表改动仍由 `tune.validate_ops` 硬校验把关（非关键课、±10%、周变化 ≤3%），减量周 AI 不干预。

## PRD 关键约束的落点

| PRD 约束 | 实现 |
| --- | --- |
| LLM 不参与任何数值计算 | `server/knowledge/rules.py` 唯一数值来源；数值断言测试兜底 |
| AI 微调不伤及关键课 | `server/tune.py` 硬校验：key 课禁动、±10%、周 ±3%，越界拒收 |
| 缺练不跨周补账 | `template_16w.yaml: compensate_across_week: false`，缺口标记降载不删除 |
| 护栏独立且不可绕过 | `server/guard/` 独立包，CI 式测试 `test_guard_does_not_import_adapt` |
| 执行率分母取周初快照 | `plan_week_snapshot` 表，周一 00:00 冻结，重排不改 |
| 体感温度而非气温 | NOAA Heat Index，`apparent_max(30,40) < 30 <= apparent_max(30,85)` 锚点测试 |
| 天气/LLM 挂掉不阻塞 | STALE + confidence=low；模板文案/规则课表降级 |
| 敢劝降目标 | 连续 3 周关键课 <50% → 三选项（半马/延期/坚持），决定权在用户 |

## 安全说明

- `.env`（含 API key）已被 `.gitignore` 排除，仓库内只有 `.env.example` 占位模板
- 账号密码 PBKDF2-SHA256 哈希存储；接口带登录态校验与注册/登录限流
- 注销账号需密码确认，删除全部个人数据
