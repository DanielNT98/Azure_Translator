import azure.cognitiveservices.speech as speechsdk


def make_up_recognized_handler(cfg, tts_worker, logger):
    def _handler(evt: speechsdk.translation.TranslationRecognitionEventArgs):
        try:
            if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech:
                src = evt.result.text or ""
                tgt = evt.result.translations.get(cfg.TGT_LANG, "")
                if tgt:
                    logger.info(f"[UP {cfg.SRC_LANG}->{cfg.TGT_LANG}] SRC: {src} || TGT: {tgt}")
                    print(f"[UP {cfg.SRC_LANG}->{cfg.TGT_LANG}] {tgt}")
                    tts_worker.put(tgt)
            elif evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                logger.info(f"[UP {cfg.SRC_LANG}] Riconosciuto senza traduzione: {evt.result.text}")
            elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                logger.info("[UP] Nessuna corrispondenza")
        except Exception as e:
            logger.exception(f"[UP] recognized handler error: {e}")
    return _handler


def make_down_recognized_handler(cfg, tts_worker, logger):
    def _handler(evt: speechsdk.translation.TranslationRecognitionEventArgs):
        try:
            if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech:
                src = evt.result.text or ""
                tgt = evt.result.translations.get(cfg.DOWN_TGT_LANG, "")
                if tgt:
                    logger.info(f"[DOWN {cfg.DOWN_SRC_LANG}->{cfg.DOWN_TGT_LANG}] SRC: {src} || TGT: {tgt}")
                    print(f"[DOWN {cfg.DOWN_SRC_LANG}->{cfg.DOWN_TGT_LANG}] {tgt}")
                    tts_worker.put(tgt)
            elif evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                logger.info(f"[DOWN {cfg.DOWN_SRC_LANG}] Riconosciuto senza traduzione: {evt.result.text}")
            elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                logger.info("[DOWN] Nessuna corrispondenza")
        except Exception as e:
            logger.exception(f"[DOWN] recognized handler error: {e}")
    return _handler


def on_cancelled(prefix, logger):
    def _inner(evt):
        logger.warning(f"[{prefix} CANCELLED] reason={evt.reason} | error={getattr(evt,'error_details',None)}")
    return _inner


def on_session(prefix, started: bool, logger):
    def _inner(evt):
        logger.info(f"[{prefix} SESSION] {'Avviata' if started else 'Terminata'}")
    return _inner
