"""
Application A — Simple Chatbot with System Prompt.
FastAPI server that wraps multiple LLM providers with a shared system prompt.
Supports: OpenAI, Anthropic, Google Gemini, Ollama (local).
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

from config import SYSTEM_PROMPT, MODELS, TEMPERATURE, MAX_TOKENS

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Prompt Injection Lab — App A (Chatbot)")


# ==============================================================================
# Request / Response schemas
# ==============================================================================

class ChatRequest(BaseModel):
    message: str
    model: str = "gpt-4o-mini"  # default to cheaper model for testing
    conversation_history: list[dict] = []  # for multi-turn attacks


class ChatResponse(BaseModel):
    response: str
    model: str
    timestamp: str


# ==============================================================================
# LLM Provider Clients
# ==============================================================================

def call_openai(message: str, model_id: str, history: list[dict]) -> str:
    """Call OpenAI API (GPT-4o, GPT-4o-mini)."""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    response = client.chat.completions.create(
        model=model_id,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
    return response.choices[0].message.content


def call_anthropic(message: str, model_id: str, history: list[dict]) -> str:
    """Call Anthropic API (Claude Sonnet)."""
    from anthropic import Anthropic

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    messages = []
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    response = client.messages.create(
        model=model_id,
        system=SYSTEM_PROMPT,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
    return response.content[0].text


def call_google(message: str, model_id: str, history: list[dict]) -> str:
    """Call Google Gemini API."""
    import google.generativeai as genai

    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

    model = genai.GenerativeModel(
        model_name=model_id,
        system_instruction=SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            temperature=TEMPERATURE,
            max_output_tokens=MAX_TOKENS,
        ),
    )

    # Build conversation history
    chat_history = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        chat_history.append({"role": role, "parts": [msg["content"]]})

    chat = model.start_chat(history=chat_history)
    response = chat.send_message(message)
    return response.text


# Provider dispatcher
PROVIDERS = {
    "openai": call_openai,
    "anthropic": call_anthropic,
    "google": call_google,
}


# ==============================================================================
# API Endpoints
# ==============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Send a message to the chatbot and get a response."""

    if request.model not in MODELS:
        available = ", ".join(MODELS.keys())
        raise HTTPException(400, f"Unknown model '{request.model}'. Available: {available}")

    model_config = MODELS[request.model]
    provider = model_config["provider"]
    model_id = model_config["model_id"]

    if provider not in PROVIDERS:
        raise HTTPException(500, f"Provider '{provider}' not implemented")

    try:
        response_text = PROVIDERS[provider](
            message=request.message,
            model_id=model_id,
            history=request.conversation_history,
        )
    except Exception as e:
        logger.error(f"Error calling {provider}/{model_id}: {e}")
        raise HTTPException(500, f"Model error: {str(e)}")

    return ChatResponse(
        response=response_text,
        model=request.model,
        timestamp=datetime.now().isoformat(),
    )


@app.get("/models")
async def list_models():
    """List available models."""
    return {
        name: {
            "display_name": cfg["display_name"],
            "provider": cfg["provider"],
        }
        for name, cfg in MODELS.items()
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


# ==============================================================================
# Direct call function (for runner.py to bypass HTTP)
# ==============================================================================

def query_model(message: str, model: str, history: list[dict] | None = None) -> str:
    """
    Directly call a model without going through FastAPI.
    Used by runner.py for faster batch execution.
    """
    if history is None:
        history = []

    model_config = MODELS[model]
    provider = model_config["provider"]
    model_id = model_config["model_id"]

    return PROVIDERS[provider](
        message=message,
        model_id=model_id,
        history=history,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
