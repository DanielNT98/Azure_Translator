# 📚 Documentazione ElevenLabs TTS - Indice

Questa cartella contiene tutta la documentazione per integrare e configurare ElevenLabs TTS.

---

## 🚀 Inizia Qui

### 1. **Quick Start** (5 minuti)
📄 [`ELEVENLABS_QUICKSTART.md`](./ELEVENLABS_QUICKSTART.md)
- Setup in 4 passi
- Configurazione veloce
- Verifica funzionamento

**Inizia da qui se**: Vuoi partire subito senza leggere troppo

---

## 🔧 Fix Audio Routing (CRITICAL)

### 2. **PCM Format Fix** (OBBLIGATORIO per VB-Cable)
📄 [`ELEVENLABS_PCM_FIX.md`](./ELEVENLABS_PCM_FIX.md)
- Guida passo-passo dettagliata
- Spiegazione tecnica del problema
- Troubleshooting completo
- Checklist verifica

**Leggi ASSOLUTAMENTE se**: Usi virtual audio cables (Cable-A/B) e l'audio non viene instradato correttamente

### 3. **Code Diff** (Per sviluppatori)
📄 [`ELEVENLABS_PCM_DIFF.md`](./ELEVENLABS_PCM_DIFF.md)
- Diff esatto delle modifiche
- Before/After code
- Spiegazione tecnica flusso
- Testing verifica

**Leggi se**: Vuoi capire esattamente cosa modificare nel codice

---

## 📖 Documentazione Completa

### 4. **Setup Completo**
📄 [`ELEVENLABS_SETUP.md`](./ELEVENLABS_SETUP.md)
- Installazione dipendenze
- Configurazione API key
- Selezione voci
- Configurazione avanzata
- Confronto Azure vs ElevenLabs
- Troubleshooting completo

**Leggi se**: Vuoi capire tutti i dettagli dell'integrazione

### 5. **System README**
📄 [`README.md`](./README.md)
- Overview sistema completo
- Modalità DeepL vs Azure
- Architettura modulare
- Performance optimizations
- Quick reference ElevenLabs

**Leggi se**: Vuoi capire il sistema nel suo complesso

---

## 🧪 Testing

### 6. **Test Script**
📄 [`test_elevenlabs.py`](./test_elevenlabs.py)
- Test isolato integrazione
- Lista voci disponibili
- Verifica configurazione

**Usa quando**: Vuoi testare solo ElevenLabs senza avviare il sistema completo

---

## 📊 Flowchart Documentazione

```
START
  ↓
Vuoi setup veloce?
  ├─ SÌ → ELEVENLABS_QUICKSTART.md (5 min)
  │         ↓
  │    Audio NON funziona su Cable-A?
  │         ├─ SÌ → ELEVENLABS_PCM_FIX.md (CRITICAL)
  │         └─ NO → ✅ COMPLETO!
  │
  └─ NO → Vuoi capire tutto nel dettaglio?
            ├─ SÌ → ELEVENLABS_SETUP.md (completo)
            │         ↓
            │    Serve modificare codice?
            │         └─ SÌ → ELEVENLABS_PCM_DIFF.md (diff)
            │
            └─ NO → README.md (overview sistema)
```

---

## ❓ FAQ Veloce

### Q: Ho installato ElevenLabs ma l'audio va sugli speaker, non su Cable-A
**A**: Devi applicare il PCM format fix. Leggi `ELEVENLABS_PCM_FIX.md`

### Q: Voglio testare ElevenLabs senza modificare il sistema
**A**: Usa `test_elevenlabs.py`

### Q: Come scelgo una voce diversa?
**A**: Vedi sezione "Configurazione Avanzata" in `ELEVENLABS_SETUP.md`

### Q: Perché Azure funziona e ElevenLabs no?
**A**: Azure usa PCM, ElevenLabs usa MP3. Vedi spiegazione in `ELEVENLABS_PCM_FIX.md`

### Q: Devo modificare il codice di sistema?
**A**: NO, solo il file `elevenlabs_engine.py` di RealtimeTTS (pacchetto installato)

---

## 🎯 Percorsi Comuni

### Percorso 1: Setup Standard (VB-Cable)
1. `ELEVENLABS_QUICKSTART.md` → Setup base (5 min)
2. `ELEVENLABS_PCM_FIX.md` → Fix audio routing (10 min)
3. `test_elevenlabs.py` → Test (2 min)
4. ✅ Operativo!

**Tempo totale**: ~20 minuti

### Percorso 2: Setup Semplice (Senza VB-Cable)
1. `ELEVENLABS_QUICKSTART.md` → Setup base (5 min)
2. Skip PCM fix (audio va su default device, ok per test)
3. `test_elevenlabs.py` → Test (2 min)
4. ✅ Operativo!

**Tempo totale**: ~7 minuti

### Percorso 3: Studio Completo
1. `README.md` → Comprendi sistema (15 min)
2. `ELEVENLABS_SETUP.md` → Dettagli integrazione (20 min)
3. `ELEVENLABS_PCM_FIX.md` → Comprendi problema audio (10 min)
4. `ELEVENLABS_PCM_DIFF.md` → Analizza modifiche (10 min)
5. Implementazione e test (15 min)

**Tempo totale**: ~70 minuti

---

## 📝 File Modificati nel Sistema

Durante setup, modificherai:

### File Esterno (Pacchetto Installato)
- ✏️ `RealtimeTTS/engines/elevenlabs_engine.py` - Fix PCM format

### File Sistema (Già presenti)
- `.env` - Aggiungi API key e configurazione
- Nessun altro file di codice richiede modifiche!

---

## 🔄 Versioning

| Versione | Data | Modifiche |
|----------|------|-----------|
| v3.0 | 2024-10-20 | Integrazione ElevenLabs + PCM fix |
| v2.0 | 2024-10-XX | Optimizations + RealtimeTTS |
| v1.0 | 2024-XX-XX | Sistema dual-mode DeepL/Azure |

---

## 🆘 Supporto

Se hai problemi:

1. **Check troubleshooting**: Ogni doc ha sezione dedicata
2. **Verifica logs**: Sistema genera logs dettagliati in `logs/`
3. **Test isolato**: Usa `test_elevenlabs.py` per isolare problema
4. **Consulta diff**: `ELEVENLABS_PCM_DIFF.md` per verificare modifiche

---

## 📌 Link Utili

- [ElevenLabs API Docs](https://elevenlabs.io/docs/api-reference/text-to-speech)
- [RealtimeTTS GitHub](https://github.com/KoljaB/RealtimeTTS)
- [ElevenLabs Voices](https://elevenlabs.io/voice-library)

---

**✅ Segui il percorso appropriato e sarai operativo in pochi minuti!**
