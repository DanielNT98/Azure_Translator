"""
Script per generare un file Excel/CSV con i tempi precisi della conversazione.
Estrae tutti i timestamp con millisecondi e le frasi pronunciate.
"""

import re
import csv
from datetime import datetime
from pathlib import Path

def parse_timestamp(time_str):
    """Converte timestamp formato HH:MM:SS.mmm"""
    return time_str

def parse_log_file(log_path):
    """Parser del file di log per estrarre tutte le informazioni della conversazione"""
    
    conversations = []
    
    # Prima raccogliamo tutte le informazioni per speech session
    speech_sessions = {}  # key: (speaker, speech_start_time)
    
    with open(log_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    current_speech_key = None
    
    for line in lines:
        line = line.strip()
        timestamp_match = re.match(r'^(\d{2}:\d{2}:\d{2}(?:\.\d{3})?)', line)
        
        if not timestamp_match:
            continue
            
        timestamp = timestamp_match.group(1)
        
        # SPEECH START
        if 'TIMING [[UP-VAD]] SPEECH START | Time:' in line:
            match = re.search(r'Time: ([\d:\.]+)', line)
            if match:
                speech_start = match.group(1)
                current_speech_key = ('Daniel', speech_start)
                speech_sessions[current_speech_key] = {
                    'speaker': 'Daniel',
                    'speech_start': speech_start,
                    'speech_end': '',
                    'source_text': '',
                    'translation_received': '',
                    'translated_text': '',
                    'tts_start': '',
                    'tts_end': ''
                }
        
        elif 'TIMING [[DOWN-VAD]] SPEECH START | Time:' in line:
            match = re.search(r'Time: ([\d:\.]+)', line)
            if match:
                speech_start = match.group(1)
                current_speech_key = ('Vincenzo', speech_start)
                speech_sessions[current_speech_key] = {
                    'speaker': 'Vincenzo',
                    'speech_start': speech_start,
                    'speech_end': '',
                    'source_text': '',
                    'translation_received': '',
                    'translated_text': '',
                    'tts_start': '',
                    'tts_end': ''
                }
        
        # SPEECH END
        if current_speech_key:
            if '[UP-VAD] SPEECH END' in line and current_speech_key[0] == 'Daniel':
                speech_sessions[current_speech_key]['speech_end'] = timestamp
            
            elif '[DOWN-VAD] SPEECH END' in line and current_speech_key[0] == 'Vincenzo':
                speech_sessions[current_speech_key]['speech_end'] = timestamp
            
            # Source text
            elif '[UP] Source:' in line and current_speech_key[0] == 'Daniel':
                text = line.split('[UP] Source:', 1)[1].strip()
                if speech_sessions[current_speech_key]['source_text']:
                    speech_sessions[current_speech_key]['source_text'] += ' ' + text
                else:
                    speech_sessions[current_speech_key]['source_text'] = text
            
            elif '[DOWN] Source:' in line and current_speech_key[0] == 'Vincenzo':
                text = line.split('[DOWN] Source:', 1)[1].strip()
                if speech_sessions[current_speech_key]['source_text']:
                    speech_sessions[current_speech_key]['source_text'] += ' ' + text
                else:
                    speech_sessions[current_speech_key]['source_text'] = text
        
        # TRANSLATION RECEIVED (cerchiamo in tutte le sessioni)
        if 'TIMING [[[UP]]] TRANSLATION RECEIVED' in line:
            match = re.search(r"Text: '([^']+)'", line)
            if match:
                text = match.group(1)
                # Trova la sessione Daniel più recente che non ha ancora translation
                for key in reversed(list(speech_sessions.keys())):
                    if key[0] == 'Daniel' and not speech_sessions[key]['translation_received']:
                        speech_sessions[key]['translation_received'] = timestamp
                        speech_sessions[key]['translated_text'] = text
                        break
        
        elif 'TIMING [[[DOWN]]] TRANSLATION RECEIVED' in line:
            match = re.search(r"Text: '([^']+)'", line)
            if match:
                text = match.group(1)
                # Trova la sessione Vincenzo più recente che non ha ancora translation
                for key in reversed(list(speech_sessions.keys())):
                    if key[0] == 'Vincenzo' and not speech_sessions[key]['translation_received']:
                        speech_sessions[key]['translation_received'] = timestamp
                        speech_sessions[key]['translated_text'] = text
                        break
        
        # TTS START
        elif 'TIMING [[[UP]-TTS]] AUDIO PLAYBACK START' in line:
            for key in reversed(list(speech_sessions.keys())):
                if key[0] == 'Daniel' and not speech_sessions[key]['tts_start']:
                    speech_sessions[key]['tts_start'] = timestamp
                    break
        
        elif 'TIMING [[[DOWN]-TTS]] AUDIO PLAYBACK START' in line:
            for key in reversed(list(speech_sessions.keys())):
                if key[0] == 'Vincenzo' and not speech_sessions[key]['tts_start']:
                    speech_sessions[key]['tts_start'] = timestamp
                    break
        
        # TTS END
        elif 'TIMING [[[UP]-TTS]] AUDIO PLAYBACK END' in line:
            for key in reversed(list(speech_sessions.keys())):
                if key[0] == 'Daniel' and speech_sessions[key]['tts_start'] and not speech_sessions[key]['tts_end']:
                    speech_sessions[key]['tts_end'] = timestamp
                    break
        
        elif 'TIMING [[[DOWN]-TTS]] AUDIO PLAYBACK END' in line:
            for key in reversed(list(speech_sessions.keys())):
                if key[0] == 'Vincenzo' and speech_sessions[key]['tts_start'] and not speech_sessions[key]['tts_end']:
                    speech_sessions[key]['tts_end'] = timestamp
                    break
    
    # Converte in lista e ordina per speech_start
    for key in sorted(speech_sessions.keys(), key=lambda x: x[1]):
        session = speech_sessions[key]
        if session['source_text']:  # Include solo se ha testo
            conversations.append(session)
    
    return conversations

def generate_csv(conversations, output_path):
    """Genera il file CSV con i dati della conversazione"""
    
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        fieldnames = [
            'Speaker',
            'Speech Start',
            'Speech End',
            'Source Text',
            'Translation Received',
            'Translated Text',
            'TTS Start',
            'TTS End'
        ]
        
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for conv in conversations:
            writer.writerow({
                'Speaker': conv.get('speaker', ''),
                'Speech Start': conv.get('speech_start', ''),
                'Speech End': conv.get('speech_end', ''),
                'Source Text': conv.get('source_text', '').strip(),
                'Translation Received': conv.get('translation_received', ''),
                'Translated Text': conv.get('translated_text', '').strip(),
                'TTS Start': conv.get('tts_start', ''),
                'TTS End': conv.get('tts_end', '')
            })
    
    print(f"✅ CSV generato con successo: {output_path}")
    print(f"📊 Totale turni di conversazione: {len(conversations)}")
    print(f"   - Daniel (UP): {sum(1 for c in conversations if c['speaker'] == 'Daniel')}")
    print(f"   - Vincenzo (DOWN): {sum(1 for c in conversations if c['speaker'] == 'Vincenzo')}")

def generate_excel(conversations, output_path):
    """Genera il file Excel con formattazione"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Conversation Analysis"
        
        # Headers
        headers = ['Speaker', 'Speech Start', 'Speech End', 'Source Text', 
                   'Translation Received', 'Translated Text', 'TTS Start', 'TTS End']
        
        # Stile header
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        # Stili per i dati
        daniel_fill = PatternFill(start_color="E7F3FF", end_color="E7F3FF", fill_type="solid")
        vincenzo_fill = PatternFill(start_color="FFF3E7", end_color="FFF3E7", fill_type="solid")
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Popola i dati
        for row_idx, conv in enumerate(conversations, 2):
            speaker = conv.get('speaker', '')
            fill = daniel_fill if speaker == 'Daniel' else vincenzo_fill
            
            data = [
                speaker,
                conv.get('speech_start', ''),
                conv.get('speech_end', ''),
                conv.get('source_text', '').strip(),
                conv.get('translation_received', ''),
                conv.get('translated_text', '').strip(),
                conv.get('tts_start', ''),
                conv.get('tts_end', '')
            ]
            
            for col_idx, value in enumerate(data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.fill = fill
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                
                # Font bold per speaker
                if col_idx == 1:
                    cell.font = Font(bold=True)
        
        # Imposta larghezza colonne
        ws.column_dimensions['A'].width = 12  # Speaker
        ws.column_dimensions['B'].width = 15  # Speech Start
        ws.column_dimensions['C'].width = 15  # Speech End
        ws.column_dimensions['D'].width = 50  # Source Text
        ws.column_dimensions['E'].width = 18  # Translation Received
        ws.column_dimensions['F'].width = 50  # Translated Text
        ws.column_dimensions['G'].width = 15  # TTS Start
        ws.column_dimensions['H'].width = 15  # TTS End
        
        # Freeze della prima riga
        ws.freeze_panes = 'A2'
        
        wb.save(output_path)
        print(f"✅ Excel generato con successo: {output_path}")
        
    except ImportError:
        print("⚠️  openpyxl non disponibile, salto generazione Excel")

def main():
    # Path del log
    log_file = Path(__file__).parent / 'azure_translator_session_20251010_163354.log'
    output_csv = Path(__file__).parent / 'conversation_timing_analysis.csv'
    output_excel = Path(__file__).parent / 'conversation_timing_analysis.xlsx'
    
    print(f"📖 Lettura log: {log_file}")
    
    if not log_file.exists():
        print(f"❌ Errore: File log non trovato: {log_file}")
        return
    
    # Parse del log
    conversations = parse_log_file(log_file)
    
    # Genera CSV
    generate_csv(conversations, output_csv)
    
    # Genera Excel
    generate_excel(conversations, output_excel)
    
    print(f"\n💡 I file CSV e Excel possono essere aperti con Excel.")
    print(f"   Tutti i timestamp sono in formato HH:MM:SS.mmm (con millisecondi)")
    print(f"\n📁 File generati:")
    print(f"   - {output_csv}")
    print(f"   - {output_excel}")

if __name__ == '__main__':
    main()
