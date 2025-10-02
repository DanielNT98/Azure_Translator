"""Configuration and logging utilities for the bidirectional translator.

This module loads environment variables and provides a RotatingFile-based
logger configured for the application.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from dotenv import load_dotenv

@dataclass
class Config:
    """Strongly-typed container for runtime configuration.

    Attributes:
        SPEECH_KEY: Azure Speech service API key.
        SPEECH_REGION: Azure region for the Speech service.
        SRC_LANG: Upstream recognition language (you speak).
        TGT_LANG: Upstream target translation language (to remote party).
        VOICE_NAME: TTS voice name for upstream synthesis.
        DOWN_SRC_LANG: Downstream recognition language (remote party speaks).
        DOWN_TGT_LANG: Downstream target translation language (to you).
        DOWN_VOICE_NAME: TTS voice name for downstream synthesis.
        CABLE_A_PLAYBACK: Substring for output device that feeds your meeting mic.
        CABLE_B_CAPTURE: Substring for input device capturing meeting audio.
        MY_HEADPHONES: Substring for your headphones output (optional).
        LOG_PATH: Path to the rotating log file.
    """
    SPEECH_KEY: str
    SPEECH_REGION: str

    SRC_LANG: str
    TGT_LANG: str
    VOICE_NAME: str

    DOWN_SRC_LANG: str
    DOWN_TGT_LANG: str
    DOWN_VOICE_NAME: str

    CABLE_A_PLAYBACK: str
    CABLE_B_CAPTURE: str
    MY_HEADPHONES: str

    LOG_PATH: str


def load_config() -> "Config":
    """Load configuration from environment variables and .env file.

    Returns:
        Config: The loaded configuration object.

    Raises:
        RuntimeError: If mandatory Azure Speech variables are missing.
    """
    load_dotenv()
    speech_key = os.getenv("SPEECH__SERVICE__KEY")
    speech_region = os.getenv("SPEECH__SERVICE__REGION", "westeurope")
    if not speech_key or not speech_region:
        raise RuntimeError("Configure SPEECH__SERVICE__KEY and SPEECH__SERVICE__REGION in .env")

    return Config(
        SPEECH_KEY=speech_key,
        SPEECH_REGION=speech_region,
        SRC_LANG=os.getenv("SRC_LANG", "it-IT"),
        TGT_LANG=os.getenv("TGT_LANG", "en"),
        VOICE_NAME=os.getenv("VOICE_NAME", "en-US-JennyNeural"),
        DOWN_SRC_LANG=os.getenv("DOWN_SRC_LANG", "en-US"),
        DOWN_TGT_LANG=os.getenv("DOWN_TGT_LANG", "it"),
        DOWN_VOICE_NAME=os.getenv("DOWN_VOICE_NAME", "it-IT-GiuseppeNeural"),
        CABLE_A_PLAYBACK=os.getenv("CABLE_A_PLAYBACK", "CABLE-A Input"),
        CABLE_B_CAPTURE=os.getenv("CABLE_B_CAPTURE", "CABLE-B Output"),
        MY_HEADPHONES=os.getenv("MY_HEADPHONES", ""),
        LOG_PATH=os.getenv("TRANSLATOR_LOG_PATH", "azure_translator_bidir.log"),
    )


def setup_logger(log_path: str) -> logging.Logger:
    """Create and configure the application logger.

    The logger writes to both console and a rotating file.

    Args:
        log_path: Path to the log file.

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger("translator")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt_console = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s")
    fmt_file = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt_console)

    fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt_file)

    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.info("=== Translator Bidir startup ===")
    logger.info(f"Log file: {os.path.abspath(log_path)}")
    return logger
