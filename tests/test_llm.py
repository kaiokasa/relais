"""LLM configuration without any network call: the key and the chat model are faked."""
import pytest
from langchain_core.messages import AIMessage

from relais import llm


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "SECRETS_FILE", tmp_path / "secrets.toml")


def test_key_from_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert llm.gemini_api_key() == "env-key"


def test_key_from_streamlit_secrets():
    llm.SECRETS_FILE.write_text('GEMINI_API_KEY = "file-key"\n', encoding="utf-8")
    assert llm.gemini_api_key() == "file-key"


def test_no_key_and_broken_secrets_file():
    assert llm.gemini_api_key() is None
    llm.SECRETS_FILE.write_text("not = valid = toml", encoding="utf-8")
    assert llm.gemini_api_key() is None


def test_chat_model_without_key_fails_clearly():
    with pytest.raises(llm.MissingApiKey):
        llm.chat_model()


def test_model_choice(monkeypatch):
    assert llm.model_name() == llm.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_MODEL", "gemini-x-flash")
    assert llm.model_name() == "gemini-x-flash"
    assert llm.model_name("gemini-y-flash-lite") == "gemini-y-flash-lite"


def test_thinking_setting_depends_on_the_model():
    assert llm.no_thinking("gemini-3.5-flash-lite") == {"thinking_level": "minimal"}
    assert llm.no_thinking("gemini-3.8-flash") == {"thinking_budget": 0}


def test_chat_model_receives_key_model_and_temperature(monkeypatch):
    import langchain_google_genai

    captured = {}
    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    llm.chat_model("gemini-3.8-flash", temperature=0.7)
    assert captured["model"] == "gemini-3.8-flash" and captured["temperature"] == 0.7
    assert captured["google_api_key"] == "key" and captured["thinking_budget"] == 0


def test_text_of_joins_list_content():
    assert llm.text_of(AIMessage(content=[{"type": "text", "text": "Taux "}, "de 82 %"])) == "Taux de 82 %"
    assert llm.text_of(AIMessage(content="simple")) == "simple"


def test_token_usage():
    message = AIMessage(content="ok", usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15})
    assert llm.token_usage(message) == (12, 3)
    assert llm.token_usage(AIMessage(content="ok")) == (0, 0)
