# 配方替代过敏原追踪（Allergen Tracker）

面向食品研发的配方替代过敏原追踪 Web 应用。录入配方版本、原料及替代关系后，系统按**有向图递归**计算替代链带来的过敏原影响，**自动检测循环替代并标记每条受影响路径**，支持两版配方差异对比与 JSON 导入导出。

- 后端：Python + FastAPI + SQLite（无需第三方账号 / 外部服务）
- 前端：原生 HTML / CSS / JS 单页应用
- 端口：`8000`（页面与 API 同端口）

## 一键启动

### 方式一：Docker Compose（推荐）

```bash
docker compose up --build
```

### 方式二：Docker

```bash
docker build -t allergen-tracker .
docker run -p 8000:8000 -v allergen_data:/data allergen-tracker
```

### 方式三：本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后访问：

- Web 页面：http://localhost:8000
- API 文档（Swagger）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/health

SQLite 数据默认保存在容器 `/data/app.db`（Compose 已挂载命名卷持久化）；本地运行保存在 `./data/app.db`，可用环境变量 `DB_PATH` 覆盖。

## 功能说明

| 模块 | 说明 |
| --- | --- |
| 原料管理 | 录入原料及其直接过敏原（增删改查） |
| 配方与版本 | 一个配方下可建多个版本，每个版本包含若干原料行（用量 + 单位） |
| 替代关系 | 有向边「原料 A → 替代品 B」，允许成环，系统自动检测并横幅告警 |
| 过敏原分析 | 对指定版本：直接过敏原 + 沿替代链递归引入的过敏原（逐条路径归因）+ 循环路径标记 |
| 版本对比 | 两版本间原料新增 / 移除 / 用量变化，以及直接与引入过敏原的增减 |
| 导入 / 导出 | 全量数据 JSON 导出（下载文件）与按名称合并式导入；页面内置示例数据 |

### 替代链分析逻辑

- 替代关系构成有向图，从配方中每种原料出发做深度优先遍历；
- 路径 `A → B → C` 表示 A 可被 B 替代、B 又可被 C 替代，B 与 C 的过敏原均计入「替代链引入」；
- 遍历中若遇到当前路径上已出现的节点，记为**循环路径**（如 `豆奶 → 杏仁奶 → 燕麦奶 → 豆奶`），在结果中以红色标记，且不再深入以避免无限递归；
- 汇总时每个可达替代品的过敏原归因到到达它的最短路径。

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/ingredients` | 原料列表 / 新建 |
| PUT/DELETE | `/api/ingredients/{id}` | 修改 / 删除原料 |
| GET/POST | `/api/recipes` | 配方列表（含版本）/ 新建 |
| DELETE | `/api/recipes/{id}` | 删除配方（级联版本） |
| POST | `/api/recipes/{id}/versions` | 新建版本（含原料行） |
| GET | `/api/versions` | 所有版本（供下拉选择） |
| GET/PUT/DELETE | `/api/versions/{id}` | 版本详情 / 更新 / 删除 |
| GET/POST | `/api/substitutions` | 替代关系列表 / 新建 |
| DELETE | `/api/substitutions/{id}` | 删除替代关系 |
| GET | `/api/substitutions/cycles` | 检测全图替代循环 |
| GET | `/api/versions/{id}/allergen-analysis` | 版本过敏原影响分析 |
| GET | `/api/diff?a={id}&b={id}` | 两版本差异对比 |
| GET | `/api/export` | 导出全量 JSON |
| POST | `/api/import` | 导入 JSON（按名称合并） |

## 导入 / 导出 JSON 格式

```json
{
  "ingredients": [
    { "name": "全脂牛奶", "allergens": ["牛奶", "乳糖"], "notes": "" }
  ],
  "substitutions": [
    { "from": "全脂牛奶", "to": "豆奶", "reason": "无乳配方" }
  ],
  "recipes": [
    {
      "name": "经典蛋糕",
      "description": "门店基础款",
      "versions": [
        {
          "version_label": "v1.0",
          "notes": "原始配方",
          "items": [
            { "ingredient": "全脂牛奶", "quantity": 200, "unit": "ml" }
          ]
        }
      ]
    }
  ]
}
```

导入按名称合并：同名原料 / 配方 / 版本会被更新（版本原料行整体替换），引用不存在的原料会跳过并在响应 `warnings` 中说明。

## 项目结构

```
├── app/
│   ├── main.py            # FastAPI 应用与全部 API 路由
│   ├── analysis.py        # 替代链有向图递归分析、循环检测
│   └── static/            # 原生前端（index.html / style.css / app.js）
├── data/                  # SQLite 数据目录（运行时生成）
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
