from typing import Optional, Tuple
import sounddevice as sd


def _find_device_index(keyword: str, want_output: bool) -> Tuple[Optional[int], Optional[str]]:
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
    return _find_device_index(keyword, want_output=True)


def find_input_device_index(keyword: str):
    return _find_device_index(keyword, want_output=False)


def resolve_devices(cfg, logger):
    a_play_idx, a_play_name = find_output_device_index(cfg.CABLE_A_PLAYBACK)
    b_cap_idx, b_cap_name = find_input_device_index(cfg.CABLE_B_CAPTURE)
    hp_play_idx, hp_play_name = (find_output_device_index(cfg.MY_HEADPHONES) if cfg.MY_HEADPHONES else (None, None))

    if a_play_idx is None:
        logger.warning(f"[UP] Non trovo device di uscita per CABLE A playback '{cfg.CABLE_A_PLAYBACK}'. Userò default (non ideale).")
    else:
        logger.info(f"[UP] TTS (tu->loro) verso: {a_play_name}")

    if b_cap_idx is None:
        logger.warning(f"[DOWN] Non trovo device di ingresso per CABLE B capture '{cfg.CABLE_B_CAPTURE}'.")
        logger.warning("Assicurati in Teams: Altoparlante = 'CABLE-B Input' e che esista 'CABLE-B Output' tra i microfoni.")
    else:
        logger.info(f"[DOWN] Catturo (loro) da: {b_cap_name}")

    if hp_play_idx is None and cfg.MY_HEADPHONES:
        logger.warning(f"[DOWN] Non trovo le tue cuffie '{cfg.MY_HEADPHONES}'. Userò output di default.")
    elif hp_play_idx is not None:
        logger.info(f"[DOWN] Riproduco traduzione (loro->tu) su: {hp_play_name}")

    return {
        "a_play_idx": a_play_idx,
        "b_cap_idx": b_cap_idx,
        "hp_play_idx": hp_play_idx,
    }
