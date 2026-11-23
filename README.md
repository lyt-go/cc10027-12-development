# 日志采集与事件追踪平台

面向应用日志收集、事件埋点追踪、流式采集与结构化归集的多源日志采集平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 日志源管理 | `log_source` | 日志源 | 源编号、源名称、日志类型 |
| 日志解析 | `log_parser` | 解析规则 | 规则编号、关联源、正则表达式 |
| 事件埋点 | `event_tracker` | 埋点事件 | 事件编号、事件名称、埋点页面 |
| 流式缓冲 | `stream_buffer` | 缓冲配置 | 配置编号、关联源、缓冲区大小 |
| 字段映射 | `field_mapping` | 映射规则 | 规则编号、源字段、目标字段 |
| 采集代理 | `agent_heartbeat` | 采集代理 | 代理编号、部署主机、代理版本 |
| 事件校验 | `event_validate` | 校验规则 | 规则编号、关联事件、字段检查 |
| 日志轮转 | `log_rotate` | 轮转策略 | 策略编号、关联源、轮转间隔 |
| 日志富化 | `enrich_join` | 富化规则 | 规则编号、目标字段、维表来源 |
| 告警规则 | `alert_rule` | 告警规则 | 规则编号、关联源、触发条件 |
| 日志分区 | `log_partition` | 分区规则 | 规则编号、关联源、分区键 |
| 采集位点 | `collect_checkpoint` | 位点记录 | 记录编号、关联源、文件偏移 |
| 窗口去重 | `dedup_window` | 去重窗口 | 窗口编号、关联源、去重键 |
| 聚合计算 | `aggregate_olap` | 聚合规则 | 规则编号、关联源、聚合维度 |
| 格式输出 | `format_output` | 输出配置 | 配置编号、关联源、输出格式 |
| 回溯采集 | `backfill_range` | 回溯任务 | 任务编号、关联源、时间范围 |
| 采样策略 | `sampling_rule` | 采样规则 | 规则编号、关联源、采样方式 |
| 质量诊断 | `log_quality` | 诊断报告 | 报告编号、关联源、解析成功率 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 维表命中归属凭证（部门归属核对）

`app/services/dimension_attestation.py` 为富化补全“可共享部门维表命中归属”的确认过程，
解决线上/离线两个日志源对同一客户登记部门冲突时，立方体取数依据无法回查的问题。

规则：

- 每个日志源只能使用自己的私有部门维表，或以 `shared_readonly` 方式取共享客户主档；
  另一源的私有维表在凭证 `access_scope.forbidden_tables` 中显式记为拒绝。
- 每行日志只确认一个可核对部门（私有优先、共享只读兜底），候选命中与实际命中
  （维表名、版本、访问方式、命中部门）全部写入凭证 `attestations.jsonl`。
- 两个合法维表为同一客户返回不同部门时，行级凭证各自成立，客户级核对判为冲突：
  该客户在部门立方体里只进 `UNRESOLVED` 一个组，不会被提前计入两个部门组。
- 维表更新只追加新版本；既有立方体与旧凭证保留原身份，重算生成新的立方体版本。
- 部门立方体每组保存 `dim_sources_used`（维表来源、版本、访问方式、命中行数）和
  成员凭证号，可回查每组的取数位置；支持用另一源同键凭证复查有效归属。

主要接口（前缀 `/api/dim-attestation`）：

| 方法与路径 | 说明 |
| --- | --- |
| `POST /dim-tables`、`POST /dim-tables/{name}/versions` | 登记部门维表 / 追加新版本 |
| `POST /logs/ingest` | 接入线上或离线日志（含客户键） |
| `POST /attestations`、`POST /sources/{source}/attest` | 单行/整源生成命中归属凭证 |
| `GET /attestations/{id}/recheck` | 按当前维表版本复查旧凭证是否漂移 |
| `GET /customers/{customer_key}/recheck` | 用另一源同键记录复查有效归属 |
| `POST /department-cube`、`GET /department-cube`、`GET /department-cube/versions` | 写/查部门立方体及历史版本 |
| `POST /conflict-demo` | 一键跑通“两源部门冲突→未决单组→更新重算”完整场景 |

验收脚本：`cd backend && .venv/bin/python -I verify_dim_attestation.py`。


领域：数据工程
子方向：数据采集、爬虫与RPA
