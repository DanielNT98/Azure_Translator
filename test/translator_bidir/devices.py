"""Audio device discovery and resolution utilities.

Provides helpers to find input/output devices by substring matching and
resolve the indices used by the application.
"""

from typing import Optional, Tuple
import sounddevice as sd


def _find_device_index(keyword: str, want_output: bool) -> Tuple[Optional[int], Optional[str]]:
    """Find a device index by substring matching against device name.

    Args:
        keyword: Case-insensitive substring to search in device name.
        want_output: True for output devices, False for input devices.

    Returns:
        A tuple (index, name) or (None, None) if not found.
    """
    if not keyword:
        return None, None
    keyword_l = keyword.lower()
    for idx, dev in enumerate(sd.query_devices()):
        if want_output and dev.get("max_output_channels", 0) > 0:
            name = dev.get("name", "")
            if keyword_l in name.lower():
                return idx, name
        if not want_output and dev.get("max_input_channels", 0) > 0:
            name = dev.get("name", "")
            if keyword_l in name.lower():
                return idx, name
    return None, None


def find_output_device_index(keyword: str):
    """Find an output device index by substring keyword."""
    return _find_device_index(keyword, want_output=True)


def find_input_device_index(keyword: str):
    """Find an input device index by substring keyword."""
    return _find_device_index(keyword, want_output=False)


def resolve_devices(cfg, logger):
    """Resolve all device indices used by the app and log status.

    Args:
        cfg: Configuration object containing device keywords.
        logger: Logger for reporting findings.

    Returns:
        dict: Mapping with keys 'a_play_idx', 'b_cap_idx', 'hp_play_idx'.
    """
    a_play_idx, a_play_name = find_output_device_index(cfg.CABLE_A_PLAYBACK)
    b_cap_idx, b_cap_name = find_input_device_index(cfg.CABLE_B_CAPTURE)
    hp_play_idx, hp_play_name = (find_output_device_index(cfg.MY_HEADPHONES) if cfg.MY_HEADPHONES else (None, None))

    if a_play_idx is None:
        logger.warning(f"[UP] Output device for CABLE-A playback '{cfg.CABLE_A_PLAYBACK}' not found. Falling back to default (not ideal).")
    else:
        logger.info(f"[UP] TTS (you->them) routed to: {a_play_name}")

    if b_cap_idx is None:
        logger.warning(f"[DOWN] Input device for CABLE-B capture '{cfg.CABLE_B_CAPTURE}' not found.")
        logger.warning("Ensure in Teams: Speaker = 'CABLE-B Input' and that 'CABLE-B Output' exists among microphones.")
    else:
        logger.info(f"[DOWN] Capturing (them) from: {b_cap_name}")

    if hp_play_idx is None and cfg.MY_HEADPHONES:
        logger.warning(f"[DOWN] Headphones '{cfg.MY_HEADPHONES}' not found. Using default output.")
    elif hp_play_idx is not None:
        logger.info(f"[DOWN] Playing translation (them->you) on: {hp_play_name}")

    return {
        "a_play_idx": a_play_idx,
        "b_cap_idx": b_cap_idx,
        "hp_play_idx": hp_play_idx,
    }
