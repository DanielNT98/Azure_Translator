"""
Audio device discovery and management.

This module handles automatic discovery and configuration of audio input/output
devices using sounddevice library.
"""

import logging
from dataclasses import dataclass
import sounddevice as sd


logger = logging.getLogger("translator_bridge")


@dataclass
class AudioDevices:
    """
    Container for audio device indices.
    
    Dataclass that holds the indices of all audio devices used by the 
    translation system. Each index corresponds to a sounddevice/PyAudio 
    device number.
    
    Attributes:
        mic_idx (int or None): Microphone input device index
        a_play_idx (int or None): Output device A (playback) index for TTS
        b_cap_idx (int or None): Input device B (capture) index for loopback
        hp_play_idx (int or None): Headphones output device index for monitoring
        
    Example:
        >>> devices = AudioDevices(mic_idx=1, a_play_idx=5, b_cap_idx=6, hp_play_idx=2)
        >>> print(f"Microphone: {devices.mic_idx}")
        Microphone: 1
    """
    mic_idx: int | None
    a_play_idx: int | None
    b_cap_idx: int | None
    hp_play_idx: int | None


def calculate_match_score(device_name, keyword):
    """
    Calculate matching score between device name and search keyword.
    
    Uses fuzzy matching with different weights for exact, prefix, and substring matches.
    
    Args:
        device_name (str): Audio device name to match
        keyword (str): Search keyword
        
    Returns:
        int: Match score (3=exact, 2=prefix, 1=substring, 0=no match)
    """
    name_lower = device_name.lower()
    keyword_lower = keyword.lower()
    
    if name_lower == keyword_lower:
        return 3  # Exact match
    if name_lower.startswith(keyword_lower):
        return 2  # Prefix match
    if keyword_lower in name_lower:
        return 1  # Substring match
    return 0  # No match


def find_device_index(keyword, is_output):
    """
    Find audio device index by keyword search.
    
    Searches through available audio devices and returns the best match
    based on keyword and device type (input/output).
    
    Args:
        keyword (str): Search keyword for device name
        is_output (bool): True for output devices, False for input devices
        
    Returns:
        tuple: (device_index, device_name) or (None, None) if not found
    """
    if not keyword:
        return None, None
    
    try:
        candidates = []
        for idx, device in enumerate(sd.query_devices()):
            # Check if device has required channels
            if is_output:
                has_channels = device.get("max_output_channels", 0) > 0
            else:
                has_channels = device.get("max_input_channels", 0) > 0
            
            if not has_channels:
                continue
            
            device_name = device.get("name", "")
            score = calculate_match_score(device_name, keyword)
            
            if score > 0:
                candidates.append((score, idx, device_name))
        
        if not candidates:
            return None, None
        
        # Sort by score (descending) and name (ascending)
        candidates.sort(key=lambda x: (-x[0], x[2]))
        _, idx, name = candidates[0]
        return idx, name
        
    except Exception as e:
        logger.error(f"Error searching for device: {e}")
        return None, None


def find_output_device(keyword):
    """
    Find output (playback) device by keyword.
    
    Args:
        keyword (str): Search keyword for device name
        
    Returns:
        tuple: (device_index, device_name) or (None, None)
    """
    return find_device_index(keyword, is_output=True)


def find_input_device(keyword):
    """
    Find input (capture) device by keyword.
    
    Args:
        keyword (str): Search keyword for device name
        
    Returns:
        tuple: (device_index, device_name) or (None, None)
    """
    return find_device_index(keyword, is_output=False)


def resolve_audio_devices(cable_a_playback, cable_b_capture, headphones, mic_name):
    """
    Resolve all audio device indices with automatic fallbacks.
    
    Searches for specified devices and applies intelligent fallbacks:
    - Uses system default devices if specified devices not found
    - Searches for first available physical device as last resort
    - Logs all resolution steps for debugging
    
    Args:
        cable_a_playback (str): Virtual cable A output device name
        cable_b_capture (str): Virtual cable B input device name
        headphones (str): Headphones output device name (optional)
        mic_name (str): Microphone input device name
        
    Returns:
        AudioDevices: Resolved device indices container
    """
    try:
        # Search for specified devices
        a_idx, a_name = find_output_device(cable_a_playback)
        b_idx, b_name = find_input_device(cable_b_capture)
        hp_idx, hp_name = find_output_device(headphones) if headphones else (None, None)
        mic_idx, mic_name_found = find_input_device(mic_name) if mic_name else (None, None)
        
        # Log microphone search result
        if mic_idx:
            logger.info(f"[UP] MIC found: '{mic_name_found}' (idx={mic_idx})")
        else:
            logger.warning(f"[UP] MIC not found for keyword: '{mic_name}'")
        
        # Get system default devices
        default_in = default_out = None
        try:
            defaults = sd.default.device
            if isinstance(defaults, (list, tuple)) and len(defaults) == 2:
                default_in, default_out = defaults
                logger.info(f"System defaults: input={default_in}, output={default_out}")
        except Exception as e:
            logger.warning(f"Could not get default devices: {e}")
        
        # Apply fallbacks for microphone
        if mic_idx is None and default_in is not None:
            mic_idx = default_in
            try:
                mic_name_found = sd.query_devices(default_in).get('name', 'Unknown')
                logger.info(f"[UP] MIC using default input: '{mic_name_found}' (idx={mic_idx})")
            except:
                pass
        
        # Search for first available physical input device
        if mic_idx is None:
            logger.warning("[UP] Searching for first available physical input device...")
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    device_name = dev.get("name", "")
                    # Skip virtual cables
                    if "CABLE" not in device_name.upper():
                        mic_idx = i
                        logger.info(f"[UP] MIC auto-selected: '{device_name}' (idx={i})")
                        break
        
        # Last resort: use any input device
        if mic_idx is None:
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    mic_idx = i
                    device_name = dev.get("name", "")
                    logger.warning(f"[UP] MIC last resort: '{device_name}' (idx={i})")
                    break
        
        # Apply default fallbacks for other devices
        if a_idx is None and default_out is not None:
            a_idx = default_out
        if b_idx is None and default_in is not None:
            b_idx = default_in
        
        # Log final resolution status
        if mic_idx is not None:
            logger.info(f"SUCCESS [UP] MIC: idx={mic_idx}")
        else:
            logger.error(f"FAILED [UP] MIC: NOT RESOLVED")
        
        if a_idx is not None:
            logger.info(f"SUCCESS [UP] Output: idx={a_idx}")
        else:
            logger.error(f"FAILED [UP] Output: NOT RESOLVED")
        
        if b_idx is not None:
            logger.info(f"SUCCESS [DOWN] Input: idx={b_idx}")
        else:
            logger.error(f"FAILED [DOWN] Input: NOT RESOLVED")
        
        if hp_idx is not None:
            logger.info(f"SUCCESS [DOWN] Output: idx={hp_idx}")
        else:
            logger.warning(f"OPTIONAL [DOWN] Output: NOT RESOLVED")
        
        return AudioDevices(
            mic_idx=mic_idx,
            a_play_idx=a_idx,
            b_cap_idx=b_idx,
            hp_play_idx=hp_idx
        )
        
    except Exception as e:
        logger.error(f"Error resolving devices: {e}")
        return AudioDevices(None, None, None, None)
