"""Lightweight sub-agent registry.

Deliberately small: register, version, discover. Nothing more. This is
NOT a service mesh or a general-purpose catalog; it exists so the gateway
has one trustworthy place to read a sub-agent's DECLARED region and
capability from, instead of trusting whatever the agent claims at call
time (which is exactly the channel a prompt injection would try to abuse).

The declared region here is what policy/engine.py's `caller_region` comes
from. An agent cannot register as "EU" and then process US data by
asserting something different mid-conversation: the gateway looks up the
region from this registry, not from the agent's output.
"""

from __future__ import annotations

import os

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SubAgentRegistration:
    agent_id: str
    region: str  # declared residency capability, e.g. "EU" or "US"
    capability: str  # e.g. "summarize", "support", "billing"
    version: str
    service_account: str  # least-privilege identity for this sub-agent


class DuplicateRegistrationError(Exception):
    pass


class UnknownAgentError(Exception):
    pass


class AgentRegistry:
    """In-memory registry keyed by (agent_id, version).

    `latest()` resolves the highest version seen for an agent_id so the
    gateway can route to "the current EU summarizer" without hardcoding a
    version string.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], SubAgentRegistration] = {}

    def register(self, registration: SubAgentRegistration) -> None:
        key = (registration.agent_id, registration.version)
        if key in self._entries:
            raise DuplicateRegistrationError(
                f"{registration.agent_id}@{registration.version} already registered"
            )
        self._entries[key] = registration

    def get(self, agent_id: str, version: Optional[str] = None) -> SubAgentRegistration:
        if version is not None:
            key = (agent_id, version)
            if key not in self._entries:
                raise UnknownAgentError(f"{agent_id}@{version} not registered")
            return self._entries[key]
        return self.latest(agent_id)

    def latest(self, agent_id: str) -> SubAgentRegistration:
        candidates = [r for (aid, _v), r in self._entries.items() if aid == agent_id]
        if not candidates:
            raise UnknownAgentError(f"{agent_id} not registered")
        return max(candidates, key=lambda r: r.version)

    def discover(
        self,
        region: Optional[str] = None,
        capability: Optional[str] = None,
    ) -> list[SubAgentRegistration]:
        """Find registered sub-agents matching region and/or capability.

        Used by the orchestrator to pick a sub-agent whose declared region
        matches the data it needs to route, e.g. "give me the latest
        EU-region summarizer".
        """
        results = []
        seen_agent_ids: set[str] = set()
        for (agent_id, _v), reg in sorted(self._entries.items()):
            if agent_id in seen_agent_ids:
                continue
            latest = self.latest(agent_id)
            if region is not None and latest.region != region:
                continue
            if capability is not None and latest.capability != capability:
                continue
            results.append(latest)
            seen_agent_ids.add(agent_id)
        return results

    def all_agent_ids(self) -> list[str]:
        return sorted({aid for aid, _v in self._entries})


DEFAULT_REGISTRY = AgentRegistry()


def _service_account(agent_id: str) -> str:
    """The least-privilege service account for a sub-agent.

    Resolved from GOOGLE_CLOUD_PROJECT so a deployed fleet registers the
    account `infra/deploy.sh` actually creates
    (`sovereign-<agent_id>@<project>.iam.gserviceaccount.com`), rather
    than a hardcoded string that only looks like an identity.

    With no project configured (offline demo, test suite) the account is
    labelled `unset-project` instead of a plausible-looking fake, so a
    reader can tell at a glance that no GCP identity is bound. The field
    is descriptive metadata for the audit log; the enforcement input is
    `region`, which is always declared.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or "unset-project"
    return f"sovereign-{agent_id}@{project}.iam.gserviceaccount.com"


def bootstrap_default_registry() -> AgentRegistry:
    """The fleet used by the demo: one orchestrator, two region sub-agents.

    Genuinely separate agents (see agent/), each with its own service
    account and declared region -- not one agent role-playing as a fleet.
    """
    reg = AgentRegistry()
    reg.register(
        SubAgentRegistration(
            agent_id="eu-summarizer",
            region="EU",
            capability="summarize",
            version="1.0.0",
            service_account=_service_account("eu-summarizer"),
        )
    )
    reg.register(
        SubAgentRegistration(
            agent_id="us-summarizer",
            region="US",
            capability="summarize",
            version="1.0.0",
            service_account=_service_account("us-summarizer"),
        )
    )
    reg.register(
        SubAgentRegistration(
            agent_id="us-support",
            region="US",
            capability="support",
            version="1.0.0",
            service_account=_service_account("us-support"),
        )
    )
    return reg
