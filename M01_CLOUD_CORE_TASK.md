# M01：因幡未梦 Cloud Core 云端常驻核心开发任务书

> 用途：把本文件连同已经完成 T01-T06 的 `inaba-ai` 代码仓库交给云端编码大模型/Agent。
> 目标：完成“本地开发环境 → 云端常驻核心 → 后续本地 RTX 5090 算力节点”的第一段切换。
> 本里程碑只做 Cloud Core 生产化，不实现 GPU Worker、训练、QQ Adapter、ASR/TTS、Live2D 或游戏。

## 0. 当前前提

本项目 T01-T06 已在本地完成，至少已经具备：
- 项目骨架和公共 Schema；
- PostgreSQL + pgvector 数据层；
- Persona Service；
- Shared Memory Service；
- Cloud LLM/API Provider 形式的弱版机器人；
- Knowledge/RAG Service；
- 本地自动化测试。

必须保留的架构原则：

1. Persona = “未梦是谁、怎么说、怎么行为”。
2. Memory = “未梦经历过什么”，以后本地模型和云端 API 必须共享同一套主记忆。
3. Knowledge = “未梦知道什么”，知识变更不要求重新训练模型。
4. LLM Provider 可替换。云端 API 模型和未来本地 RTX 5090 模型只替换 Provider，不复制 Persona/Memory/Knowledge。
5. 云端服务器从 M01 完成后成为 Persona、Memory、Knowledge、Conversation 等状态的唯一主数据源。
6. 本地 RTX 5090 以后是可拔插算力节点，不持有唯一业务状态。

## 1. 开始修改前必须做的事情

不要直接重构代码。

第一步先完整阅读仓库，并生成：

`docs/M01_CURRENT_STATE_AUDIT.md`

必须说明：

- T01-T06 在实际仓库中的目录和代码映射；
- Core API 实际启动入口；
- 配置加载方式；
- PostgreSQL/pgvector 使用方式；
- Persona 数据结构和存储方式；
- Memory 数据结构、作用域隔离方式和存储方式；
- Knowledge ingestion、chunk、embedding、retrieval 的实际实现；
- LLM Provider 和 Embedding Provider 当前实现；
- migration 机制；
- 测试现状；
- 哪些实现与本任务书假设不同。

如果实际代码与本文目录示例不同，以实际仓库为准，不得为了“看起来一致”而无必要地搬目录或重写已工作的 T01-T06。

完成审计后再实施 M01。

## 2. M01 最终部署拓扑

```text
Internet / Internal Client
          |
        HTTPS
          |
     Caddy/Nginx
          |
      Inaba Core API
          |
  +-------+----------------+----------------+
  |                        |                |
Persona                Memory          Knowledge/RAG
  |                        |                |
  +------------------------+----------------+
                           |
                  PostgreSQL + pgvector
                           |
                   Cloud LLM Provider
                   Cloud Embedding Provider
```

注意：

- PostgreSQL 不允许直接暴露公网；
- Knowledge Worker 如无必要不得暴露公网端口；
- Core API 不直接以裸 8000 端口面向公网；
- 后续 RTX 5090 GPU Worker 通过受认证网络/API 接入 Core，而不是直接访问 PostgreSQL。

## 3. 本里程碑必须实现

### 3.1 Production 配置体系

增加生产环境配置支持，至少包括：

- `APP_ENV=production`
- `DATABASE_URL`
- `LLM_PROVIDER`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `EMBEDDING_PROVIDER`
- `EMBEDDING_BASE_URL`
- `EMBEDDING_API_KEY`
- `EMBEDDING_MODEL`
- `SERVICE_TOKEN` 或等价内部服务认证字段
- 日志级别
- CORS/allowed hosts
- Knowledge 数据目录
- Backup 数据目录

必须提供：

- `.env.example`
- 配置字段说明
- 启动时必要配置校验

禁止把真实密钥写入 Git。

### 3.2 Cloud LLM Provider

