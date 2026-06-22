"""Verify config.py raises in production when required secrets are missing,
and is silent in development."""
import importlib
import os

import dotenv
import pytest


REQUIRED_KEYS = ("JWT_SECRET", "ADMIN_PASSWORD_HASH", "ALLOWED_ORIGINS")


def _reload_config_with(env: dict[str, str | None]):
    """Reload config.py with the given env overrides. Pass None to clear a key."""
    original: dict[str, str | None] = {}
    for k, v in env.items():
        original[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # Suppress .env loading so the test's env-var overrides aren't shadowed by
    # whatever the developer has in their local .env file.
    original_load_dotenv = dotenv.load_dotenv
    dotenv.load_dotenv = lambda *a, **kw: False
    try:
        import config as _cfg
        return importlib.reload(_cfg)
    finally:
        dotenv.load_dotenv = original_load_dotenv
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def restore_dev_config():
    """After every test, reload config back to its conftest-defined state."""
    yield
    import config
    importlib.reload(config)


class TestProductionFailFast:

    def test_missing_jwt_secret_raises(self):
        with pytest.raises(RuntimeError) as exc:
            _reload_config_with({
                "ENVIRONMENT": "production",
                "JWT_SECRET": None,
                "ADMIN_PASSWORD_HASH": "$2b$12$abcdefghijklmnop",
                "ALLOWED_ORIGINS": "https://app.example.com",
            })
        assert "JWT_SECRET" in str(exc.value)

    def test_missing_admin_hash_raises(self):
        with pytest.raises(RuntimeError) as exc:
            _reload_config_with({
                "ENVIRONMENT": "production",
                "JWT_SECRET": "x" * 64,
                "ADMIN_PASSWORD_HASH": None,
                "ALLOWED_ORIGINS": "https://app.example.com",
            })
        assert "ADMIN_PASSWORD_HASH" in str(exc.value)

    def test_missing_allowed_origins_raises(self):
        with pytest.raises(RuntimeError) as exc:
            _reload_config_with({
                "ENVIRONMENT": "production",
                "JWT_SECRET": "x" * 64,
                "ADMIN_PASSWORD_HASH": "$2b$12$abcdefghijklmnop",
                "ALLOWED_ORIGINS": None,
            })
        assert "ALLOWED_ORIGINS" in str(exc.value)

    def test_lists_all_missing_in_one_message(self):
        with pytest.raises(RuntimeError) as exc:
            _reload_config_with({
                "ENVIRONMENT": "production",
                "JWT_SECRET": None,
                "ADMIN_PASSWORD_HASH": None,
                "ALLOWED_ORIGINS": None,
            })
        msg = str(exc.value)
        for key in REQUIRED_KEYS:
            assert key in msg, f"{key} not in error message"

    def test_all_secrets_set_no_raise(self):
        cfg = _reload_config_with({
            "ENVIRONMENT": "production",
            "JWT_SECRET": "x" * 64,
            "ADMIN_PASSWORD_HASH": "$2b$12$abcdefghijklmnop",
            "ALLOWED_ORIGINS": "https://app.example.com",
        })
        assert cfg.IS_PRODUCTION is True
        assert cfg.ALLOWED_ORIGINS == ["https://app.example.com"]

    def test_railway_environment_name_triggers_prod_mode(self):
        with pytest.raises(RuntimeError):
            _reload_config_with({
                "ENVIRONMENT": None,
                "RAILWAY_ENVIRONMENT_NAME": "production",
                "JWT_SECRET": None,
                "ADMIN_PASSWORD_HASH": "h",
                "ALLOWED_ORIGINS": "https://x",
            })


class TestDevelopmentTolerance:

    def test_dev_with_missing_secrets_does_not_raise(self):
        cfg = _reload_config_with({
            "ENVIRONMENT": "development",
            "JWT_SECRET": None,
            "ADMIN_PASSWORD_HASH": None,
            "ALLOWED_ORIGINS": None,
        })
        assert cfg.IS_PRODUCTION is False
        # JWT_SECRET should still be populated (random ephemeral) for dev session continuity.
        assert cfg.JWT_SECRET, "dev mode should auto-generate a random JWT_SECRET"

    def test_default_environment_is_development(self):
        cfg = _reload_config_with({
            "ENVIRONMENT": None,
            "RAILWAY_ENVIRONMENT_NAME": None,
        })
        assert cfg.ENVIRONMENT == "development"
        assert cfg.IS_PRODUCTION is False

    def test_allowed_origins_parses_comma_separated(self):
        cfg = _reload_config_with({
            "ENVIRONMENT": "development",
            "ALLOWED_ORIGINS": "https://a.com, https://b.com ,https://c.com",
        })
        assert cfg.ALLOWED_ORIGINS == [
            "https://a.com", "https://b.com", "https://c.com",
        ]
