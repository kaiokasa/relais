"""The LLM used by every agent of the team: Gemini, through its API key.

The key is read from the GEMINI_API_KEY (or GOOGLE_API_KEY) environment variable, or from .streamlit/secrets.toml
(ignored by git). It is never written in the code, in the logs or in the traces.

The model is chosen explicitly: the variability experiment compares models, so the name used must be known and
stable. Default: gemini-3.5-flash-lite (cheapest and fastest), or the GEMINI_MODEL environment variable.

Check the connection (prints the model and a short answer, never the key):
    python -m relais.llm
"""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"
DEFAULT_MODEL = "gemini-3.5-flash-lite"


class MissingApiKey(RuntimeError):
    pass


def gemini_api_key():
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    if SECRETS_FILE.exists():
        try:
            secrets = tomllib.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return None
        return secrets.get("GEMINI_API_KEY") or secrets.get("GOOGLE_API_KEY")
    return None


def model_name(model=None):
    """The model asked, else GEMINI_MODEL, else the default one."""
    return model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def no_thinking(model):
    """Settings that turn the 'thinking' step off. The models do not accept the same one: 'lite' models reject a
    budget of 0, gemini-3.8-flash rejects the 'minimal' level."""
    return {"thinking_level": "minimal"} if "lite" in model else {"thinking_budget": 0}


def chat_model(model=None, temperature=0.0):
    """A LangChain chat model for Gemini. Raises MissingApiKey if no key is configured."""
    key = gemini_api_key()
    if not key:
        raise MissingApiKey("No Gemini key: set GEMINI_API_KEY or fill .streamlit/secrets.toml")
    from langchain_google_genai import ChatGoogleGenerativeAI

    name = model_name(model)
    return ChatGoogleGenerativeAI(model=name, google_api_key=key, temperature=temperature, timeout=60,
                                  max_retries=2, **no_thinking(name))


def text_of(message):
    """The text of a reply. Gemini can return the content as a list of parts: .text joins them."""
    return message.text if hasattr(message, "text") else str(message.content)


def token_usage(message):
    """(input tokens, output tokens) of a reply, (0, 0) when the provider does not report them."""
    usage = getattr(message, "usage_metadata", None) or {}
    return usage.get("input_tokens", 0), usage.get("output_tokens", 0)


if __name__ == "__main__":
    reply = chat_model().invoke("Réponds seulement : OK")
    tokens_in, tokens_out = token_usage(reply)
    print(f"{model_name()} : {text_of(reply).strip()!r} ({tokens_in} tokens en entrée, {tokens_out} en sortie)")