必须确保无 GPU 云服务器可以依赖 OpenAI-compatible 或已有开放大模型 API 完成聊天。

要求：

- 使用现有 Provider abstraction；
- 不把某一家模型 API 写死到业务层；
- Provider 错误返回标准化；
- 设置连接和生成超时；
- 为未来 `LocalGPUProvider` 留出一致接口；
- 对 Persona/Memory/Knowledge 层透明。

### 3.3 Cloud Embedding Provider

云端没有 GPU，因此 Knowledge/RAG 不得要求本地 CUDA 才能工作。

如果 T01-T06 当前 embedding 依赖本地模型：

- 抽象/补全 `EmbeddingProvider`；
- 增加 Cloud/API Embedding Provider；
- Knowledge ingestion 和 query embedding 必须都可在无 GPU 环境执行；
- embedding model/version 写入知识索引元数据；
- 模型变更时支持 re-index。

### 3.4 PostgreSQL + pgvector Production 化

要求：

- 数据库使用正式 volume；
- migrations 可重复执行；
- 启动时不通过 `create_all()` 代替 migration；
- 所有 schema 修改进入 migration；
- pgvector extension 初始化自动化；
- 数据库不得开放公网；
- 增加连接池和合理超时；
- `/health/ready` 必须检查数据库可用性。

### 3.5 Persona / Memory / Knowledge 保持统一

不得在 Cloud API 模式下另建一套简化记忆。

必须保证：

```text
request
  -> Persona
  -> retrieve Shared Memory
  -> retrieve Knowledge
  -> assemble context
  -> selected LLM Provider
  -> response
  -> Memory extraction/writeback
```

未来 Local GPU Provider 接入后仍走同一条链，只替换最后的 Provider。

### 3.6 Health / Readiness

至少提供：

- `GET /health/live`
- `GET /health/ready`

`live`：进程活着即可返回成功。

`ready`：至少验证：

- DB；
- pgvector；
- Persona 可加载；
- LLM Provider 配置有效；
- Embedding Provider 配置有效。

不得在每次 ready 请求中产生高成本 LLM 推理。

### 3.7 服务认证

为后续 GPU Worker、QQ Adapter、管理端调用预留服务认证。

M01 最低要求：

- 支持 `Authorization: Bearer <service-token>` 或等价方案；
- health endpoint 可按设计放行；
- 内部管理/导入接口必须认证；
- 不把 token 记录到日志。

### 3.8 数据导入能力

提供幂等或可重复执行的导入工具：

```text
persona import
memory import
knowledge source import / reindex
```

建议至少提供 CLI：

```bash
python -m ... import-persona ...
python -m ... import-memory ...
python -m ... ingest-knowledge ...
python -m ... reindex-knowledge ...
```

实际命令可按现有工程结构调整。

Memory 导入格式必须保存至少：

- stable ID（若已有）；
- type；
- scope；
- user/subject；
- card/persona；
- session（若适用）；
- canonical content；
- importance；
- confidence；
- confirmed；
- source；
- created_at / updated_at。

Knowledge 不建议直接迁移本地 vector 行；优先迁移原始 source + metadata，在云端用 Cloud Embedding Provider 重新生成向量。

### 3.9 Backup / Restore

必须提供：

- `scripts/backup.sh`
- `scripts/restore.sh`
- `docs/BACKUP_RESTORE.md`

至少备份：

- PostgreSQL 业务数据；
- Persona 生产配置/版本；
- Knowledge 原始 source/manifest；
- 生产配置模板（不包含明文 secret）。

恢复必须支持在一套新的测试 stack 上验证，而不是只能原地覆盖。

### 3.10 Docker Production 部署

必须提供等价能力：

```text
deploy/
  docker-compose.prod.yml
  Dockerfile/core
  reverse-proxy/
  scripts/
```

实际目录可遵循现有仓库。

Compose 至少包含：

- core-api；
- postgres + pgvector；
- 必要的 knowledge worker（若项目架构需要）；
- reverse proxy（可选独立或使用宿主机已有服务）。

