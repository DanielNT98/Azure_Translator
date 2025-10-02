import os
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from dotenv import load_dotenv
from pathlib import Path

@dataclass
class Config:
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
    # Ensure we load .env from the project root (folder containing main.py),
    # even if the script is started from a different CWD.
    project_root = Path(__file__).resolve().parents[1]
    env_path = project_root / ".env"
    load_dotenv(env_path)
    speech_key = os.getenv("SPEECH__SERVICE__KEY")
    speech_region = os.getenv("SPEECH__SERVICE__REGION", "westeurope")
    if not speech_key or not speech_region:
        raise RuntimeError(
            f"Configura SPEECH__SERVICE__KEY e SPEECH__SERVICE__REGION nel .env ({env_path})"
        )

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
    logger.info("=== Avvio Translator Bidir ===")
    logger.info(f"Log file: {os.path.abspath(log_path)}")
    return logger
