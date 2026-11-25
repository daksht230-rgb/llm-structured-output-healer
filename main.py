from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Optional
from healer_engine import StructuredOutputHealer

app = FastAPI(
    title="LLM Structured Output Healer API",
    description="Deterministic JSON extraction, syntax repair, and type coercion layer for unreliable LLM structured responses.",
    version="1.0.0",
)

healer = StructuredOutputHealer(max_repair_passes=3)


class HealRequest(BaseModel):
    raw_output: str = Field(
        ...,
        min_length=1,
        example='```json\nSure! Here is the data:\n{score: \'95\', passed: True, tags: "rag, eval",}\n```',
    )
    expected_schema: Optional[Dict[str, str]] = Field(
        default=None,
        example={"score": "int", "passed": "bool", "tags": "list"},
    )


@app.post("/v1/output/heal", summary="Repair and Validate Malformed LLM Output")
def heal_structured_output(payload: HealRequest):
    try:
        result = healer.heal(
            raw_output=payload.raw_output,
            expected_schema=payload.expected_schema,
        )
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Output healing error: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
