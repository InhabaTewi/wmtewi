# 因幡未梦 AI 机器人 V3 — VSCode Copilot / GPT 实施规格

> 本文件是编码 Agent 的最高优先级实施说明。目标是让 Copilot/GPT 能按任务逐项实现，不需要自行重新设计架构。

## 0. 目标

构建一个双运行模式 AI 机器人：

- 云端 Linux 无 GPU：永久在线，保存 Persona、共享 Memory、Knowledge、Trace、训练元数据，并在本地 GPU 不可用时调用开放 LLM API。
- 本地 Windows + RTX 5090：通过 WSL2 Ubuntu 运行 GPU Worker，承担本地模型推理、LoRA/QLoRA 训练、评测以及后续 ASR/TTS/Vision。
- 两种模式共享同一个 Memory Service、Knowledge Service、Persona Package 和 Trace 系统。
- 所有训练数据与训练过程必须可追溯，支持一键重新训练。

## 1. 强制架构原则

1. **事实不进权重优先**：频繁变化的事实、设定资料放 Knowledge/RAG；模型训练主要学习角色行为与风格。
2. **Memory 云端唯一权威**：本地模型和 API Provider 禁止维护各自独立记忆。
3. **Provider 可拔插**：业务层只依赖 `LLMProvider` 协议，不允许直接依赖 vLLM/OpenAI SDK。
4. **所有 AI 调用可追踪**：每次回复必须生成 `trace_id`，记录实际 Persona/Memory/Knowledge/Model 版本。
5. **训练可重复**：每个训练 run 必须保存 Git commit、DVC revision、base model revision、config、seed、环境快照、评测、adapter hash。
6. **训练与生产隔离**：训练失败不能影响正在运行的机器人。
7. **任何模型写 Memory 必须通过 MemoryService**，不能直接 SQL。

## 2. 推荐仓库结构

```text
inaba-ai/
├─ apps/
│  ├─ control_api/           # 云端 FastAPI
│  └─ gpu_worker/            # 本地 WSL2 Worker
├─ packages/
│  ├─ schemas/               # Pydantic 跨模块 DTO
│  ├─ persona/
│  ├─ memory/
│  ├─ knowledge/
│  ├─ context_builder/
│  ├─ providers/
│  ├─ router/
│  ├─ tracing/
│  ├─ training/
│  ├─ evaluation/
│  └─ integrations/
│     └─ qqbot/
├─ configs/
│  ├─ persona/inaba.yaml
│  ├─ providers.yaml
│  └─ training/inaba_9b_lora.yaml
├─ migrations/
├─ scripts/
│  ├─ bootstrap_cloud.sh
│  ├─ bootstrap_worker.ps1
│  ├─ retrain.ps1
│  └─ promote_model.ps1
├─ tests/
├─ data_manifests/
├─ docker-compose.cloud.yml
├─ pyproject.toml
└─ README.md
```

## 3. 核心 Pydantic Schema

### 3.1 ChatEvent

```python
class ChatEvent(BaseModel):
    event_id: UUID
    trace_id: UUID
    channel: Literal['qq', 'web', 'bilibili', 'voice']
    session_id: str
    user_id: str
    text: str
    timestamp: datetime
    metadata: dict[str, Any] = {}
```

### 3.2 MemoryAtom

```python
class MemoryAtom(BaseModel):
    id: UUID
    type: Literal[
        'preference','stable_fact','relationship','promise',
        'state_change','plot_point','episodic_summary'
    ]
    subject_id: str | None = None
    object_id: str | None = None
    content: str
    scope: str
    importance: float
    confidence: float
    confirmed: bool
    source_event_id: UUID | None
    source_runtime_mode: Literal['local','api','import','human']
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    version: int = 1
    supersedes_id: UUID | None = None
```

### 3.3 AgentContext

```python
class AgentContext(BaseModel):
    persona_version: str
    recent_messages: list[dict]
    memories: list[MemoryAtom]
    knowledge_chunks: list['KnowledgeChunk']
    behavior_examples: list['BehaviorExample']
    runtime_mode: Literal['local','api']
```

### 3.4 AgentResponse

```python
class AgentResponse(BaseModel):
    speech: str
    emotion: str | None = None
    expression: str | None = None
    actions: list['ToolCall'] = []
    memory_candidates: list['MemoryCandidate'] = []
```

## 4. Provider 接口

```python
class LLMProvider(Protocol):
    name: str
    async def health(self) -> bool: ...
    async def generate(
        self,
        messages: list[dict],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel: ...
```

实现：

- `LocalOpenAIProvider`：访问本地 vLLM/SGLang OpenAI-compatible endpoint。
- `ExternalOpenAIProvider`：访问 Qwen/DeepSeek/OpenAI-compatible 云 API。
- `ProviderRouter`：根据 worker heartbeat + health + circuit breaker 选择 provider。

**禁止在业务代码里直接 `from openai import OpenAI`。** 只能 Provider 内部使用。

## 5. 云端数据库

使用 PostgreSQL + pgvector，Alembic 管 migration。

