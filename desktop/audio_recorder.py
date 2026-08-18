"""
Desktop Audio Recorder

- Polls the backend context endpoint for call_active
- When a call starts, records local microphone audio to WAV
- Uses faster-whisper (if available) or whisper CLI to transcribe locally
- Posts a TRANSCRIPT event to /api/events/ with transcript and metadata
- Stores local log files for auditing

Notes:
- Requires: sounddevice, soundfile, faster-whisper (optional), requests
- Install: pip install sounddevice soundfile requests faster-whisper
"""
import os
import time
import tempfile
import threading
import requests
import json
from datetime import datetime

API_URL = os.environ.get('CEREBRUS_API_URL', 'http://localhost:8000')
POLL_INTERVAL = 1.0
SAMPLE_RATE = 16000
CHANNELS = 1

try:
    import sounddevice as sd
    import soundfile as sf
    SOUND_AVAILABLE = True
except Exception:
    SOUND_AVAILABLE = False

# Try faster-whisper
try:
    from faster_whisper import WhisperModel
    FW_AVAILABLE = True
except Exception:
    FW_AVAILABLE = False


class AudioRecorder:
    def __init__(self, api_url: str = API_URL):
        self.api_url = api_url
        self.recording = False
        self._stop_event = threading.Event()
        self._thread = None
        self._file = None
        self._sf = None
        self._start_time = None
        self._end_time = None
        self.model = None
        if FW_AVAILABLE:
            try:
                # Use small model by default for local CPU
                self.model = WhisperModel("small", device="cpu", compute_type="int8")
            except Exception as e:
                print("Warning: faster-whisper model load failed:", e)
                self.model = None

    def _record_worker(self, filename: str):
        # Open soundfile for writing
        with sf.SoundFile(filename, mode='w', samplerate=SAMPLE_RATE, channels=CHANNELS, subtype='PCM_16') as file:
            self._sf = file
            def callback(indata, frames, time_info, status):
                if status:
                    print('Recording status:', status)
                file.write(indata)
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=callback):
                # Keep recording until stop event
                while not self._stop_event.is_set():
                    time.sleep(0.1)

    def _detect_speech(self, threshold=0.01, sample_duration=0.8, max_wait=10):
        """Simple energy-based VAD: records short samples until energy > threshold."""
        if not SOUND_AVAILABLE:
            return False
        waited = 0.0
        while waited < max_wait:
            try:
                rec = sd.rec(int(sample_duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16')
                sd.wait()
                # compute RMS
                import numpy as np
                rms = (rec.astype('float32') / 32768.0)
                rms_val = (rms ** 2).mean() ** 0.5
                # print('VAD rms', rms_val)
                if rms_val > threshold:
                    return True
            except Exception as e:
                print('VAD sampling error:', e)
                return True  # be permissive on error
            waited += sample_duration
        return False

    def start_recording(self, require_speech=False):
        if not SOUND_AVAILABLE:
            print("Error: sounddevice or soundfile not installed")
            return None
        if self.recording:
            return None

        if require_speech:
            print('Waiting for speech to begin...')
            has_speech = self._detect_speech()
            if not has_speech:
                print('No sustained speech detected within threshold; skipping recording.')
                return None

        tmpdir = tempfile.gettempdir()
        timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        filename = os.path.join(tmpdir, f"cerebrus_recording_{timestamp}.wav")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._record_worker, args=(filename,), daemon=True)
        self._thread.start()
        self.recording = True
        self._file = filename
        self._start_time = datetime.utcnow()
        print(f"Started recording to {filename}")
        return filename

    def stop_recording(self):
        if not self.recording:
            return None
        self._stop_event.set()
        self._thread.join(timeout=5)
        self._end_time = datetime.utcnow()
        self.recording = False
        duration = (self._end_time - self._start_time).total_seconds()
        print(f"Stopped recording. Duration: {duration}s file: {self._file}")
        return {
            'file': self._file,
            'start_time': self._start_time,
            'end_time': self._end_time,
            'duration': duration
        }

    def transcribe(self, audio_file: str) -> dict:
        # Prefer faster-whisper if available
        result = {
            'transcript': '',
            'segments': [],
            'provider': None
        }
        if FW_AVAILABLE and self.model is not None:
            try:
                segments, info = self.model.transcribe(audio_file, beam_size=5)
                text = "".join([s.text for s in segments])
                result['transcript'] = text
                result['segments'] = [{'start': s.start, 'end': s.end, 'text': s.text, 'confidence': getattr(s, 'avg_logprob', None)} for s in segments]
                result['provider'] = 'faster-whisper'
                return result
            except Exception as e:
                print('faster-whisper transcribe failed:', e)
        # Fallback: call whisper CLI if available
        try:
            import subprocess
            out_path = audio_file + '.txt'
            cmd = ['whisper', audio_file, '--model', 'small', '--output_format', 'txt', '--language', 'en', '--output_dir', tempfile.gettempdir()]
            subprocess.run(cmd, check=True)
            # read the generated txt file
            base = os.path.basename(audio_file)
            txtname = os.path.splitext(base)[0] + '.txt'
            txtpath = os.path.join(tempfile.gettempdir(), txtname)
            if os.path.exists(txtpath):
                with open(txtpath, 'r', encoding='utf-8') as f:
                    text = f.read()
                result['transcript'] = text
                result['provider'] = 'whisper-cli'
                return result
        except Exception as e:
            print('whisper CLI failed:', e)

        return result

    def post_transcript(self, transcript_obj: dict, audio_meta: dict, context: dict):
        payload = {
            'event_type': 'TRANSCRIPT',
            'source': 'audio_recorder',
            'data': {
                'transcript': transcript_obj.get('transcript'),
                'segments': transcript_obj.get('segments'),
                'audio_path': audio_meta.get('file'),
                'start_time': audio_meta.get('start_time').isoformat() if audio_meta.get('start_time') else None,
                'end_time': audio_meta.get('end_time').isoformat() if audio_meta.get('end_time') else None,
                'duration': audio_meta.get('duration'),
                'case_id': context.get('crm_case')
            }
        }
        try:
            resp = requests.post(f"{self.api_url}/api/events/", json=payload, timeout=30)
            if resp.status_code == 200:
                print('Posted transcript event')
            else:
                print('Failed to post transcript event:', resp.status_code, resp.text)
        except Exception as e:
            print('Error posting transcript:', e)


def poll_loop(api_url=API_URL):
    r = AudioRecorder(api_url)
    was_call_active = False
    recording_meta = None
    while True:
        try:
            resp = requests.get(f"{api_url}/api/context/current", timeout=5)
            if resp.status_code == 200:
                context = resp.json()
                call_active = context.get('call_active', False)
                # If call started, begin recording
                if call_active and not was_call_active:
                    # Start recording, require speech to avoid false positives
                    filename = r.start_recording(require_speech=True)
                    recording_meta = {'file': filename} if filename else None
                # If call ended, stop and transcribe
                if not call_active and was_call_active:
                    audio_info = r.stop_recording()
                    if audio_info and audio_info.get('file'):
                        # Transcribe
                        transcription = r.transcribe(audio_info['file'])
                        # include provider in payload
                        if transcription.get('provider'):
                            transcription['provider'] = transcription.get('provider')
                        # Post transcript to backend
                        r.post_transcript(transcription, audio_info, context)
                was_call_active = call_active
        except Exception as e:
            print('Poll loop error:', e)
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    print('Starting Cerebrus audio recorder (polling backend context)...')
    if not SOUND_AVAILABLE:
        print('Note: sounddevice/soundfile not available. Install to enable recording: pip install sounddevice soundfile')
    if not FW_AVAILABLE:
        print('Note: faster-whisper not available. Install for faster local transcription: pip install faster-whisper')
    poll_loop()
