# Translator Bidir (Azure Speech)

Real-time, bidirectional speech translation using Azure Speech Services.

- You speak into your local microphone (e.g., Italian). The system translates and synthesizes English to a virtual microphone (CABLE-A) that you select in Teams/Meet/Zoom.
- Remote speech (e.g., English) is captured from a virtual speaker (CABLE-B), translated into your language, and played on your headphones.

## Requirements

- Python 3.9+
- Azure Speech Service key and region
- Audio routing tools installed (VB-CABLE recommended):
  - CABLE-A: used as the microphone sent to the meeting (upstream TTS goes here)
  - CABLE-B: used as the meeting speaker (we capture from here)

## Installation

1) Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2) Install dependencies:

```powershell
pip install -r requirements.txt
```

3) Configure environment variables: copy `.env.example` to `.env` and fill values.

```powershell
Copy-Item .env.example .env
```

## Run

```powershell
python main.py
```

Recommended settings in your meeting app:

- Microphone: CABLE-A (or equivalent — where upstream TTS is sent)
- Speaker: CABLE-B (or equivalent — the remote audio source we capture)

## Project structure

- `main.py`: entry point. Orchestration, setup, and lifecycle.
- `translator_bidir/`
  - `config.py`: .env loading, config, and logger
  - `devices.py`: audio device discovery and resolution
  - `azure_clients.py`: Azure recognizer/synthesizer factories
  - `audio.py`: WAV playback helpers
  - `workers.py`: TTS worker threads (queue -> synth -> play)
  - `capture.py`: capture thread feeding PushAudioInputStream from CABLE-B
  - `handlers.py`: Azure event handlers (recognized, canceled, session)

## Notes

- Downstream capture uses 16 kHz mono PCM 16-bit for Azure.
- The simple silence gate can be adjusted in `capture.py`.
- If devices are not found, the system defaults are used where possible and a warning is logged.
- Se i device non vengono trovati, il programma usa i default di sistema (dove possibile) e mostra un warning.


