"""Entry point for piradio-ha daemon."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

from .config import LOG_FORMAT, load_config
from .daemon import PiRadioHADaemon


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format=LOG_FORMAT,
    )
    config = load_config()
    daemon = PiRadioHADaemon(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig: int, frame: Any) -> None:
        logging.getLogger("piradio-ha").info("Received signal %d, shutting down…", sig)
        daemon.stop()
        loop.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        loop.run_until_complete(daemon.run())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        daemon.stop()
        loop.close()
    logging.getLogger("piradio-ha").info("piradio-ha stopped.")


if __name__ == "__main__":
    main()
