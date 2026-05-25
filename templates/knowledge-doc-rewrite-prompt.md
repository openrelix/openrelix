# Knowledge Doc Rewrite Prompt

You are rewriting compact OpenRelix summaries into a local knowledge document
draft. The input has already been reduced from raw chat into source-safe
summary data. Do not ask for tools, do not inspect files, do not use network,
and do not invent facts that are not supported by `source_refs`.

Output JSON only, matching `templates/knowledge-doc-schema.json`.

Rules:

- Treat the document as local-only unless a later explicit policy changes it.
- Keep `status` as `draft` and `reviewer_state` as `needs_review` unless a
  human review record is included in the input.
- Keep `visibility.host_context` false.
- Use only these knowledge types: `troubleshooting`, `decision`, `procedure`,
  `project_context`.
- Write reusable business or engineering knowledge, not one-off task progress.
- Prefer project-scoped synthesis over one document per window. When several
  candidates belong to the same `project_key`, group them into stable business
  subtopics and merge evidence across dates/windows when the sources support
  one coherent document.
- Use the provided `draft_docs` as safe starting points. Preserve their
  `source_refs`, `visibility.host_context=false`, privacy fields, and stable ids
  unless you intentionally merge multiple drafts; merged docs must carry the
  union of source refs and a stable project-scoped `canonical_key`.
- Set `generation_mode=llm_rewrite` for model-authored docs. Keep
  `aggregation_scope=project` or `cross_day_project` for project builds, set
  `aggregation_key` to a stable project/subtopic key, and update
  `evidence_window_days` plus `source_window_count` from the merged source refs.
- Preserve evidence in `source_refs`, `source_range`, and `source_contexts`;
  never include raw transcript text or absolute local paths.
- Fill `business_items` with the project/business subtopics represented by the
  doc. Each item must include a stable key, a concise label, a summary, and the
  supporting window ids/dates.
- Keep `feishu_export.status=not_configured` with empty URL/token/error fields
  unless an explicit export result is already included in the input.
- If evidence is weak, output a rejected document with `model_status=not_run`
  or let the caller keep only a rejected candidate.
- Redact private paths, account data, emails, tokens, keys, cookies, and
  non-public URLs.
- Include applicable limits and invalidation conditions in `body_sections.limits`.

The caller provides the only valid input below as sanitized JSON.
