from config.base import BaseConfig, load_base_config


def test_load_base_config_names_all_three_models_separately():
    config = load_base_config()

    assert isinstance(config, BaseConfig)
    assert config.main_agent_model
    assert config.history_agent_model
    assert config.insights_agent_model


def test_base_config_is_frozen():
    config = load_base_config()

    try:
        config.main_agent_model = "something-else"
        assert False, "BaseConfig should be immutable"
    except AttributeError:
        pass
