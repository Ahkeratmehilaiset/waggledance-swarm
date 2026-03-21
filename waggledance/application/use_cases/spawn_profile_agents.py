"""Use case: spawn agents for a profile."""

from pathlib import Path

from waggledance.application.services.bootstrap_service import BootstrapService
from waggledance.core.domain.agent import AgentDefinition


async def spawn_profile_agents(
    profile: str,
    bootstrap_service: BootstrapService,
    agents_dir: Path | None = None,
) -> list[AgentDefinition]:
    """Load and activate agents for the given profile."""
    directory = agents_dir or Path("agents")
    return await bootstrap_service.load_agents(directory)
