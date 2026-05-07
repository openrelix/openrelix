# Asset Generation Template

Use this after a task review markdown has been written. Human-facing fields should follow the configured runtime language. Keep enum values canonical.

## Source

- Review path:
- Date:
- Task:
- Repo or workspace:
- Source window IDs:

## Model value judgment

- Decision: generate | ask | skip
- Candidate types: memory | playbook | template | automation | skill
- Confidence: high | medium | low
- Reuse trigger:
- Evidence:
- Privacy risk:
- Why not just keep the review:

## User confirmation prompt

Use a concise prompt before generating reusable memory rows or artifact files, unless the user explicitly requested automatic generation.

```text
我判断这次复盘有可复用价值，建议生成：
- 类型：
- 范围：
- 产物路径：
- 原因：

是否生成？如果只想保留复盘，我会跳过资产生成。
```

## Memory item row shape

Use this only after confirmation. Store sanitized durable facts, not raw transcript text.

```json
{
  "date": "YYYY-MM-DD",
  "language": "zh",
  "source": "memory_review",
  "bucket": "durable",
  "scope": "global",
  "injection_policy": "global_context",
  "project_key": "",
  "project_label": "",
  "title": "",
  "memory_type": "procedural",
  "priority": "medium",
  "value_note": "",
  "source_window_ids": [],
  "source_review_path": "",
  "keywords": [],
  "storage_quality_score": 0,
  "storage_quality_reason": "confirmed memory-review assetization"
}
```

## Asset registry row shape

```json
{
  "id": "",
  "title": "",
  "type": "playbook",
  "domain": "general",
  "scope": "personal",
  "status": "active",
  "created_at": "YYYY-MM-DD",
  "updated_at": "YYYY-MM-DD",
  "source_task": "",
  "source_review_path": "",
  "reuse_count": 0,
  "minutes_saved_total": 0,
  "value_note": "",
  "artifact_paths": [],
  "tags": [],
  "notes": ""
}
```

## Skip shape

Use this when no asset is generated.

```text
Assetization decision: skip
Reason:
Signals to watch for future reuse:
```
