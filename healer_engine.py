import re
import json
import time
from typing import Any, Dict, List, Optional, Tuple


class StructuredOutputHealer:
    """
    Deterministic repair & validation engine for noisy or malformed LLM outputs.
    Handles markdown code fences, leading/trailing chatter, unquoted keys,
    single quotes, trailing commas, unclosed braces/brackets, and basic type coercion.
    """

    def __init__(self, max_repair_passes: int = 3):
        self.max_repair_passes = max(1, max_repair_passes)

    def _strip_markdown_fences(self, text: str) -> Tuple[str, bool]:
        stripped = text.strip()
        fenced_pattern = re.compile(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```$", re.DOTALL)
        match = fenced_pattern.match(stripped)
        if match:
            return match.group(1).strip(), True
        return stripped, False

    def _extract_json_candidate(self, text: str) -> Tuple[str, bool]:
        start_obj = text.find("{")
        start_arr = text.find("[")

        candidates = [idx for idx in (start_obj, start_arr) if idx != -1]
        if not candidates:
            return text.strip(), False

        start_idx = min(candidates)
        open_char = text[start_idx]
        close_char = "}" if open_char == "{" else "]"

        depth = 0
        in_string = False
        escape_next = False
        end_idx = -1

        for i in range(start_idx, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == open_char:
                    depth += 1
                elif ch == close_char:
                    depth -= 1
                    if depth == 0:
                        end_idx = i
                        break

        if end_idx != -1:
            extracted = text[start_idx : end_idx + 1]
            return extracted, extracted != text.strip()

        # Truncated output: return from opening brace/bracket to end for bracket healing
        return text[start_idx:].strip(), True

    def _balance_brackets(self, text: str) -> Tuple[str, bool]:
        stack: List[str] = []
        in_string = False
        escape_next = False
        pairs = {"{": "}", "[": "]"}
        closing = {"}": "{", "]": "["}

        for ch in text:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch in pairs:
                    stack.append(ch)
                elif ch in closing:
                    if stack and stack[-1] == closing[ch]:
                        stack.pop()

        repaired = text
        changed = False

        if in_string:
            repaired += '"'
            changed = True

        # Remove dangling trailing colon or comma before closing brackets
        cleaned_tail = re.sub(r",\s*$", "", repaired.rstrip())
        cleaned_tail = re.sub(r":\s*$", ": null", cleaned_tail)
        if cleaned_tail != repaired:
            repaired = cleaned_tail
            changed = True

        while stack:
            opener = stack.pop()
            repaired += pairs[opener]
            changed = True

        return repaired, changed

    def _repair_syntax(self, candidate: str, repairs_applied: List[str]) -> str:
        text = candidate.strip()

        # Normalize smart quotes
        smart_mapped = (
            text.replace("“", '"')
            .replace("”", '"')
            .replace("‘", "'")
            .replace("’", "'")
        )
        if smart_mapped != text:
            text = smart_mapped
            repairs_applied.append("normalized_smart_quotes")

        # Quote unquoted object keys: { name: "abc" } -> { "name": "abc" }
        quoted_keys = re.sub(
            r'(?P<prefix>[{\[,]\s*)(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:',
            r'\g<prefix>"\g<key>":',
            text,
        )
        if quoted_keys != text:
            text = quoted_keys
            repairs_applied.append("quoted_unquoted_keys")

        # Convert single-quoted strings to double-quoted strings safely
        single_to_double = re.sub(
            r"(?<![A-Za-z0-9_])'([^'\\]*(?:\\.[^'\\]*)*)'(?![A-Za-z0-9_])",
            lambda m: json.dumps(m.group(1)),
            text,
        )
        if single_to_double != text:
            text = single_to_double
            repairs_applied.append("converted_single_quotes")

        # Normalize Python/JS literals to valid JSON literals
        literal_fixed = re.sub(r"\bTrue\b", "true", text)
        literal_fixed = re.sub(r"\bFalse\b", "false", literal_fixed)
        literal_fixed = re.sub(r"\b(None|undefined|NaN)\b", "null", literal_fixed)
        if literal_fixed != text:
            text = literal_fixed
            repairs_applied.append("normalized_literals")

        # Remove trailing commas before } or ]
        no_trailing_commas = re.sub(r",\s*([}\]])", r"\1", text)
        if no_trailing_commas != text:
            text = no_trailing_commas
            repairs_applied.append("removed_trailing_commas")

        # Balance unclosed strings/brackets
        balanced, bracket_changed = self._balance_brackets(text)
        if bracket_changed:
            text = balanced
            repairs_applied.append("balanced_brackets_or_quotes")

        return text

    def _coerce_value(self, value: Any, expected_type: str) -> Tuple[Any, bool]:
        t = expected_type.strip().lower()
        try:
            if t in ("int", "integer"):
                if isinstance(value, bool):
                    return int(value), True
                if isinstance(value, int):
                    return value, False
                return int(float(str(value).strip())), True

            if t in ("float", "number"):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value), False
                return float(str(value).strip()), True

            if t in ("bool", "boolean"):
                if isinstance(value, bool):
                    return value, False
                s = str(value).strip().lower()
                if s in ("true", "1", "yes", "y"):
                    return True, True
                if s in ("false", "0", "no", "n"):
                    return False, True

            if t in ("str", "string"):
                if isinstance(value, str):
                    return value, False
                return str(value), True

            if t in ("list", "array"):
                if isinstance(value, list):
                    return value, False
                if isinstance(value, str):
                    items = [x.strip() for x in value.split(",") if x.strip()]
                    return items, True
        except (ValueError, TypeError):
            pass

        return value, False

    def _apply_schema(
        self, data: Any, schema: Dict[str, str], repairs_applied: List[str]
    ) -> Tuple[Any, List[str]]:
        if not isinstance(data, dict):
            return data, ["schema_skipped_non_dict_payload"]

        warnings: List[str] = []
        healed_obj = dict(data)

        for field_name, expected_type in schema.items():
            is_optional = expected_type.endswith("?")
            clean_type = expected_type[:-1] if is_optional else expected_type

            if field_name not in healed_obj:
                if not is_optional:
                    healed_obj[field_name] = None
                    warnings.append(f"missing_required_field:{field_name}")
                continue

            coerced_val, changed = self._coerce_value(healed_obj[field_name], clean_type)
            if changed:
                healed_obj[field_name] = coerced_val
                repairs_applied.append(f"coerced_field:{field_name}->{clean_type}")

        return healed_obj, warnings

    def heal(
        self,
        raw_output: str,
        expected_schema: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        start_time = time.perf_counter()
        repairs_applied: List[str] = []
        warnings: List[str] = []

        if not raw_output or not raw_output.strip():
            return {
                "is_valid_json": False,
                "was_healed": False,
                "data": None,
                "repairs_applied": [],
                "warnings": ["empty_input"],
                "latency_ms": round((time.perf_counter() - start_time) * 1000, 3),
            }

        # Fast path: already valid JSON
        try:
            parsed = json.loads(raw_output.strip())
            if expected_schema:
                parsed, warnings = self._apply_schema(parsed, expected_schema, repairs_applied)
            return {
                "is_valid_json": True,
                "was_healed": len(repairs_applied) > 0,
                "data": parsed,
                "repairs_applied": repairs_applied,
                "warnings": warnings,
                "latency_ms": round((time.perf_counter() - start_time) * 1000, 3),
            }
        except json.JSONDecodeError:
            pass

        # Repair pipeline
        working, fence_stripped = self._strip_markdown_fences(raw_output)
        if fence_stripped:
            repairs_applied.append("stripped_markdown_fences")

        candidate, extracted_sub = self._extract_json_candidate(working)
        if extracted_sub:
            repairs_applied.append("extracted_json_payload")

        parsed_result = None
        valid_json = False

        current_text = candidate
        for _ in range(self.max_repair_passes):
            current_text = self._repair_syntax(current_text, repairs_applied)
            try:
                parsed_result = json.loads(current_text)
                valid_json = True
                break
            except json.JSONDecodeError as exc:
                warnings.append(f"parse_attempt_warning:{exc.msg}")

        if valid_json and expected_schema:
            parsed_result, schema_warnings = self._apply_schema(
                parsed_result, expected_schema, repairs_applied
            )
            warnings.extend(schema_warnings)

        # Deduplicate while preserving order
        repairs_applied = list(dict.fromkeys(repairs_applied))
        warnings = list(dict.fromkeys(warnings))

        return {
            "is_valid_json": valid_json,
            "was_healed": valid_json and len(repairs_applied) > 0,
            "data": parsed_result,
            "repairs_applied": repairs_applied,
            "warnings": warnings,
            "latency_ms": round((time.perf_counter() - start_time) * 1000, 3),
        }
