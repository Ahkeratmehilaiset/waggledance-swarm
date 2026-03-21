"""Use case: execute one night-mode learning cycle."""

from waggledance.application.services.learning_service import LearningService


async def run_night_mode_tick(learning_service: LearningService) -> dict:
    """Run one learning cycle and return tick statistics."""
    return await learning_service.run_night_tick()
