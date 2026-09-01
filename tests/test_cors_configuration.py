"""Whether the CORS middleware is installed at all.

Empty CORS_ALLOWED_ORIGINS means no middleware rather than a middleware
allowing nothing — the two look identical until someone sets `allow_origins`
to a default and every site can call the gateway. This is also the only
middleware here whose job is deciding who may call the API from a browser, so
"never exercised" was the wrong state for it to be in.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.main as main_module


def _cors_middlewares(application: FastAPI) -> list:
    return [m for m in application.user_middleware if m.cls is CORSMiddleware]


def test_no_origins_configured_installs_no_middleware(monkeypatch):
    monkeypatch.setattr(main_module.settings, "cors_allowed_origins", "")
    fresh = FastAPI()

    assert main_module._configure_cors(fresh) is False
    assert _cors_middlewares(fresh) == []


def test_configured_origins_are_installed_verbatim(monkeypatch):
    monkeypatch.setattr(
        main_module.settings, "cors_allowed_origins", "https://a.example, https://b.example"
    )
    fresh = FastAPI()

    assert main_module._configure_cors(fresh) is True

    installed = _cors_middlewares(fresh)
    assert len(installed) == 1
    # Exactly the two given — not a wildcard, and whitespace stripped.
    assert installed[0].kwargs["allow_origins"] == ["https://a.example", "https://b.example"]
