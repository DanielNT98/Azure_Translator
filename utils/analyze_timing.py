#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di analisi timing per Azure Translator con DeepL
Genera report dettagliato con statistiche sui delay delle varie fasi
"""

import re
import sys
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import statistics

# Configura encoding UTF-8 per stdout/stderr su Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, errors='replace')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, errors='replace')


class TimingAnalyzer:
    """Analizzatore di timing dai log"""
    
    def __init__(self, log_file):
        self.log_file = log_file
        self.timings = defaultdict(list)
        self.sessions = []
        self.current_session = None
        
    def parse_logs(self):
        """Estrae timing e sessioni dai log"""
        # Pattern per timing con Delay
        # Formato: HH:MM:SS [INFO] TIMING [[[label]]] phase | Delay: XXms | extra
        # Nota: label ha triple [ ma solo doppie ] → [[[label]]
        timing_pattern_delay = r'(\d{2}:\d{2}:\d{2})\s+\[INFO\]\s+TIMING\s+\[\[\[(.+?)\]\]\s+([^|]+?)\s+\|\s+Delay:\s+([\d.]+)ms\s+\|\s+(.+)'
        # Pattern per timing con Time (primo evento senza delay)
        timing_pattern_time = r'(\d{2}:\d{2}:\d{2})\s+\[INFO\]\s+TIMING\s+\[\[\[(.+?)\]\]\s+([^|]+?)\s+\|\s+Time:'
        
        session_start_pattern = r'(\d{2}:\d{2}:\d{2}).*DeepL Translator started'
        session_end_pattern = r'(\d{2}:\d{2}:\d{2}).*DeepL Translator stopped'
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            for line in lines:
                # Rileva inizio sessione
                session_match = re.search(session_start_pattern, line)
                if session_match:
                    timestamp = session_match.group(1)
                    if self.current_session:
                        self.sessions.append(self.current_session)
                    self.current_session = {
                        'start': timestamp,
                        'end': None,
                        'timings': defaultdict(list)
                    }
                
                # Rileva fine sessione
                end_match = re.search(session_end_pattern, line)
                if end_match and self.current_session:
                    self.current_session['end'] = end_match.group(1)
                    self.sessions.append(self.current_session)
                    self.current_session = None
                
                # Estrai timing con Delay
                timing_match = re.search(timing_pattern_delay, line)
                if timing_match:
                    timestamp, label, phase, delay, extra = timing_match.groups()
                    key = f"{label}:{phase.strip()}"
                    value = float(delay)
                    
                    # Aggiungi a timing globali
                    self.timings[key].append(value)
                    
                    # Aggiungi a sessione corrente
                    if self.current_session:
                        self.current_session['timings'][key].append(value)
                
                # Estrai timing con Time (non aggiungiamo perché non ha delay)
                # Ma possiamo contarli per statistiche
                time_match = re.search(timing_pattern_time, line)
                if time_match:
                    # Semplicemente riconosciamo che c'è un evento di timing
                    pass
            
            # Se c'è una sessione aperta, chiudila
            if self.current_session:
                self.current_session['end'] = 'ongoing'
                self.sessions.append(self.current_session)
                
        except FileNotFoundError:
            print(f"❌ File non trovato: {self.log_file}")
            print(f"   Assicurati che il file esista in: {Path(self.log_file).absolute()}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Errore parsing: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    def calculate_stats(self, values):
        """Calcola statistiche da una lista di valori"""
        if not values:
            return None
        
        return {
            'count': len(values),
            'avg': statistics.mean(values),
            'median': statistics.median(values),
            'min': min(values),
            'max': max(values),
            'stdev': statistics.stdev(values) if len(values) > 1 else 0,
            'p95': sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else values[0]
        }
    
    def generate_report(self):
        """Genera report completo"""
        if not self.timings and not self.sessions:
            print("⚠️  Nessun dato di timing trovato nei log")
            print("")
            print("💡 Suggerimenti:")
            print("   1. Assicurati che il programma sia stato eseguito almeno una volta")
            print("   2. Verifica che i log contengano linee con '⏱️' (emoji orologio)")
            print("   3. Controlla che il file log sia quello corretto")
            print(f"   4. File analizzato: {Path(self.log_file).absolute()}")
            return None
        
        report = []
        report.append("=" * 80)
        report.append("📊 TIMING ANALYSIS REPORT - Azure Translator with DeepL")
        report.append("=" * 80)
        report.append(f"📅 Report generato: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"📄 Log file: {self.log_file}")
        report.append("")
        
        # Statistiche per sessione
        if self.sessions:
            report.append("=" * 80)
            report.append("📈 STATISTICHE PER SESSIONE")
            report.append("=" * 80)
            
            for i, session in enumerate(self.sessions, 1):
                report.append(f"\n🔹 Sessione #{i}")
                report.append(f"   Inizio: {session['start']}")
                report.append(f"   Fine:   {session['end']}")
                
                if session['timings']:
                    total_translations = sum(len(v) for k, v in session['timings'].items() 
                                            if 'TRANSLATION RECEIVED' in k)
                    report.append(f"   Traduzioni: {total_translations}")
                    
                    # Calcola delay medio end-to-end per questa sessione
                    for channel in ['UP', 'DOWN']:
                        translation_key = f"{channel}:📝 TRANSLATION RECEIVED"
                        playback_key = f"{channel}-TTS:🔈 AUDIO PLAYBACK START"
                        
                        if translation_key in session['timings'] and playback_key in session['timings']:
                            # End-to-end = translation_delay + playback_delay
                            e2e_delays = []
                            for trans, play in zip(session['timings'][translation_key], 
                                                   session['timings'][playback_key]):
                                e2e_delays.append(trans + play)
                            
                            if e2e_delays:
                                stats = self.calculate_stats(e2e_delays)
                                report.append(f"   [{channel}] End-to-End medio: {stats['avg']:.1f}ms "
                                            f"(min: {stats['min']:.1f}ms, max: {stats['max']:.1f}ms)")
        
        # Statistiche globali per fase
        report.append("\n" + "=" * 80)
        report.append("📊 STATISTICHE GLOBALI PER FASE")
        report.append("=" * 80)
        report.append("")
        
        # Raggruppa per canale
        channels = set()
        for key in self.timings.keys():
            channel = key.split(':')[0]
            channels.add(channel)
        
        for channel in sorted(channels):
            channel_timings = {k: v for k, v in self.timings.items() if k.startswith(channel + ':')}
            
            if not channel_timings:
                continue
            
            report.append(f"┌{'─' * 78}┐")
            report.append(f"│ 📡 CANALE: {channel:<66} │")
            report.append(f"└{'─' * 78}┘")
            report.append("")
            
            # Ordina per fase logica con descrizioni (supporta con e senza emoji)
            phase_descriptions = {
                'SPEECH START': '(Inizio parlato - evento di riferimento)',
                'TRANSLATION RECEIVED': '(Tempo da speech start a traduzione ricevuta)',
                'TTS RECEIVED TEXT': '(Overhead locale: traduzione → TTS)',
                'TTS SYNTHESIS COMPLETE': '(Tempo sintesi vocale Azure)',
                'AUDIO PLAYBACK START': '(Tempo totale: TTS riceve → audio inizia)',
                'AUDIO PLAYBACK END': '(Durata riproduzione audio completa)'
            }
            
            phase_order = list(phase_descriptions.keys())
            
            for phase in phase_order:
                matching_keys = [k for k in channel_timings.keys() if phase in k]
                
                for key in matching_keys:
                    values = channel_timings[key]
                    stats = self.calculate_stats(values)
                    
                    if stats:
                        # Aggiungi descrizione alla fase
                        phase_with_desc = f"{phase} {phase_descriptions.get(phase, '')}"
                        report.append(f"  {phase_with_desc}")
                        report.append(f"    📊 Campioni:   {stats['count']}")
                        report.append(f"    📈 Media:      {stats['avg']:>8.1f} ms")
                        report.append(f"    📉 Mediana:    {stats['median']:>8.1f} ms")
                        report.append(f"    ⬇️  Minimo:     {stats['min']:>8.1f} ms")
                        report.append(f"    ⬆️  Massimo:    {stats['max']:>8.1f} ms")
                        report.append(f"    📊 Dev.Std:    {stats['stdev']:>8.1f} ms")
                        report.append(f"    🎯 P95:        {stats['p95']:>8.1f} ms")
                        
                        # Aggiungi interpretazione in base al valore medio
                        avg = stats['avg']
                        if 'TTS RECEIVED TEXT' in phase:
                            if avg < 10:
                                report.append(f"    ✅ Ottimo: Overhead locale minimo")
                            elif avg < 50:
                                report.append(f"    ⚠️  Accettabile: Overhead leggermente alto")
                            else:
                                report.append(f"    ❌ Problematico: Overhead eccessivo")
                        elif 'TTS SYNTHESIS' in phase:
                            if avg < 300:
                                report.append(f"    ✅ Ottimo: Sintesi molto veloce")
                            elif avg < 500:
                                report.append(f"    ✅ Buono: Sintesi veloce (target <500ms)")
                            elif avg < 800:
                                report.append(f"    ⚠️  Accettabile: Sintesi un po' lenta")
                            else:
                                report.append(f"    ❌ Lento: Sintesi troppo lenta (>800ms)")
                        elif 'AUDIO PLAYBACK START' in phase:
                            if avg < 500:
                                report.append(f"    ✅ Ottimo: Audio inizia velocemente")
                            elif avg < 1000:
                                report.append(f"    ⚠️  Accettabile: Latenza percettibile")
                            else:
                                report.append(f"    ❌ Lento: Latenza troppo alta")
                        
                        report.append("")
            
            report.append("")
        
        # Analisi bottleneck
        report.append("=" * 80)
        report.append("🔍 ANALISI BOTTLENECK")
        report.append("=" * 80)
        report.append("")
        
        bottlenecks = []
        
        for key, values in self.timings.items():
            stats = self.calculate_stats(values)
            if not stats:
                continue
            
            # Identifica problemi
            if 'TRANSLATION RECEIVED' in key and stats['avg'] > 1500:
                bottlenecks.append(f"⚠️  {key}: Media {stats['avg']:.1f}ms > 1500ms (target)")
            
            if 'TTS SYNTHESIS COMPLETE' in key and stats['avg'] > 800:
                bottlenecks.append(f"⚠️  {key}: Media {stats['avg']:.1f}ms > 800ms (target)")
            
            if 'PLAYBACK START' in key and stats['avg'] > 50:
                # Questo è normale perché include il tempo di sintesi
                pass
            
            if stats['max'] > stats['avg'] * 2:
                bottlenecks.append(f"⚠️  {key}: Max ({stats['max']:.1f}ms) >> Media ({stats['avg']:.1f}ms) - Variabilità alta")
        
        if bottlenecks:
            for issue in bottlenecks:
                report.append(issue)
        else:
            report.append("✅ Nessun bottleneck significativo rilevato!")
        
        report.append("")
        
        # Raccomandazioni
        report.append("=" * 80)
        report.append("💡 RACCOMANDAZIONI")
        report.append("=" * 80)
        report.append("")
        
        recommendations = []
        
        for key, values in self.timings.items():
            stats = self.calculate_stats(values)
            if not stats:
                continue
            
            if 'TRANSLATION RECEIVED' in key:
                if stats['avg'] > 2000:
                    recommendations.append("🔴 CRITICO: Latenza traduzione molto alta (>2s). Verifica connessione DeepL.")
                elif stats['avg'] > 1500:
                    recommendations.append("🟡 WARNING: Latenza traduzione alta. Considera ottimizzazione rete o cambio endpoint.")
                
                if stats['stdev'] > 500:
                    recommendations.append("🟡 WARNING: Variabilità alta nella traduzione. Connessione instabile?")
            
            if 'TTS SYNTHESIS COMPLETE' in key:
                if stats['avg'] > 1000:
                    recommendations.append("🔴 CRITICO: Sintesi TTS molto lenta (>1s). Verifica connessione Azure.")
                elif stats['avg'] > 800:
                    recommendations.append("🟡 WARNING: Sintesi TTS lenta. Considera voice più veloce o cache.")
        
        # End-to-end analysis
        for channel in ['UP', 'DOWN']:
            translation_key = f"{channel}:📝 TRANSLATION RECEIVED"
            playback_key = f"{channel}-TTS:🔈 AUDIO PLAYBACK START"
            
            if translation_key in self.timings and playback_key in self.timings:
                trans_stats = self.calculate_stats(self.timings[translation_key])
                play_stats = self.calculate_stats(self.timings[playback_key])
                
                if trans_stats and play_stats:
                    # Stima end-to-end
                    e2e_avg = trans_stats['avg'] + play_stats['avg']
                    
                    if e2e_avg > 3000:
                        recommendations.append(f"🔴 CRITICO [{channel}]: Latenza end-to-end {e2e_avg:.0f}ms (>3s). Utente noterà ritardo significativo.")
                    elif e2e_avg > 2000:
                        recommendations.append(f"🟡 WARNING [{channel}]: Latenza end-to-end {e2e_avg:.0f}ms (>2s). Limite accettabilità.")
                    else:
                        recommendations.append(f"✅ OK [{channel}]: Latenza end-to-end {e2e_avg:.0f}ms (<2s). Buona esperienza utente.")
        
        if recommendations:
            for rec in recommendations:
                report.append(rec)
        else:
            report.append("✅ Sistema performante! Tutte le metriche nei target.")
        
        report.append("")
        report.append("=" * 80)
        report.append(f"📄 Report salvato: {self.log_file.replace('.log', '_timing_report.txt')}")
        report.append("=" * 80)
        
        return "\n".join(report)
    
    def save_report(self, report):
        """Salva report su file"""
        output_file = self.log_file.replace('.log', '_timing_report.txt')
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"✅ Report salvato: {output_file}")
            return output_file
        except Exception as e:
            print(f"❌ Errore salvataggio report: {e}")
            return None


def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        log_file = sys.argv[1]
    else:
        # Default log file
        log_file = "azure_translator_bidir.log"
    
    print(f"🔍 Analizzando {log_file}...")
    print("")
    
    analyzer = TimingAnalyzer(log_file)
    analyzer.parse_logs()
    
    report = analyzer.generate_report()
    
    if report:
        # Stampa a console
        print(report)
        print("")
        
        # Salva su file
        analyzer.save_report(report)
    else:
        print("⚠️  Nessun dato di timing trovato nel file di log")
        print("")
        print("💡 Questo è normale se:")
        print("   • Lo script è stato appena avviato e interrotto immediatamente")
        print("   • Non sono state effettuate traduzioni durante la sessione")
        print("   • Il file di log non contiene ancora dati di timing")
        print("")
        # Esci con codice 0 invece di 1 per non mostrare come errore
        sys.exit(0)


if __name__ == "__main__":
    main()
