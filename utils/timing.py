"""
Timing and logging utilities for performance analysis.

This module provides high-precision timestamp utilities for tracking
system latency and generating detailed timing logs.
"""

import time
import logging
from datetime import datetime
from pathlib import Path


def get_precise_timestamp():
    """
    Get current timestamp in milliseconds with high precision.
    
    Uses time.perf_counter() for monotonic, high-resolution timing.
    
    Returns:
        float: Current timestamp in milliseconds
    """
    return time.perf_counter() * 1000


def format_timestamp(timestamp_ms):
    """
    Format timestamp with millisecond precision for display.
    
    Args:
        timestamp_ms (float): Timestamp in milliseconds
        
    Returns:
        str: Formatted timestamp string (HH:MM:SS.mmm)
    """
    dt = datetime.now()
    return f"{dt.strftime('%H:%M:%S')}.{int(timestamp_ms % 1000):03d}"


def log_timing(logger, label, phase, ts_start=None, extra_info=""):
    """
    Log timing information with delay calculation.
    
    Logs timing events with precise delay measurement from start timestamp.
    If no start timestamp provided, logs absolute time instead.
    
    Args:
        logger (logging.Logger): Logger instance to use
        label (str): Label identifying the component (e.g., "[UP-TTS]")
        phase (str): Phase description (e.g., "TTS RECEIVED TEXT")
        ts_start (float, optional): Start timestamp in ms for delay calculation
        extra_info (str, optional): Additional information to log
        
    Returns:
        float: Current timestamp in milliseconds
    """
    ts_now = get_precise_timestamp()
    if ts_start:
        delay = ts_now - ts_start
        logger.info(f"TIMING [[{label}]] {phase} | Delay: {delay:.1f}ms | {extra_info}")
    else:
        logger.info(f"TIMING [[{label}]] {phase} | Time: {format_timestamp(ts_now)} | {extra_info}")
    return ts_now


def setup_logging(log_dir="logs", session_timestamp=None):
    """
    Configure logging with both file and console handlers.
    
    Creates a session-specific log file in the logs directory and sets up
    logging to both file and console with appropriate formatting.
    
    Args:
        log_dir (str, optional): Directory for log files. Defaults to "logs".
        session_timestamp (str, optional): Session timestamp for log filename.
            If None, generates new timestamp.
            
    Returns:
        tuple: (logger, log_file_path) - Configured logger and path to log file
    """
    if session_timestamp is None:
        session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    log_filename = f"azure_translator_session_{session_timestamp}.log"
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file_path = log_path / log_filename
    
    # Remove existing handlers
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_file_path, mode='w', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    logger = logging.getLogger("translator_bridge")
    logger.info(f"Session log file: {log_file_path}")
    
    return logger, str(log_file_path)
