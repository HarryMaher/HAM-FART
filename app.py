import argparse
import os
import queue
import threading
import time
import wave
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyaudio

# Audio configuration
CHUNK_SIZE = 1024
FORMAT = pyaudio.paFloat32
CHANNELS = 1
RATE = 44100
TONE_DURATION = 0.1  # Duration of each layered character tone in seconds
CHAR_GAP = 0.01  # Brief silence between characters
SECTION_GAP = 0.03  # Pause between preamble / payload / trailer sections
CHAR_STRIDE = int(RATE * (TONE_DURATION + CHAR_GAP))
PREAMBLE_THRESHOLD = 0.75
TRAILER_THRESHOLD = 0.80
DECODE_SCORE_THRESHOLD = 0.68  # Min spectral score to accept a character
DECODE_SCORE_MARGIN = 0.04  # Best score must beat second-best by this much
LOOPBACK_MATCH_THRESHOLD = 0.60
CHECKSUM_LENGTH = 2  # Two hex digits appended before trailer
CHECKSUM_CHARSET = "0123456789ABCDEF"


class HAMFART:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.audio_queue = queue.Queue()
        self.is_receiving = False
        self._receive_thread: Optional[threading.Thread] = None
        self._duplex_stream = None
        self._play_buffer: Optional[np.ndarray] = None
        self._play_pos = 0
        self._play_done = threading.Event()
        self._play_lock = threading.Lock()
        self._incoming_audio = queue.Queue()
        self._decode_thread: Optional[threading.Thread] = None
        self._decode_lock = threading.Lock()
        self.decoded_chars: List[str] = []
        self.decoded_payload: str = ""
        self.checksum_valid: Optional[bool] = None
        self.message_complete = threading.Event()
        self.character_map = self._create_character_map()
        self.pair_to_chars = self._build_pair_to_chars()
        self.decode_state = "idle"  # idle | message
        self._decode_charset: Optional[str] = None
        self._expected_payload_len: Optional[int] = None
        self._expected_total_len: Optional[int] = None

        # Load audio files (all assets live in Official_Farts/)
        self.fake_preamble = self._load_audio_file(
            "Official_Farts/fake_preamble.wav")
        self.preamble = self._load_audio_file("Official_Farts/preamble.wav")
        self.trailer = self._load_audio_file("Official_Farts/trailer.wav")
        self.fart_tones = self._load_fart_tones()

        # Load Morse code audio files
        self.short_morse = self._load_audio_file(
            "Official_Farts/short_morse.wav")
        self.long_morse = self._load_audio_file(
            "Official_Farts/long_morse.wav")

        # Audio processing parameters
        self.buffer_size = int(RATE * TONE_DURATION)
        self.audio_buffer = np.array([], dtype=np.float32)

        # Pre-compute layered tone templates keyed by normalized tone pair
        self.pair_templates = self._build_pair_templates()

        # Morse code mapping for call sign
        self.morse_map = {
            'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.',
            'F': '..-.', 'G': '--.', 'H': '....', 'I': '..', 'J': '.---',
            'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---',
            'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
            'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--',
            'Z': '--..', '0': '-----', '1': '.----', '2': '..---', '3': '...--',
            '4': '....-', '5': '.....', '6': '-....', '7': '--...', '8': '---..',
            '9': '----.'
        }

    def _uppercase_tone_pair(self, index: int) -> Tuple[int, int]:
        if index <= 8:
            return (1, index + 2)
        if index <= 16:
            return (2, index - 6)
        if index == 17:
            return (3, 3)
        if index <= 24:
            return (3, index - 14)
        return (4, 5)

    def _lowercase_tone_pair(self, index: int) -> Tuple[int, int]:
        if index <= 6:
            return (3, index + 4)
        if index <= 12:
            return (4, index - 2)
        if index <= 17:
            return (5, index - 7)
        if index <= 21:
            return (6, index - 11)
        if index <= 24:
            return (7, index - 14)
        return (8, 9)

    def _number_tone_pair(self, index: int) -> Tuple[int, int]:
        if index <= 4:
            return (5, index + 6)
        if index <= 8:
            return (6, index + 2)
        return (7, 8)

    def _create_character_map(self) -> Dict[str, Tuple[int, int]]:
        """Create mapping between characters and tone pairs (README table)."""
        mapping = {}

        for i, char in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
            mapping[char] = self._uppercase_tone_pair(i)

        for i, char in enumerate('abcdefghijklmnopqrstuvwxyz'):
            mapping[char] = self._lowercase_tone_pair(i)

        for i, char in enumerate('0123456789'):
            mapping[char] = self._number_tone_pair(i)

        special_chars = {
            ' ': (7, 9), '\t': (7, 10), '\n': (8, 9),
            '!': (8, 10), '.': (9, 10), ',': (1, 1),
            '(': (2, 2), ')': (3, 3), '%': (4, 4),
            '#': (5, 5), '?': (6, 6), '+': (7, 7),
            '*': (8, 8), '@': (9, 9), '^': (10, 10),
            '<': (1, 3), '>': (2, 4), '/': (3, 5),
            "'": (4, 6), '"': (5, 7), '=': (6, 8),
            '&': (7, 9), ';': (8, 10), ':': (1, 4),
            '-': (2, 5), '_': (3, 6), '{': (1, 2),
            '}': (4, 7), '[': (5, 8), ']': (6, 9)
        }
        mapping.update(special_chars)

        return mapping

    def _build_pair_to_chars(self) -> Dict[Tuple[int, int], List[str]]:
        pair_map: Dict[Tuple[int, int], List[str]] = {}
        for char, pair in self.character_map.items():
            normalized = self._normalize_tone_pair(*pair)
            pair_map.setdefault(normalized, []).append(char)
        return pair_map

    def _normalize_tone_pair(self, tone1: int, tone2: int) -> Tuple[int, int]:
        if tone1 == tone2:
            return (tone1, tone2)
        return (min(tone1, tone2), max(tone1, tone2))

    def _load_audio_file(self, filename: str) -> np.ndarray:
        """Load a WAV file and return its audio data as float32 [-1, 1]."""
        try:
            with wave.open(filename, 'rb') as wf:
                sample_width = wf.getsampwidth()
                audio_data = wf.readframes(wf.getnframes())

                if sample_width == 2:
                    dtype = np.int16
                elif sample_width == 4:
                    dtype = np.int32
                else:
                    dtype = np.int8

                audio_array = np.frombuffer(audio_data, dtype=dtype).astype(np.float32)
                audio_array /= np.iinfo(dtype).max
                return audio_array
        except FileNotFoundError:
            print(f"Warning: {filename} not found")
            return np.array([], dtype=np.float32)

    def _load_fart_tones(self) -> List[np.ndarray]:
        """Load all fart tone WAV files from the Official_Farts folder."""
        tones = []
        for i in range(1, 21):
            filename = f"Official_Farts/{i}_mod.wav"
            tone = self._load_audio_file(filename)
            if len(tone) > 0:
                tones.append(tone)
            else:
                print(f"Warning: Could not load {filename}")
        return tones

    def _fit_segment(self, audio_segment: np.ndarray) -> np.ndarray:
        """Resize or pad a segment to the standard tone buffer size."""
        if len(audio_segment) == self.buffer_size:
            return audio_segment
        if len(audio_segment) > self.buffer_size:
            return audio_segment[:self.buffer_size]
        padded = np.zeros(self.buffer_size, dtype=np.float32)
        padded[:len(audio_segment)] = audio_segment
        return padded

    def _spectral_correlation(self, audio1: np.ndarray, audio2: np.ndarray) -> float:
        """Cosine similarity between magnitude spectra (works for layered tones)."""
        if len(audio1) == 0 or len(audio2) == 0:
            return 0.0

        spec1 = np.abs(np.fft.rfft(self._fit_segment(audio1)))
        spec2 = np.abs(np.fft.rfft(self._fit_segment(audio2)))
        length = min(len(spec1), len(spec2))
        spec1 = spec1[:length]
        spec2 = spec2[:length]

        norm1 = np.linalg.norm(spec1)
        norm2 = np.linalg.norm(spec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(spec1, spec2) / (norm1 * norm2))

    def _mix_tones(self, tone1: int, tone2: int) -> np.ndarray:
        """Layer two fart tones into a single Dual-Toot burst."""
        audio1 = self.fart_tones[tone1 - 1]
        audio2 = self.fart_tones[tone2 - 1]
        length = min(len(audio1), len(audio2), self.buffer_size)
        mixed = audio1[:length] + audio2[:length]
        peak = np.max(np.abs(mixed))
        if peak > 0:
            mixed = mixed / peak
        return self._fit_segment(mixed)

    def _build_pair_templates(self) -> Dict[Tuple[int, int], np.ndarray]:
        templates = {}
        for pair in set(self.character_map.values()):
            normalized = self._normalize_tone_pair(*pair)
            if normalized not in templates:
                templates[normalized] = self._mix_tones(*pair)
        return templates

    def _silence(self, duration: float) -> np.ndarray:
        return np.zeros(int(RATE * duration), dtype=np.float32)

    def _get_char_audio(self, char: str) -> np.ndarray:
        if char not in self.character_map:
            return np.array([], dtype=np.float32)
        tone1, tone2 = self.character_map[char]
        return self._mix_tones(tone1, tone2)

    def decode_tone_pair(self, audio_segment: np.ndarray) -> Tuple[Optional[Tuple[int, int]], float]:
        """Identify which layered tone pair best matches an audio segment."""
        if len(audio_segment) == 0 or not self.pair_templates:
            return None, 0.0

        best_pair = None
        best_score = -1.0

        for pair, template in self.pair_templates.items():
            score = self._spectral_correlation(audio_segment, template)
            if score > best_score:
                best_score = score
                best_pair = pair

        return best_pair, best_score

    def decode_character(
        self,
        audio_segment: np.ndarray,
        charset: Optional[str] = None
    ) -> Tuple[Optional[str], float]:
        """Decode a layered segment into a character, optionally constrained to a charset."""
        if len(audio_segment) == 0 or not self.pair_templates:
            return None, 0.0

        scores: List[Tuple[float, Tuple[int, int]]] = []
        for pair, template in self.pair_templates.items():
            scores.append((self._spectral_correlation(audio_segment, template), pair))
        scores.sort(reverse=True)

        best_score, best_pair = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else 0.0

        if best_score < DECODE_SCORE_THRESHOLD:
            return None, best_score
        if charset is None and best_score - second_score < DECODE_SCORE_MARGIN:
            return None, best_score

        candidates = self.pair_to_chars.get(best_pair, [])
        if not candidates:
            return None, best_score

        if charset:
            for char in candidates:
                if char in charset:
                    return char, best_score
            return None, best_score

        return candidates[0], best_score

    def match_score_in_recording(
        self,
        recorded_audio: np.ndarray,
        reference_audio: np.ndarray
    ) -> float:
        """Return the best spectral match score for a reference within a recording."""
        if len(recorded_audio) == 0 or len(reference_audio) == 0:
            return 0.0

        if len(recorded_audio) <= self.buffer_size:
            return self._spectral_correlation(recorded_audio, reference_audio)

        best_score = -1.0
        step = max(1, CHUNK_SIZE // 4)
        for start in range(0, len(recorded_audio) - self.buffer_size, step):
            segment = recorded_audio[start:start + self.buffer_size]
            score = self._spectral_correlation(segment, reference_audio)
            if score > best_score:
                best_score = score
        return best_score

    def _decode_message_from_recording(
        self,
        recording: np.ndarray,
        text: str
    ) -> str:
        """Slide across a continuous recording to recover a known character sequence."""
        decoded = []
        offset = 0
        step = max(1, CHUNK_SIZE // 8)

        for char in text:
            expected_audio = self._get_char_audio(char)
            best_score = -1.0
            best_at = offset

            search_end = len(recording) - self.buffer_size
            for start in range(offset, search_end + 1, step):
                segment = recording[start:start + self.buffer_size]
                score = self._spectral_correlation(segment, expected_audio)
                if score > best_score:
                    best_score = score
                    best_at = start

            if best_score >= LOOPBACK_MATCH_THRESHOLD:
                decoded.append(char)
                offset = best_at + CHAR_STRIDE
            else:
                decoded.append("?")

        return "".join(decoded)

    def _decode_signed_from_recording(
        self,
        recording: np.ndarray,
        message: str
    ) -> Tuple[str, bool]:
        """Recover payload from a recording and verify its checksum."""
        signed = self._signed_payload(message)
        decoded = self._decode_message_from_recording(recording, signed)
        if len(decoded) < len(signed):
            return decoded[:len(message)], False

        payload = decoded[:len(message)]
        received_checksum = decoded[len(message):len(signed)]
        valid = self._validate_checksum(payload, received_checksum)
        return payload, valid

    def _flush_decode(self):
        """Process any remaining mic audio after playback finishes."""
        for _ in range(200):
            if self.message_complete.is_set():
                return
            before = (len(self.decoded_chars), len(self.audio_buffer))
            self._process_audio_buffer()
            after = (len(self.decoded_chars), len(self.audio_buffer))
            if before == after:
                break

    def _prefix_correlation(self, buffer_audio: np.ndarray, reference: np.ndarray) -> float:
        if len(buffer_audio) == 0 or len(reference) == 0:
            return 0.0
        length = min(len(buffer_audio), len(reference))
        return self._spectral_correlation(buffer_audio[:length], reference[:length])

    def _compute_checksum(self, message: str) -> str:
        """Return a 2-char hex checksum for the message payload."""
        value = sum(ord(char) for char in message) % 256
        return f"{value:02X}"

    def _validate_checksum(self, payload: str, checksum: str) -> bool:
        if len(checksum) != CHECKSUM_LENGTH:
            return False
        return checksum.upper() == self._compute_checksum(payload)

    def _signed_payload(self, message: str) -> str:
        return message + self._compute_checksum(message)

    def _decode_charset_for_message(self, message: str) -> str:
        return message + CHECKSUM_CHARSET

    def reset_receiver(self, expected_payload: Optional[str] = None):
        """Clear decode state before a new message."""
        with self._decode_lock:
            self.decoded_chars = []
        self.decoded_payload = ""
        self.checksum_valid = None
        self.message_complete.clear()
        self.decode_state = "idle"
        self.audio_buffer = np.array([], dtype=np.float32)
        if expected_payload is not None:
            self._decode_charset = self._decode_charset_for_message(expected_payload)
            self._expected_payload_len = len(expected_payload)
            self._expected_total_len = len(expected_payload) + CHECKSUM_LENGTH
        else:
            self._decode_charset = None
            self._expected_payload_len = None
            self._expected_total_len = None

    def get_decoded_message(self) -> str:
        """Return the payload only after checksum validation; else raw stream."""
        if self.message_complete.is_set():
            return self.decoded_payload
        with self._decode_lock:
            return ''.join(self.decoded_chars)

    def _finalize_message(self):
        with self._decode_lock:
            raw = ''.join(self.decoded_chars)

        if len(raw) < CHECKSUM_LENGTH:
            self.decoded_payload = raw
            self.checksum_valid = False
            print(" [checksum missing]")
            return

        self.decoded_payload = raw[:-CHECKSUM_LENGTH]
        received_checksum = raw[-CHECKSUM_LENGTH:]
        self.checksum_valid = self._validate_checksum(
            self.decoded_payload, received_checksum)

        expected = self._compute_checksum(self.decoded_payload)
        if self.checksum_valid:
            print(f" [checksum OK: {received_checksum}]")
        else:
            print(
                f" [CHECKSUM FAIL: expected {expected}, got {received_checksum}]"
            )

    def _record_decoded_char(self, char: str):
        with self._decode_lock:
            self.decoded_chars.append(char)

    def _should_check_trailer(self) -> bool:
        with self._decode_lock:
            decoded_len = len(self.decoded_chars)
        if self._expected_total_len is not None:
            return decoded_len >= self._expected_total_len
        return decoded_len >= CHECKSUM_LENGTH + 1

    def _try_detect_trailer(self) -> bool:
        if not self._should_check_trailer():
            return False
        if len(self.audio_buffer) < len(self.trailer):
            return False

        search_limit = len(self.audio_buffer) - len(self.trailer)
        step = max(1, CHUNK_SIZE // 2)
        for offset in range(0, search_limit + 1, step):
            if self._prefix_correlation(
                self.audio_buffer[offset:],
                self.trailer
            ) >= TRAILER_THRESHOLD:
                print("\nTrailer detected!")
                self.decode_state = "idle"
                self.audio_buffer = self.audio_buffer[offset + len(self.trailer):]
                self._finalize_message()
                self.message_complete.set()
                return True
        return False

    def _decode_next_char(self) -> bool:
        if len(self.audio_buffer) < self.buffer_size:
            return False

        with self._decode_lock:
            decoded_len = len(self.decoded_chars)

        if self._expected_total_len is not None and decoded_len >= self._expected_total_len:
            return False

        segment = self.audio_buffer[:self.buffer_size]
        char, score = self.decode_character(segment, charset=self._decode_charset)
        if char:
            self._record_decoded_char(char)
            print(char, end='', flush=True)
            self.audio_buffer = self.audio_buffer[CHAR_STRIDE:]
            return True

        search_limit = min(len(self.audio_buffer) - self.buffer_size, CHAR_STRIDE)
        step = max(1, CHUNK_SIZE // 4)
        best_char = None
        best_score = -1.0
        best_offset = 0

        for offset in range(step, search_limit + 1, step):
            segment = self.audio_buffer[offset:offset + self.buffer_size]
            candidate, candidate_score = self.decode_character(
                segment, charset=self._decode_charset)
            if candidate_score > best_score:
                best_score = candidate_score
                best_char = candidate
                best_offset = offset

        if best_char:
            self._record_decoded_char(best_char)
            print(best_char, end='', flush=True)
            self.audio_buffer = self.audio_buffer[best_offset + CHAR_STRIDE:]
            return True

        return False

    def _process_audio_buffer(self):
        """Process live microphone audio to detect preamble, payload, and trailer."""
        if self.decode_state == "idle":
            if len(self.audio_buffer) < len(self.preamble):
                return

            if self._prefix_correlation(self.audio_buffer, self.preamble) >= PREAMBLE_THRESHOLD:
                print("\nPreamble detected! Starting message decode...")
                self.decode_state = "message"
                self.audio_buffer = self.audio_buffer[len(self.preamble):]
                gap_skip = int(RATE * SECTION_GAP)
                if len(self.audio_buffer) > gap_skip:
                    self.audio_buffer = self.audio_buffer[gap_skip:]
            else:
                self.audio_buffer = self.audio_buffer[CHUNK_SIZE:]
            return

        while True:
            if self._try_detect_trailer():
                return

            with self._decode_lock:
                at_expected_len = (
                    self._expected_total_len is not None
                    and len(self.decoded_chars) >= self._expected_total_len
                )

            if at_expected_len:
                if self._try_detect_trailer():
                    return
                break

            if not self._decode_next_char():
                break

    def feed_audio(self, audio_data: np.ndarray):
        """Feed audio into the decoder as if it arrived on the microphone."""
        for start in range(0, len(audio_data), CHUNK_SIZE):
            chunk = audio_data[start:start + CHUNK_SIZE]
            if len(chunk) < CHUNK_SIZE:
                padded = np.zeros(CHUNK_SIZE, dtype=np.float32)
                padded[:len(chunk)] = chunk
                chunk = padded
            self.audio_buffer = np.append(self.audio_buffer, chunk)

        for _ in range(len(audio_data) // CHUNK_SIZE + 50):
            before = (self.decode_state, len(self.decoded_chars), len(self.audio_buffer))
            self._process_audio_buffer()
            if self.message_complete.is_set():
                break
            after = (self.decode_state, len(self.decoded_chars), len(self.audio_buffer))
            if before == after:
                break

    def _normalize_audio(self, audio_data: np.ndarray) -> np.ndarray:
        audio_data = np.asarray(audio_data, dtype=np.float32)
        peak = np.max(np.abs(audio_data))
        if peak > 1.0:
            audio_data = audio_data / peak
        return audio_data

    def _write_to_stream(self, stream, audio_data: np.ndarray):
        """Write a full audio buffer to an already-open output stream."""
        audio_data = self._normalize_audio(audio_data)
        for start in range(0, len(audio_data), CHUNK_SIZE):
            chunk = audio_data[start:start + CHUNK_SIZE]
            if len(chunk) < CHUNK_SIZE:
                padded = np.zeros(CHUNK_SIZE, dtype=np.float32)
                padded[:len(chunk)] = chunk
                chunk = padded
            stream.write(chunk.tobytes())

    def _append_chars_audio(self, parts: List[np.ndarray], text: str):
        for char in text:
            if char in self.character_map:
                parts.append(self._get_char_audio(char))
                parts.append(self._silence(CHAR_GAP))
            else:
                print(f"Warning: Character '{char}' not supported")

    def _build_payload_audio(self, message: str, include_checksum: bool = True) -> np.ndarray:
        """Build character tones for a payload, with optional checksum suffix."""
        parts: List[np.ndarray] = []
        text = self._signed_payload(message) if include_checksum else message
        self._append_chars_audio(parts, text)
        return np.concatenate(parts) if parts else np.array([], dtype=np.float32)

    def _build_message_audio(self, message: str, include_fake_preamble: bool = True) -> np.ndarray:
        """Concatenate a full transmission into one buffer for fast playback."""
        parts = []
        if include_fake_preamble:
            parts.extend([self.fake_preamble, self._silence(SECTION_GAP)])

        parts.extend([
            self.preamble,
            self._silence(SECTION_GAP),
        ])

        self._append_chars_audio(parts, self._signed_payload(message))
        parts.extend([self._silence(SECTION_GAP), self.trailer])
        return np.concatenate(parts)

    def transmit(self, message: str):
        """Transmit a message while the mic keeps listening (call start_receiving first)."""
        if len(message) > 100:
            print("Error: Message too long (max 100 characters)")
            return

        print("Transmitting...")
        self._queue_playback(self._build_message_audio(message))

        time.sleep(SECTION_GAP)
        print("Sending call sign...")
        for char in "KM3ASS":
            self._send_morse_char(char)

        print("Transmission complete!")

    def _read_output_chunk(self, frame_count: int) -> np.ndarray:
        """Pull the next speaker chunk from the queued play buffer."""
        out_chunk = np.zeros(frame_count, dtype=np.float32)
        with self._play_lock:
            if self._play_buffer is None:
                return out_chunk

            remaining = len(self._play_buffer) - self._play_pos
            if remaining <= 0:
                self._play_buffer = None
                self._play_pos = 0
                self._play_done.set()
                return out_chunk

            samples = min(frame_count, remaining)
            out_chunk[:samples] = self._play_buffer[self._play_pos:self._play_pos + samples]
            self._play_pos += samples
            if self._play_pos >= len(self._play_buffer):
                self._play_buffer = None
                self._play_pos = 0
                self._play_done.set()
        return out_chunk

    def _queue_playback(self, audio_data: np.ndarray, tail_seconds: float = 0.08):
        """Play audio on speakers while the mic keeps running on the same stream."""
        if len(audio_data) == 0:
            return

        if not self.is_receiving:
            self.start_receiving()
            time.sleep(0.1)

        audio_data = self._normalize_audio(audio_data)
        self._play_done.clear()
        with self._play_lock:
            self._play_buffer = audio_data
            self._play_pos = 0

        if not self._play_done.wait(timeout=len(audio_data) / RATE + tail_seconds + 2.0):
            with self._play_lock:
                self._play_buffer = None
                self._play_pos = 0
            raise TimeoutError("Playback did not finish")

        time.sleep(tail_seconds)

    def _play_audio(self, audio_data: np.ndarray):
        """Play audio without an active receiver (used for morse code, etc.)."""
        if len(audio_data) == 0:
            return

        if self.is_receiving and self._duplex_stream is not None:
            self._queue_playback(audio_data)
            return

        stream = self.p.open(format=FORMAT,
                             channels=CHANNELS,
                             rate=RATE,
                             output=True,
                             frames_per_buffer=CHUNK_SIZE)
        try:
            self._write_to_stream(stream, audio_data)
        finally:
            stream.stop_stream()
            stream.close()

    def _decode_loop(self):
        """Process incoming mic audio off the real-time callback thread."""
        while self.is_receiving:
            try:
                chunk = self._incoming_audio.get(timeout=0.1)
            except queue.Empty:
                continue
            self.audio_buffer = np.append(self.audio_buffer, chunk)
            self._process_audio_buffer()

    def start_receiving(self):
        """Start continuous duplex audio — mic always on, speaker plays when queued."""
        if self.is_receiving:
            return

        self.is_receiving = True
        while not self._incoming_audio.empty():
            try:
                self._incoming_audio.get_nowait()
            except queue.Empty:
                break

        def audio_callback(in_data, frame_count, time_info, status):
            if status:
                print(f"Status: {status}")

            self._incoming_audio.put(np.frombuffer(in_data, dtype=np.float32).copy())
            out_chunk = self._read_output_chunk(frame_count)
            return (out_chunk.tobytes(), pyaudio.paContinue)

        self._duplex_stream = self.p.open(format=FORMAT,
                                          channels=CHANNELS,
                                          rate=RATE,
                                          input=True,
                                          output=True,
                                          frames_per_buffer=CHUNK_SIZE,
                                          stream_callback=audio_callback)
        self._duplex_stream.start_stream()
        self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._decode_thread.start()
        print("Receiver listening continuously...")

    def stop_receiving(self):
        """Stop the continuous duplex stream."""
        self.is_receiving = False
        with self._play_lock:
            self._play_buffer = None
            self._play_pos = 0
        self._play_done.set()
        if self._decode_thread is not None:
            self._decode_thread.join(timeout=1.0)
            self._decode_thread = None
        if self._duplex_stream is not None:
            self._duplex_stream.stop_stream()
            self._duplex_stream.close()
            self._duplex_stream = None

    def _receive_loop(self):
        """Block until receiving is stopped."""
        try:
            while self.is_receiving:
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nStopping receive mode...")

    def receive(self):
        """Start continuous receive mode (blocking)."""
        self.reset_receiver()
        self.start_receiving()
        self._receive_loop()
        self.stop_receiving()

    def loopback_test(
        self,
        message: str = "HELLO",
        timeout: float = 20.0
    ) -> bool:
        """
        Real-world simulation: mic listens continuously, full message plays fast,
        decoder pulls characters from the live stream and verifies checksum.
        """
        expected_checksum = self._compute_checksum(message)
        print(f"\nLoopback test: '{message}'")
        print(f"Expected checksum: {expected_checksum}")
        print("Mic listening continuously — playing message at full speed...")
        print("(Tip: place the mic near the speaker and keep the room quiet.)\n")

        self.reset_receiver(expected_payload=message)
        self.start_receiving()
        time.sleep(0.15)  # Let the input stream settle

        try:
            self.decode_state = "message"
            self.audio_buffer = np.array([], dtype=np.float32)
            transmission = np.concatenate([
                self._build_payload_audio(message, include_checksum=True),
                self._silence(SECTION_GAP),
                self.trailer,
            ])
            recording_start = len(self.audio_buffer)
            self._queue_playback(transmission)
            self._flush_decode()

            if self.message_complete.is_set():
                live_payload = self.get_decoded_message()
                live_ok = self.checksum_valid is True
                print(f"\nLive payload: '{live_payload}'")
                print(f"Live checksum: {'OK' if live_ok else 'FAIL'}")
            else:
                live_payload = ""
                live_ok = False
                print("\nLive decode did not reach trailer.")

            recording = self.audio_buffer[recording_start:].copy()
            rec_payload, rec_ok = self._decode_signed_from_recording(recording, message)
            print(f"Recording payload: '{rec_payload}'")
            print(f"Recording checksum: {'OK' if rec_ok else 'FAIL'}")

            passed = sum(
                1 for i, expected in enumerate(message)
                if i < len(rec_payload) and rec_payload[i] == expected
            )
            for i, expected in enumerate(message):
                got = rec_payload[i] if i < len(rec_payload) else "?"
                if expected == got:
                    print(f"  PASS  '{expected}'")
                else:
                    print(f"  FAIL  expected '{expected}', got '{got}'")

            success = passed == len(message) and rec_ok
            print(f"\nLoopback result: {passed}/{len(message)} chars, checksum={'OK' if rec_ok else 'FAIL'}")
            return success
        finally:
            pass

    def close(self):
        """Cleanup PyAudio resources."""
        self.stop_receiving()
        if hasattr(self, 'p'):
            self.p.terminate()

    def __del__(self):
        self.close()

    def _send_morse_char(self, char: str):
        """Send a single character in Morse code using fart tones."""
        if char not in self.morse_map:
            return

        morse = self.morse_map[char]
        for symbol in morse:
            if symbol == '.':
                self._play_audio(self.short_morse)
            else:
                self._play_audio(self.long_morse)
            time.sleep(0.1)
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description="HAM FART Communication System")
    parser.add_argument(
        "--loopback",
        action="store_true",
        help="Run speaker+mic loopback test (default message: HELLO)")
    parser.add_argument(
        "--message",
        default="HELLO",
        help="Message for loopback test")
    args = parser.parse_args()

    ham_fart = HAMFART()

    try:
        if args.loopback:
            success = ham_fart.loopback_test(args.message)
            ham_fart.stop_receiving()
            raise SystemExit(0 if success else 1)

        print("\nHAM FART Communication System")
        print("Mic is always listening — audio from other machines will decode live.")
        print("Press Enter to transmit a message")
        print("Press Ctrl+C to exit")

        ham_fart.reset_receiver()
        ham_fart.start_receiving()
        receive_thread = threading.Thread(target=ham_fart._receive_loop, daemon=True)
        receive_thread.start()

        while True:
            input()
            message = input("Enter message to transmit (max 100 chars): ")
            checksum = ham_fart._compute_checksum(message)
            print(f"Checksum will be: {checksum}")
            ham_fart.reset_receiver(expected_payload=message)
            ham_fart.transmit(message)
            print("\nWaiting for decode...")
            ham_fart.message_complete.wait(timeout=30)
            received = ham_fart.get_decoded_message()
            if ham_fart.checksum_valid:
                print(f"\nReceived: '{received}' (checksum OK)")
            else:
                print(f"\nReceived: '{received}' (CHECKSUM FAILED)")
            print("Press Enter to transmit again")
            print("Press Ctrl+C to exit")

    except KeyboardInterrupt:
        print("\nShutting down...")
        ham_fart.is_receiving = False
    finally:
        ham_fart.close()
        print("Goodbye!")


if __name__ == "__main__":
    main()
