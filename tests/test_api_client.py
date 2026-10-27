"""Provider selection must never send the ProxyAPI key to api.openai.com."""

from nexacrm.api_client import create_api_client
from nexacrm.config import load_config


def test_proxyapi_key_takes_priority_and_uses_proxy_endpoint(monkeypatch):
    monkeypatch.setenv("PROXY_API_KEY", "proxy-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("OPENAI_QUERY_MODEL", "gpt-6-luna")
    monkeypatch.setenv("OPENAI_ANSWER_MODEL", "gpt-6.1-sol")

    config = load_config()
    client = create_api_client(config, timeout=10)
    try:
        assert config.api_provider == "proxyapi"
        assert config.api_key_available
        assert config.embedding_model == "openai/text-embedding-3-small"
        assert config.query_model == "openai/gpt-6-luna"
        assert config.answer_model == "openai/gpt-6.1-sol"
        assert client.api_key == "proxy-test-key"
        assert str(client.base_url) == "https://api.proxyapi.ru/v1/"
    finally:
        client.close()


def test_openai_fallback_without_proxy_key(monkeypatch):
    monkeypatch.setenv("PROXY_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    config = load_config()
    client = create_api_client(config, timeout=10)
    try:
        assert config.api_provider == "openai"
        assert config.embedding_model == "text-embedding-3-small"
        assert client.api_key == "openai-test-key"
        assert str(client.base_url) == "https://api.openai.com/v1/"
    finally:
        client.close()
