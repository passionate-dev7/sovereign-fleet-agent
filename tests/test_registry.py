"""Tests for the lightweight agent registry."""

from __future__ import annotations

import pytest

from registry.agent_registry import (
    AgentRegistry,
    DuplicateRegistrationError,
    SubAgentRegistration,
    UnknownAgentError,
    bootstrap_default_registry,
)


def test_register_and_get():
    reg = AgentRegistry()
    reg.register(
        SubAgentRegistration(
            agent_id="eu-summarizer",
            region="EU",
            capability="summarize",
            version="1.0.0",
            service_account="sa@x.iam.gserviceaccount.com",
        )
    )
    got = reg.get("eu-summarizer")
    assert got.region == "EU"


def test_duplicate_registration_rejected():
    reg = AgentRegistry()
    r = SubAgentRegistration(
        agent_id="a", region="US", capability="support",
        version="1.0.0", service_account="sa",
    )
    reg.register(r)
    with pytest.raises(DuplicateRegistrationError):
        reg.register(r)


def test_unknown_agent_raises():
    reg = AgentRegistry()
    with pytest.raises(UnknownAgentError):
        reg.get("nonexistent")


def test_latest_resolves_highest_version():
    reg = AgentRegistry()
    reg.register(SubAgentRegistration("a", "US", "support", "1.0.0", "sa"))
    reg.register(SubAgentRegistration("a", "US", "support", "1.2.0", "sa"))
    reg.register(SubAgentRegistration("a", "US", "support", "1.1.0", "sa"))
    assert reg.latest("a").version == "1.2.0"


def test_discover_by_region_and_capability():
    reg = bootstrap_default_registry()
    eu_agents = reg.discover(region="EU")
    assert [a.agent_id for a in eu_agents] == ["eu-summarizer"]

    summarizers = reg.discover(capability="summarize")
    assert set(a.agent_id for a in summarizers) == {"eu-summarizer", "us-summarizer"}


def test_bootstrap_registry_has_genuinely_separate_agents():
    reg = bootstrap_default_registry()
    ids = reg.all_agent_ids()
    assert set(ids) == {"eu-summarizer", "us-summarizer", "us-support"}
    # each has its own service account -- no shared credential across agents
    accounts = {reg.latest(i).service_account for i in ids}
    assert len(accounts) == 3
