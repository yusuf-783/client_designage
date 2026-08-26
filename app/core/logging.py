import logging
import os
import sys
from pathlib import Path
from typing import Optional

try:
    from app.core.config import client_config
except ImportError:
    from client.app.core.config import client_config


def setup_client_logging() -> logging.Logger:
    """Setup client logger for terminal stdout and optional file logs."""
    log_level_name = client_config.logging.level.upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_format = (
        "[%(asctime)s] [%(levelname)s] [client:%(name)s] - %(message)s"
    )
    formatter = logging.Formatter(log_format)

    handlers = [logging.StreamHandler(sys.stdout)]

    if client_config.logging.log_file:
        try:
            log_path = Path(client_config.logging.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except Exception:
            pass

    # Root config for client
    root_logger = logging.getLogger("signage-client")
    root_logger.setLevel(log_level)
    root_logger.handlers = []
    for h in handlers:
        h.setFormatter(formatter)
        root_logger.addHandler(h)

    return root_logger


logger = setup_client_logging()
