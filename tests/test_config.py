import pytest

from callcopilot.llm import ConfigError, create_client, load_config, load_judge_config
from callcopilot.llm.fake import FakeLLM


def test_missing_variables_named_in_error():
    with pytest.raises(ConfigError) as e:
        load_config(env={})
    msg = str(e.value)
    assert "AI_SERVER_URL" in msg and "AI_MODEL" in msg and ".env.example" in msg
    assert "AI_SERVER_API_KEY" not in msg  # the key is optional


def test_only_the_missing_one_is_reported():
    with pytest.raises(ConfigError) as e:
        load_config(env={"AI_SERVER_URL": "http://localhost:8000/v1"})
    assert "AI_MODEL" in str(e.value) and "AI_SERVER_URL" not in str(e.value)


def test_empty_api_key_is_allowed_and_blank_values_count_as_unset():
    cfg = load_config(env={"AI_SERVER_URL": "http://x", "AI_MODEL": "m", "AI_SERVER_API_KEY": "", "AI_REQUEST_TIMEOUT_SECONDS": ""})
    assert cfg.api_key == "" and cfg.timeout_s == 60.0 and cfg.provider == "openai"


def test_fake_needs_nothing_and_creates_fake_client():
    cfg = load_config(env={"AI_PROVIDER": "fake"})
    assert cfg.is_fake and isinstance(create_client(cfg), FakeLLM)


@pytest.mark.parametrize("env,needle", [
    ({"AI_PROVIDER": "banana"}, "AI_PROVIDER"),
    ({"AI_PROVIDER": "fake", "AI_REQUEST_TIMEOUT_SECONDS": "soon"}, "AI_REQUEST_TIMEOUT_SECONDS"),
    ({"AI_PROVIDER": "fake", "AI_TOOL_MODE": "magic"}, "AI_TOOL_MODE"),
])
def test_invalid_values_have_actionable_errors(env, needle):
    with pytest.raises(ConfigError) as e:
        load_config(env=env)
    assert needle in str(e.value)


def test_judge_falls_back_to_main_config_field_by_field():
    env = {"AI_SERVER_URL": "http://main", "AI_MODEL": "main-model", "AI_JUDGE_MODEL": "judge-model"}
    j = load_judge_config(env)
    assert j.url == "http://main" and j.model == "judge-model"


def test_judge_none_when_fake_or_unconfigured():
    assert load_judge_config({"AI_PROVIDER": "fake"}) is None
    assert load_judge_config({}) is None
    assert load_judge_config({"AI_PROVIDER": "fake", "AI_JUDGE_SERVER_URL": "http://j", "AI_JUDGE_MODEL": "m", "AI_JUDGE_PROVIDER": "openai"}).url == "http://j"
