import logging, os

_LEVEL = os.environ.get("AUTOHEAL_LOG_LEVEL", "INFO").upper()

def get_logger(name: str):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(_LEVEL)
    return logger
