# Translator Bidir (Azure Speech)

Sistema di traduzione bidirezionale in tempo reale basato su Azure Speech Services.

- Tu parli al microfono locale (es. Italiano) e il sistema sintetizza in inglese verso il tuo microfono virtuale (CABLE-A), che può essere selezionato in Teams/Meet/Zoom.
- La voce remota (es. in inglese) viene catturata dall'uscita virtuale (CABLE-B), tradotta in italiano e riprodotta nelle tue cuffie.


## Requisiti

- Python 3.9+
- Azure Speech Service key e region
- Driver audio installati (consigliato VB-CABLE per routing audio virtuale):
  - CABLE-A: usato come microfono verso la call (la TTS in uscita va qui)
  - CABLE-B: usato come altoparlante della call (catturiamo da qui l'audio remoti)

## Installazione

1) Crea ed attiva un virtualenv:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2) Installa le dipendenze:

```powershell
pip install -r requirements.txt
```

3) Configura le variabili d'ambiente: copia `.env.example` in `.env` e compila i valori.

```powershell
Copy-Item .env.example .env
```

## Esecuzione

```powershell
python main.py
```

Impostazioni consigliate nella tua app di videoconferenza:

- Microfono: CABLE-A (o equivalente – quello dove inviamo la TTS di uscita)
- Altoparlante: CABLE-B (o equivalente – quello che catturiamo per tradurre la voce remota)

## Struttura del progetto

- `main.py`: entry-point. Orchestrazione, setup e ciclo di vita.
- `translator_bidir/`
  - `config.py`: caricamento .env, config e logger
  - `devices.py`: ricerca e risoluzione dei device audio
  - `azure_clients.py`: factory di recognizer/synthesizer Azure
  - `audio.py`: utilità per riproduzione WAV
  - `workers.py`: worker thread TTS (queue -> synth -> play)
  - `capture.py`: thread di cattura da CABLE-B verso PushAudioInputStream
  - `handlers.py`: callback per eventi Azure (recognized, canceled, session)

## Note

- Il downsampling e il formato audio verso Azure sono fissati a 16kHz mono PCM 16-bit per la cattura remota.
- La logica di silenzio (gate) è minimale e può essere regolata in `capture.py`.
- Se i device non vengono trovati, il programma usa i default di sistema (dove possibile) e mostra un warning.

