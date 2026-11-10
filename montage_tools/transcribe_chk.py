r"""Transcribe the short wav windows listed in work/chk.json (written by win_audio.py) with faster-whisper
large-v3-turbo, French, word timings. Run with the WhisperX venv: D:\Vibe coding\whisperx\.venv\Scripts\python.exe"""
import json, os
from faster_whisper import WhisperModel
import whisperx
W = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'work')
m = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')
for s, pth in json.load(open(os.path.join(W, 'chk.json'))).items():
    segs = m.transcribe(whisperx.load_audio(pth), language='fr', word_timestamps=True)[0]
    print('==', s, ':', ' '.join(f'{w.start:.1f}:{w.word.strip()}' for x in segs for w in (x.words or [])))