必须包含：

- `persona_versions`
- `sessions`
- `messages`
- `memory_atoms`
- `memory_links`
- `knowledge_documents`
- `knowledge_chunks`
- `knowledge_embeddings`
- `behavior_examples`
- `interaction_traces`
- `feedback`
- `training_candidates`
- `dataset_snapshots`
- `training_jobs`
- `training_runs`
- `model_versions`
- `worker_nodes`

## 6. Context Builder

固定顺序：

```text
Persona System Prompt
→ Safety / Tool Policy
→ Relevant Shared Memory
→ Relevant Knowledge RAG
→ API 模式额外 Behavior Few-shot
→ Recent Conversation
→ Current User Event
```

Local/API 使用同一个 builder。仅 Provider 不同。

## 7. Knowledge Pipeline

### 输入

- `knowledge/` 目录文档
- qqBot 中已有 Search Gateway 抓取后经人工批准的资料
- 角色设定文档
- FAQ / 世界观 / 项目资料

### 流程

```text
raw document
→ normalize markdown
→ sha256
→ document version
→ split by heading + token limit
→ embedding provider
→ pgvector
```

Embedding 不依赖 5090。本阶段默认配置 OpenAI-compatible embedding API；Provider 可替换。

### API

- `POST /api/knowledge/documents`
- `POST /api/knowledge/reindex/{document_id}`
- `GET /api/knowledge/search?q=`
- `DELETE /api/knowledge/documents/{id}`

## 8. Memory Service

### API

- `POST /api/memory/search`
- `POST /api/memory/candidates`
- `POST /api/memory/{id}/confirm`
- `POST /api/memory/{id}/supersede`
- `GET /api/memory/subject/{subject_id}`

模型只能产生 `MemoryCandidate`。真正写入由 MemoryService 负责去重、冲突判断、阈值和 confirmed 状态。

## 9. Trace 与训练数据

每次生成后必须写 `interaction_traces`：

```json
{
  "trace_id": "...",
  "event_id": "...",
  "persona_version": "inaba-12",
  "memory_ids": ["..."],
  "knowledge_chunk_ids": ["..."],
  "behavior_example_ids": ["..."],
  "runtime_mode": "local",
  "provider": "local-vllm",
  "model_id": "inaba-qwen35-9b",
  "model_version": "production@17",
  "prompt_template_version": "3",
  "response": {...},
  "latency_ms": 834,
  "feedback": null,
  "corrected_response": null,
  "approved_for_training": false
}
```

只有满足以下条件的样本才允许进入 Training Candidate：

- 人工 `approved_for_training=true`；或
- 人工提供了 `corrected_response`；或
- 明确配置的高置信规则通过。

禁止默认用所有机器人输出自训练。

## 10. DVC 数据版本

目录建议：

```text
data/
├─ raw/               # 不直接训练
├─ normalized/
├─ curated/
│  ├─ train.jsonl
│  ├─ eval.jsonl
│  └─ behavior_examples.jsonl
└─ manifests/
```

DVC remote 使用云端 SSH 目录：`/srv/inaba/dvc`。

每个 dataset snapshot 必须生成：

```json
{
  "dataset_id": "inaba-sft-20260921-001",
  "dvc_rev": "...",
  "source_trace_ids": ["..."],
  "train_sha256": "...",
  "eval_sha256": "...",
  "created_at": "..."
}
```

## 11. MLflow

云端运行 MLflow OSS：

- backend store: PostgreSQL
- artifacts: `/srv/inaba/mlflow-artifacts`
- model registry aliases: `candidate`, `staging`, `production`, `rollback`

训练 Worker 必须设置 `MLFLOW_TRACKING_URI` 指向云端。

## 12. 一键重训

用户入口：

```powershell
.\scripts\retrain.ps1 -Profile inaba_9b_lora
```

内部等价：

```text
build_dataset_snapshot
→ dvc push
→ create MLflow run
→ collect environment snapshot
→ swift sft
→ evaluate
→ log artifacts/metrics
→ register candidate
```

### training config

```yaml
profile: inaba_9b_lora
base_model: Qwen/Qwen3.5-9B
base_model_revision: PINNED_REVISION
framework: ms-swift
train_type: lora
lora_rank: 16
lora_alpha: 32
learning_rate: 0.0001
num_train_epochs: 2
max_length: 4096
seed: 42
dataset_snapshot: latest-approved
```

**不要在脚本中写死这些参数。**

## 13. Training Run Manifest

训练开始立即写 `run_manifest.json`，结束时补齐结果：

- run_id
- git_commit / git_dirty
- dvc_rev
- dataset_id
- base_model + revision
- training config SHA256
- Python/PyTorch/CUDA/ms-swift/transformers 版本
- GPU name / driver
- random seed
- start/end time
- exit code
- best checkpoint
- adapter SHA256
- eval metrics
- MLflow run URL

## 14. GPU Worker

Worker 只负责 GPU 作业，不保存权威业务状态。

API/WS：

