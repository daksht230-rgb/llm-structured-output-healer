# LLM Structured Output Healer

A zero-dependency, deterministic **JSON Repair & Schema Coercion API** that rescues broken, truncated, or markdown-wrapped LLM outputs before they crash downstream application code.

## Why This Exists
Even with structured-output prompts, LLMs regularly emit:
- Markdown code fences (` ```json ... ``` `) and conversational filler text
- Unquoted keys (`{status: 'ok'}`), single quotes, or trailing commas
- Truncated JSON strings missing closing braces/brackets
- Type mismatches like `"42"` instead of `42` or `"true"` instead of `true`

`LLM Structured Output Healer` extracts, repairs, coerces types against an optional schema, and logs every repair step applied.

## Key Capabilities
- **Safe Fence & Payload Extraction:** Strips markdown wrappers and isolates balanced `{...}` or `[...]` JSON structures without breaking on nested quotes/escapes.
- **Deterministic Syntax Repair:** Fixes unquoted keys, single quotes, Python/JS literals (`True`, `False`, `None`), trailing commas, and unclosed brackets.
- **Lightweight Schema Coercion:** Casts fields to `int`, `float`, `bool`, `str`, or `list` and flags missing required fields.

## Quick Start

```bash
pip install -r requirements.txt
uvicorn main:app --port 8002 --reloadcurl -X POST http://127.0.0.1:8002/v1/output/heal \
  -H "Content-Type: application/json" \
  -d '{
    "raw_output": "```json\n{retries: '\''3'\'', enabled: True,}\n```",
    "expected_schema": {"retries": "int", "enabled": "bool"}
  }'
