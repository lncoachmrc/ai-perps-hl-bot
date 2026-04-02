from __future__ import annotations

import logging
import signal
import sys

from app.logging import configure_logging
from app.services.health import HealthServer
from app.settings import settings
from app.strategy.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging(settings.log_level)
    orchestrator = Orchestrator(settings)
    health = HealthServer("0.0.0.0", settings.port, orchestrator.status)
    health.start()

    def _shutdown(signum, frame):  # noqa: ANN001
        logger.warning("Shutdown signal received", extra={"signal": signum})
        health.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if not settings.start_on_boot:
        logger.info("START_ON_BOOT disabled. Health server only.")
        signal.pause()
        return

    orchestrator.run_forever()
