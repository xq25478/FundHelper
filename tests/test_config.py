from __future__ import annotations

from fund_helper.config import load_config


def test_ai_env_overrides(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        """
ai:
  enabled: false
  protocol: anthropic
  base_url: https://local.example/v1
  api_key: EMPTY
  model: local-model
  timeout: 30
  max_tokens: 4096
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("FH_AI_ENABLED", "true")
    monkeypatch.setenv("FH_AI_PROTOCOL", "openai_responses")
    monkeypatch.setenv("FH_AI_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("FH_AI_API_KEY", "secret")
    monkeypatch.setenv("FH_AI_MODEL", "remote-model")
    monkeypatch.setenv("FH_AI_TIMEOUT", "120")
    monkeypatch.setenv("FH_AI_MAX_TOKENS", "12000")

    cfg = load_config(settings)

    assert cfg.ai.enabled is True
    assert cfg.ai.protocol == "openai_responses"
    assert cfg.ai.base_url == "https://api.example/v1"
    assert cfg.ai.api_key == "secret"
    assert cfg.ai.model == "remote-model"
    assert cfg.ai.timeout == 120
    assert cfg.ai.max_tokens == 12000


def test_explicit_config_path_wins_over_config_yml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yml").write_text(
        """
ai:
  enabled: true
  protocol: anthropic
  base_url: https://wrong.example/v1
  api_key: EMPTY
  model: wrong-model
""",
        encoding="utf-8",
    )
    explicit = tmp_path / "custom.yaml"
    explicit.write_text(
        """
ai:
  enabled: true
  protocol: openai_chat
  base_url: https://right.example/v1
  api_key: EMPTY
  model: right-model
""",
        encoding="utf-8",
    )

    cfg = load_config(explicit)

    assert cfg.ai.protocol == "openai_chat"
    assert cfg.ai.base_url == "https://right.example/v1"
    assert cfg.ai.model == "right-model"