要求：

- restart policy；
- healthcheck；
- 持久卷；
- 内部网络；
- 不暴露数据库公网端口；
- 日志大小控制；
- production 环境不使用 debug/reload。

### 3.11 HTTPS 反向代理

提供 Caddy 或 Nginx 模板。

如果部署时没有域名：

- 不得伪造域名；
- 允许先以内网/受限 IP 进行 smoke test；
- 文档标出域名和 TLS 后续填写位置。

### 3.12 Smoke Test

提供自动 smoke test，至少测试：

1. `/health/live`
2. `/health/ready`
3. 创建一次 Cloud API 对话
4. Memory 写入
5. 下一轮 Memory 能检索
6. Knowledge 入库
7. Knowledge 检索
8. Docker 重启后 Memory/Knowledge 仍存在

## 4. 云端服务器目录建议

```text
/opt/inaba-ai/                # Git 工作树/部署代码
/opt/inaba-data/postgres/     # PostgreSQL 持久数据（也可 Docker named volume）
/opt/inaba-data/knowledge/    # Knowledge 原始 source / manifest
/opt/inaba-backups/           # 备份
/etc/inaba/inaba.env          # 生产 secrets/config，不进 Git
```

如果服务器已有既定目录规范，可调整，但必须在部署文档说明。

不得修改或覆盖服务器上现有 qqBot、OneBot、NapCat、Lagrange 等目录和 systemd 服务。

## 5. 本里程碑明确禁止实施

不要实现：

- RTX 5090 GPU Worker；
- 本地 Qwen 推理；
- CUDA/vLLM GPU 部署；
- LoRA/QLoRA；
- ms-swift；
- MLflow Training；
- DVC Training Dataset；
- T07 Trace/Feedback 完整训练闭环；
- T08 之后的本地 GPU 推理逻辑；
- T09 QQ Adapter；
- T10 Dataset Builder；
- T11 Training；
- T12 Promotion/Rollback；
- ASR；
- TTS；
- VTube Studio；
- OBS；
- 游戏控制。

可以预留接口，但不得把这些能力实际塞入 M01。

## 6. 与现有 qqBot 的边界

现有 qqBot 暂时保持原运行方式。

M01 不修改 qqBot。

后续 T09 才改为：

```text
QQ
 -> OneBot
 -> NoneBot
 -> Inaba Adapter
 -> Cloud Inaba Core
```

到那时 qqBot 负责 QQ 接入、权限和消息发送，Persona/Memory/Knowledge/LLM 由 Inaba Core 统一负责。

M01 只允许为未来 Adapter 定义稳定的 Core API 契约，不允许现在迁移/删除 qqBot 功能。

## 7. 验收标准

M01 完成必须满足：

- 一台无 GPU Linux 服务器可从空环境部署；
- 服务器无需 CUDA；
- Cloud API 模型可完成未梦对话；
- Persona 生效；
- Shared Memory 生效；
- Knowledge/RAG 生效；
- embedding 在无 GPU 条件可用；
- Docker/服务重启后数据不丢；
- PostgreSQL 不暴露公网；
- 有 health/readiness；
- 有 backup/restore；
- 有 smoke test；
- 所有 secrets 不进入 Git；
- 现有 T01-T06 本地测试仍通过；
- 未修改现有 qqBot；
- 输出完整部署文档。

## 8. 完成后必须输出的文件/文档

至少生成：

- `docs/M01_CURRENT_STATE_AUDIT.md`
- `docs/M01_CLOUD_DEPLOYMENT.md`
- `docs/BACKUP_RESTORE.md`
- `.env.example`
- Production Dockerfile
- `docker-compose.prod.yml` 或等价文件
- migration 更新
- backup/restore scripts
- smoke test
- 本次变更清单
- 已执行测试及结果
- 已知风险
- 下一阶段 T08 GPU Worker 所需 API 契约说明

最后不要自动开始 T08。完成 M01 后停止。
