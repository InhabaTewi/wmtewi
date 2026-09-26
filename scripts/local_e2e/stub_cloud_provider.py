import json

import uvicorn
from fastapi import FastAPI, Request


app = FastAPI()
chat_call_count = 0


@app.get("/v1/models")
async def models() -> dict:
    return {"data": [{"id": "stub-cloud-model"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    global chat_call_count
    payload = await request.json()
    chat_call_count += 1
    messages = payload.get("messages", [])
    user_text = next((message.get("content", "") for message in reversed(messages) if message.get("role") == "user"), "")
    response = {"speech": f"stub-cloud fallback: {user_text}"}
    return {"choices": [{"message": {"content": json.dumps(response, ensure_ascii=True)}}]}


@app.get("/__test/calls")
async def calls() -> dict:
    return {"chat_call_count": chat_call_count}


@app.post("/__test/reset")
async def reset() -> dict:
    global chat_call_count
    chat_call_count = 0
    return {"chat_call_count": chat_call_count}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18082)