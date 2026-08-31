"""agent/tools.py's credential gate must accept BOTH real auth modes.

Regression coverage for the architecture-inconsistency fix: `_generate()`
used to require GOOGLE_API_KEY/GEMINI_API_KEY unconditionally, which made
Vertex AI + ADC (the mode the real Cloud Run Job deploy actually uses, per
`infra/deploy_sovereign.sh`'s COMMON_ENV) impossible to exercise without
fabricating a throwaway API key. These tests never make a network call --
they exercise only `_require_credentials()`, the gate `_generate()` calls
before touching `google.genai` at all -- so they stay in the offline suite.
"""

from __future__ import annotations

import pytest

from agent.tools import MissingModelCredentials, _require_credentials

ALL_CRED_VARS = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "GOOGLE_CLOUD_PROJECT",
)


@pytest.fixture(autouse=True)
def clean_credential_env(monkeypatch):
    """Every test in this file starts from a blank credential slate,
    regardless of what the real shell/CI environment happens to have set."""
    for var in ALL_CRED_VARS:
        monkeypatch.delenv(var, raising=False)


def test_no_credentials_at_all_raises():
    with pytest.raises(MissingModelCredentials, match="no Gemini API key configured"):
        _require_credentials()


def test_api_key_mode_is_accepted(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-gate-check-only")
    _require_credentials()  # must not raise


def test_gemini_api_key_alias_is_accepted(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-gate-check-only")
    _require_credentials()  # must not raise


def test_vertex_mode_with_project_is_accepted_with_no_api_key(monkeypatch):
    """The exact case that was broken: Vertex AI + ADC, no API key set
    anywhere. This is `infra/deploy_sovereign.sh`'s real deploy shape."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _require_credentials()  # must not raise


def test_vertex_mode_without_project_raises(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    with pytest.raises(MissingModelCredentials, match="GOOGLE_CLOUD_PROJECT"):
        _require_credentials()


def test_vertex_mode_lowercase_true_is_accepted(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _require_credentials()  # must not raise


def test_vertex_mode_false_falls_back_to_api_key_requirement(monkeypatch):
    """GOOGLE_GENAI_USE_VERTEXAI=false (or any falsy value) must not be
    mistaken for Vertex mode -- google-genai treats it as "developer API",
    so the gate must still require an API key in that case."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    with pytest.raises(MissingModelCredentials, match="no Gemini API key configured"):
        _require_credentials()
