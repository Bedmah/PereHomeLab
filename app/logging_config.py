import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import PROJECT_ROOT


def _safe_file_handler(path: Path, formatter: logging.Formatter) -> RotatingFileHandler | None:
    try:
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding='utf-8')
    except OSError as exc:
        logging.getLogger('pere_home_lab').warning('Cannot write log file %s: %s', path, exc)
        return None
    handler.setFormatter(formatter)
    return handler


def configure_logging() -> logging.Logger:
    log_dir = PROJECT_ROOT / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger('pere_home_lab')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', '%Y-%m-%d %H:%M:%S')

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    app_handler = _safe_file_handler(log_dir / 'app.log', formatter)
    if app_handler:
        logger.addHandler(app_handler)

    access_logger = logging.getLogger('uvicorn.access')
    access_logger.handlers.clear()
    access_logger.setLevel(logging.INFO)

    access_handler = _safe_file_handler(log_dir / 'access.log', formatter)
    if access_handler:
        access_logger.addHandler(access_handler)
    else:
        access_logger.addHandler(console_handler)

    logger.info('Logging initialized. log_dir=%s', log_dir)
    return logger
