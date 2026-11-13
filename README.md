# 分布式网页采集调度系统

面向多站点网页抽取、代理池调度、反爬对抗、增量采集、失败重试与采集结果校验的分布式采集调度系统。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
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
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 站点管理 | `site` | 采集站点 | 站点编号、域名地址、站点类型 |
| 抽取模板 | `template` | 网页模板 | 模板编号、入口地址、列表选择器 |
| 代理池管理 | `proxy` | 代理节点 | 节点编号、代理地址、代理协议 |
| 采集任务 | `spider` | 采集作业 | 作业编号、目标站点、抽取模板 |
| 调度策略 | `schedule` | 调度规则 | 规则编号、关联站点、调度表达式 |
| 反爬对抗 | `antibot` | 对抗策略 | 策略编号、验证码类型、识别服务 |
| 限速控制 | `ratelimit` | 限速配置 | 配置编号、目标站点、每秒请求上限 |
| 失败重试 | `retry` | 重试策略 | 策略编号、关联任务、最大重试次数 |
| 字段映射 | `extractor` | 映射规则 | 规则编号、源字段名称、目标字段名称 |
| 字段投影 | `projection` | 投影版本 | 版本号、隔离状态、冲突裁决、预演结论 |
| 采集流水线 | `pipeline` | 流水线步骤 | 步骤编号、步骤名称、输入数据源 |
| 结果校验 | `validator` | 校验规则 | 规则编号、校验对象、校验类型 |
| 数据落盘 | `storage` | 存储配置 | 配置编号、存储类型、输出路径 |
| 采集监控 | `monitor` | 监控指标 | 指标编号、关联任务、指标名称 |
| 数据清洗 | `cleanse` | 清洗规则 | 规则编号、目标字段、空值替换 |
| 增量采集 | `delta` | 增量游标 | 游标编号、目标站点、增量字段 |
| 数据分发 | `export` | 分发通道 | 通道编号、输出数据集、目标系统 |
| RPA流程 | `rpaflow` | 自动化流程 | 流程编号、目标页面、操作序列 |
| 登录凭证 | `auth` | 凭证配置 | 凭证编号、目标站点、认证方式 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.tsx`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 投影预演按“源字段 → 默认填充 → 类型转换 → 清洗 → 写入目标”逐步展开；
  转换或清洗失败得到 `null` 并以 `dropped` / `rejected` 状态说明原因，不会写成空字符串。
- 多个写入方争用同一目标（含清洗输出重名）必须先裁决保留谁、落选字段丢弃还是重命名；
  未裁决的目标整体挂起，不按顺序静默覆盖。
- 编辑中版本与已启用版本隔离：已启用版本只读，发布前必须有与当前 revision 一致的通过预演；
  发布只替换现用版本，不改写任务绑定，既有任务继续按原绑定版本执行。

## 字段投影预演

页面 `/projection`，接口 `/api/projection`，规则代码见
`backend/app/services/projection.py` 与 `backend/app/services/projection_engine.py`。

主要接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/projection/meta` | 样本、枚举与任务绑定 |
| GET | `/api/projection/versions` | 版本列表与未裁决冲突数 |
| POST | `/api/projection/versions/{id}/fork` | 从已启用版本派生编辑草稿 |
| POST | `/api/projection/versions/{id}/preview` | 分步预演（编辑中版本可发布，已启用版本只读查看） |
| POST | `/api/projection/versions/{id}/conflicts/{cid}/resolve` | 冲突裁决（保留方 + 丢弃/重命名） |
| POST | `/api/projection/versions/{id}/publish` | 发布闸门：预演未通过或 revision 过期会被拒绝 |
| POST | `/api/projection/bindings/rebind` | 任务显式改绑到已启用版本 |

回归测试：`cd backend && .venv/bin/python -m unittest discover -s tests -v`。

领域：数据工程
子方向：数据采集、爬虫与RPA