- `POST /worker/register`
- `POST /worker/heartbeat`
- `GET /worker/jobs/next`
- `POST /worker/jobs/{id}/progress`
- `POST /worker/jobs/{id}/complete`

能力上报：

```json
{
  "node": "home-5090",
  "gpu": "RTX 5090",
  "vram_gb": 32,
  "capabilities": ["train", "llm_serve", "eval", "asr", "tts"],
  "local_llm_endpoint": "http://.../v1"
}
```

## 15. Provider Router 状态机

```text
LOCAL_HEALTHY
  └─ failures >= threshold → API_FALLBACK
API_FALLBACK
  └─ local passes N health checks → LOCAL_RECOVERY_TEST
LOCAL_RECOVERY_TEST
  ├─ smoke test pass → LOCAL_HEALTHY
  └─ fail → API_FALLBACK
```

切换 Provider **不能改变 session_id、persona_version 或 Memory scope**。

## 16. 与现有 qqBot 的接口

现有 NoneBot/OneBot 保持登录和消息收发，只替换 AI 对话调用：

```python
reply = await inaba_client.chat(
    channel='qq',
    session_id=..., user_id=..., text=...
)
```

迁移项：

- `cards.json` → Persona import
- `sessions.json` → 可选历史会话导入
- `HybridMemoryRuntime` JSON/JSONL → Memory importer
- `dialogue_turns.jsonl` / `training_examples.jsonl` → Raw/Curated import
- Search Gateway → `web_search` Tool，不等同于静态 Knowledge RAG

## 17. 第一阶段编码任务（Copilot 按顺序执行）

### T01 — Monorepo 与基础 Schema
**创建**：仓库目录、pyproject、Pydantic DTO、基础测试。  
**Done**：`pytest` 通过；跨模块无循环依赖。

### T02 — PostgreSQL/pgvector + Alembic
**创建**：核心表和 Repository。  
**Done**：可一键 migrate up/down；测试库可创建。

### T03 — Persona Service
**创建**：YAML 角色包版本、导入现有 cards.json。  
**Done**：API 可获取 active persona + version。

### T04 — Memory Service
**创建**：MemoryAtom CRUD、search、candidate、confirm、supersede。  
**Done**：同一 memory 可由 local/API 两种 trace 检索到。

### T05 — External API Provider + Context Builder
**创建**：Fallback 机器人。  
**Done**：不依赖本地 GPU 即可完成 QQ/Web 对话。

### T06 — Knowledge Service
**创建**：document ingest、chunk、embedding、pgvector retrieval。  
**Done**：修改知识文档后无需训练即可影响回答。

### T07 — Trace / Feedback
**创建**：完整 trace、👍/👎/corrected_response。  
**Done**：一条回答可追溯全部 context IDs。

### T08 — GPU Worker + Local Provider
**创建**：Worker 注册/心跳/job；接入本地 OpenAI-compatible LLM。  
**Done**：local healthy 自动用本地模型；断开 worker 自动 fallback。

### T09 — qqBot Adapter
**修改**：现有 AI 对话入口转 Control API。  
**Done**：QQ 登录运行链路不变，AI 后端可自动 local/API 切换。

### T10 — DVC Dataset Builder
**创建**：trace → training candidate → curated JSONL → snapshot。  
**Done**：任何 snapshot 可通过 DVC rev 恢复。

### T11 — MLflow + ms-swift Retrain
**创建**：`retrain.ps1`、train runner、环境快照、评测、registry。  
**Done**：单命令产生 Candidate model，MLflow UI 可完整查看 lineage。

### T12 — Promotion / Rollback
**创建**：Promote、切 local model alias、rollback。  
**Done**：无需改代码可在模型版本间切换。

## 18. 第一阶段自动化测试

必须至少包含：

1. local provider 正常 → route local。
2. local provider 超时 → route api。
3. local 恢复 → recovery test 后 route local。
4. local 写入 memory → api 可检索。
5. api 写入 memory → local 可检索。
6. knowledge 文档更新 → 两模式都能检索新版本。
7. trace 中能找到实际 memory_ids / chunk_ids / model_version。
8. dataset snapshot 可通过 DVC 精确恢复。
9. 同一个 run 配置可重新启动训练。
10. Candidate 不会未经 promote 自动替换 production。

## 19. 非目标（第一阶段不要做）

- 不做游戏自动控制。
- 不做复杂多 Agent。
- 不把 Memory 训练进模型作为唯一记忆方式。
- 不做全量历史自动自训练。
- 不做独立向量数据库集群。
- 不要求把现有 qqBot 全部重构成新框架。
- 不在本地 5090 服务端保存唯一的数据副本。

## 20. 完成定义

第一阶段完成时，即使 Windows/5090 整机关闭：

- QQ 仍可登录并收发消息；
- 机器人自动使用开放 LLM API；
- 能读取相同 Persona、Memory 和 Knowledge；
- 能继续产生 trace 和训练候选数据；
- Windows 恢复后可自动重新接管本地推理；
- 管理员可在本地执行一个命令，从指定数据版本完整重训并生成可追溯 Candidate 模型。
