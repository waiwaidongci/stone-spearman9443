# 澄石材料共变研究册

独立材料试件孔隙率/吸水率→秩相关研究→后续试验假设；相关不解释为因果。原生 HTML/CSS/JavaScript 多模块工作席，所有计算由 Python 完成。

## 模拟业务
澄石研究协作组，12名分析师与业务人员，日常使用 `stone-spearman-202611-1.csv` 手工抄表。当前三个痛点：观测与口径不可追溯、重算易出错、结果与复核割裂。上游提供独立合成业务观测；本应用接管数据登记、方法执行和输入冻结；下游负责人形成复测/预算方案，观察员获取复核后的CSV。

核心业务实体：MaterialBatch / Specimen / CorrelationRun / ResearchNote，以及新增的 CalibrationProject / CalibrationInstrument / CalibrationPair / CalibrationRun。源码 model/schema/service/controller/route 与前端 api/store/pages 完成链路，不依赖浏览器本地假数据。

## 仪器校准（吸水率偏差区间）

多台仪器测同一批吸水率时，在“仪器校准”页登记成对偏差区间 `A − B ∈ [下界, 上界]`，选定一台基准仪器，即可求各台相对基准的最紧偏移范围。

- 每条区间被解释为差分约束 `d(a) − d(b) ≤ upper` 与 `d(b) − d(a) ≤ −lower`；对基准做正反两次最短路，得到 `下界 = −最短路径(i→基准)`、`上界 = 最短路径(基准→i)`。直接区间与间接链路自动合并，结果是全部约束下的最紧范围，不是统计置信区间。
- 每条边界的依据链（直接/间接、参与相加的区间与原始依据文字）随结果返回，可逐条回算。
- 区间互相矛盾时求解器检出负权闭环，返回由真实区间组成的闭环、同向相加后的缺口和复核方法，不下发范围，也不允许发布。
- 与基准不连通的仪器单独列出（含同组仪器），不混入范围表。
- 冻结：提交运行时对项目行加锁，按同一 `revision` 读取基准、仪器清单和全部区间，写 SHA256；之后的编辑不改写历史快照。
- 幂等：运行带 `idempotency_key` 且库内唯一；写入中断后重试返回原运行并重新入队原快照，不新增结果。worker 重启时把 queued/running 的校准运行按冻结快照重算。
- 发布：只有 `publish_ready`（无矛盾、无接不到基准的仪器、每条区间都有依据、边界与依据链齐备）才允许负责人发布，发布记录不可覆盖；观察员只能导出已发布结果。

算法与接口的自检：`cd backend && PYTHONPATH=. python3 -m pytest tests -q`，或 `PYTHONPATH=backend python3 backend/scripts/calibration_smoke.py`（本地 SQLite，无需 Docker）。

## 功能与方法
- 材料批次、独立试件录入/编辑/删除；重名记录与非法数值明确拒绝。
- 并列平均秩Spearman与固定seed置换p、散点分组解释。并列值使用平均秩；置换检验假定独立且可交换的试件。相关关系只支持后续假设，不解释为因果。
- 每次运行冻结参数、观测和SHA256。Redis任务由独立worker计算，写DB与共享产物卷；输入修改不会改写历史结果。
- 负责人复核不可覆盖；审计记录登录、数据改动、排队、复核与导出；240请求/分钟限流、全局可恢复错误。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18243 ，API http://localhost:19243/docs 。数据库映射 17743。无需Node构建。可以从中文和带空格目录启动。清理 `docker compose down -v --remove-orphans` 会删除本题演示数据卷。

|角色|账号|密码|权限与典型动作|
|---|---|---|---|
|admin 负责人|admin@demo.local|Research2026!|全部读写、批准复核、下载|
|analyst 分析师|analyst@demo.local|Research2026!|数据修改、创建任务、读取结果及下载；不能复核|
|viewer 观察员|viewer@demo.local|Research2026!|只读；仅可导出已复核/已发布结果|

## 一天的工作剧本
1. 09:00 admin登录，查“澄石·细砂岩批次”。
2. 09:10 analyst核对 `stone-spearman-202611-1.csv` 的seed记录。
3. 09:15 来源录入页检查 `孔隙率 (%)` 等业务字段，必要时编辑。
4. 09:20 分析执行页保留默认参数 `{"resamples": 4999}`，命名本轮研究。
5. 09:21 冻结输入并排队，worker实算，结果页自动刷新。
6. 09:25 阅读核心数值、方法边界、明细与SHA256；发现失败先修数据重跑。
7. 09:35 admin打开业务复核，写明限制与下一步动作，归档后不可覆盖。
8. 09:40 viewer读取复核意见并下载CSV，在操作留痕页核对下载事件。
9. 10:10 analyst在“仪器校准”页登记各吸水率仪器的成对偏差区间与依据，核对基准与修订号。
10. 10:20 冻结校准输入并排队；结果页查看最紧范围、依据链、矛盾闭环与接不到基准的仪器。
11. 10:30 范围与依据齐备后由 admin 发布校准结果；未接通基准或存在矛盾时页面列出阻断项，不能发布。

## 技术与目录
|部分|实现|
|---|---|
|前端|原生ES modules、HTML/CSS、Nginx1.27，无前端框架|
|API|Python3.11、FastAPI0.115、SQLAlchemy2、JWT、PBKDF2密码|
|计算|NumPy2.2、SciPy1.15，固定seed42|
|数据库|MySQL8|
|任务|Redis7持久队列，Python独立worker，心跳healthcheck|

`frontend/src/`分api、stores、types、components/common、hooks、pages、router、utils、constants；`backend/src/`分config、models、schemas、services、controllers、routes、middlewares、utils、constants；`backend/scripts/seed_demo.py`自然键插入、重复不覆盖修改；`backend/migrations/README.md`说明schema版本。

环境变量 `.env.example`：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO。开发时在本地venv安装backend/requirements.txt，设置DATABASE_URL、REDIS_URL、JWT_SECRET再运行uvicorn；前端开发经Nginx代理 `/api/` 到不带前缀的API。

共享枚举：角色admin/analyst/viewer和队列queued/running/completed/failed；后端`src/constants/enums.py`、认证与worker使用相同值；前端`src/types/enums.js`、状态与角色界面使用相同值。Google Fonts：Noto Serif SC + IBM Plex Mono；断网使用serif/monospace回退。

## 算法输入、失败态与复现
输入为选定批次全部观测和参数，输出核心数值、明细、解释；参数经后端严格白名单和范围验证。并列值使用平均秩；置换检验假定独立且可交换的试件。相关关系只支持后续假设，不解释为因果。。空批次拒绝；不完整字段/非有限数/非法计数拒绝；常量相关列与不足样本使任务failed并保留错误。固定seed42、输入checksum相同可复现。独立oracle：对scipy spearmanr相关系数；单调/反向/并列/常量边界。

已知边界：小型本地研究应用，使用合成数据，不含企业SSO与外发邮件；结果由人工复核，不自动执行业务决策；单worker恢复queued/running任务，数据库与Redis间未采用分布式事务；校准运行靠幂等键与冻结快照保证重试不重复。以当前测试目录TESTING.md记录实际验证结果。

MIT License。
