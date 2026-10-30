import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        # Uvicorn configures logging first; force our level/format to win.
        force=True,
    )
    # Do not log upstream URLs, payloads or headers at normal verbosity.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
