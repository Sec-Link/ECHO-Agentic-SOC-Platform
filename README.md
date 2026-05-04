# ECHO-SOC-Platform

ECHO-SOC-Platform 是一个 AI Native 的安全运营中心（SOC）平台，提供告警管理、关联分析、自动化编排、工单与 CMDB 等能力，并集成 AI 助手以提升分析与处置效率。

## 核心能力
- 告警管理与关联分析（Alerts / Correlation）
- SOC 可视化与自定义看板（Dashboards）
- 工单与处置闭环（Tickets）
- 资产与配置管理（CMDB）
- 自动化编排与工作流（Orchestrator / Workflows / Interfaces）
- 集成能力（Integrations，支持可选 Elasticsearch）
- AI Assistant 与 MCP（模型协作与工具编排）

## 技术栈
- **后端**：Django 6 + Django REST Framework + PostgreSQL
- **前端**：Next.js 15 + React 18 + Ant Design
- **部署**：Docker Compose / Kubernetes

## 快速开始（Docker Compose）
### 1) 准备环境变量
```bash
cp env.example .env
```
按需填写以下变量（示例见 `.env`）：
- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`
- `CSRF_TRUSTED_ORIGINS`
- `BACKEND_ORIGIN`
- `ES_HOST` / `ES_USERNAME` / `ES_PASSWORD`（可选）
- `TICKETS_API_BASE` / `TICKETS_API_TOKEN`（可选）

### 2) 开发环境启动
```bash
docker-compose -f docker-compose.dev.yml up --build -d
```
访问：
- 前端：`http://localhost:3000`
- 后端：`http://localhost:8000`

### 3) 生产环境启动
```bash
docker-compose -f docker-compose.prod.yml up --build -d
```
生产环境建议：
- 将 `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS` 设置为真实域名
- 将数据库端口对外暴露配置移除
- 通过反向代理与 HTTPS 进行访问

## 本地开发（非 Docker）
> 仅适用于有本地 PostgreSQL 的环境。

后端：
```bash
cd backend
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

前端：
```bash
cd frontend
npm install
npm run dev
```

## 目录结构
```
.
├── backend/                # Django 后端（API、任务、编排、AI、工单等）
├── frontend/               # Next.js 前端
├── k8s/                    # Kubernetes 部署清单
├── docker-compose.dev.yml  # 开发环境编排
├── docker-compose.prod.yml # 生产环境编排
├── env.example             # 环境变量示例
└── LICENSE.md              # 许可证
```

## API 概览
后端 API 统一以 `/api/v1/` 为前缀，包含认证、告警、关联分析、编排、工单、CMDB、AI Assistant 等模块接口。

## 许可协议
见 [LICENSE.md](LICENSE.md)。该项目基于 Apache 2.0 修改版协议，包含商业使用与 Logo 约束条款。

## 贡献指南
欢迎提交 Issue 与 PR。提交前请确保：
- 变更说明清晰
- 不引入敏感信息
- 与项目风格保持一致
