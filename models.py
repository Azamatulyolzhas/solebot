from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str = ""
    session_id: str = "web_anon"


class ChatResponse(BaseModel):
    reply: str
