import os
import time
import wave
import pyaudio
import numpy as np
from typing import Dict, Tuple, List
import threading
import queue

# Audio configuration
CHUNK_SIZE = 1024
FORMAT = pyaudio.paFloat32
CHANNELS = 1
RATE = 44100
TONE_DURATION = 0.1  # Duration of each fart tone in seconds


class HAMFART:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.audio_queue = queue.Queue()
        self.is_receiving = False
        self.character_map = self._create_character_map()
        self.reverse_map = {v: k for k, v in self.character_map.items()}

        # Load audio files
        self.fake_preamble = self._load_audio_file(
            "Official_Farts/fake_preamble.wav")
        self.preamble = self._load_audio_file("Preamble.wav")
        self.trailer = self._load_audio_file("Trailer.wav")
        self.fart_tones = self._load_fart_tones()

        # Load Morse code audio files
        self.short_morse = self._load_audio_file(
            "Official_Farts/short_morse.wav")
        self.long_morse = self._load_audio_file(
            "Official_Farts/long_morse.wav")

        # Audio processing parameters
        # Size of buffer to analyze
        self.buffer_size = int(RATE * TONE_DURATION)
        self.correlation_threshold = 0.7  # Threshold for tone detection
        self.audio_buffer = np.array([], dtype=np.float32)

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

    def _create_character_map(self) -> Dict[str, Tuple[int, int]]:
        """Create mapping between characters and tone pairs."""
        # This matches the mapping in README.md
        mapping = {}

        # Uppercase letters (A-Z)
        for i, char in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
            tone1 = (i // 10) + 1
            tone2 = (i % 10) + 1
            mapping[char] = (tone1, tone2)

        # Lowercase letters (a-z)
        for i, char in enumerate('abcdefghijklmnopqrstuvwxyz'):
            tone1 = (i // 10) + 3
            tone2 = (i % 10) + 4
            mapping[char] = (tone1, tone2)

        # Numbers (0-9)
        for i, char in enumerate('0123456789'):
            tone1 = (i // 5) + 5
            tone2 = (i % 5) + 6
            mapping[char] = (tone1, tone2)

        # Special characters
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

    def _load_audio_file(self, filename: str) -> np.ndarray:
        """Load a WAV file and return its audio data."""
        try:
            with wave.open(filename, 'rb') as wf:
                # Get the audio parameters
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                n_frames = wf.getnframes()

                # Read the audio data
                audio_data = wf.readframes(n_frames)

                # Convert to numpy array with proper dtype
                if sample_width == 2:  # 16-bit
                    dtype = np.int16
                elif sample_width == 4:  # 32-bit
                    dtype = np.int32
                else:
                    dtype = np.int8

                # Convert to numpy array
                audio_array = np.frombuffer(audio_data, dtype=dtype)

                # Convert to float32 and normalize to [-1, 1]
                audio_array = audio_array.astype(np.float32)
                if dtype != np.float32:
                    audio_array = audio_array / np.iinfo(dtype).max

                return audio_array
        except FileNotFoundError:
            print(f"Warning: {filename} not found")
            return np.array([], dtype=np.float32)

    def _load_fart_tones(self) -> List[np.ndarray]:
        """Load all fart tone WAV files from the Official_Farts folder."""
        tones = []
        for i in range(1, 21):  # 20 fart tones
            filename = f"Official_Farts/{i}_mod.wav"
            tone = self._load_audio_file(filename)
            if len(tone) > 0:
                tones.append(tone)
            else:
                print(f"Warning: Could not load {filename}")
        return tones

    def _correlate_audio(self, audio1: np.ndarray, audio2: np.ndarray) -> float:
        """Calculate correlation between two audio signals."""
        # Check for zero signals
        if len(audio1) == 0 or len(audio2) == 0:
            return 0.0

        # Check if signals are silent
        max1 = np.max(np.abs(audio1))
        max2 = np.max(np.abs(audio2))
        if max1 == 0 or max2 == 0:
            return 0.0

        # Normalize both signals
        audio1 = audio1 / max1
        audio2 = audio2 / max2

        # Calculate correlation
        correlation = np.correlate(audio1, audio2, mode='valid')
        return np.max(correlation) / len(audio1)

    def _identify_tone(self, audio_segment: np.ndarray) -> int:
        """Identify which fart tone matches the audio segment."""
        best_match = -1
        best_correlation = 0

        for i, tone in enumerate(self.fart_tones):
            correlation = self._correlate_audio(audio_segment, tone)
            if correlation > best_correlation and correlation > self.correlation_threshold:
                best_correlation = correlation
                best_match = i + 1  # Convert to 1-based index

        return best_match

    def _process_audio_buffer(self):
        """Process the audio buffer to detect and decode tones."""
        if len(self.audio_buffer) < self.buffer_size:
            return

        # Check for preamble
        preamble_correlation = self._correlate_audio(
            self.audio_buffer[:len(self.preamble)],
            self.preamble
        )

        if preamble_correlation > self.correlation_threshold:
            print("\nPreamble detected! Starting message decode...")
            # Skip past the preamble
            self.audio_buffer = self.audio_buffer[len(self.preamble):]
            return

        # Check for trailer
        trailer_correlation = self._correlate_audio(
            self.audio_buffer[:len(self.trailer)],
            self.trailer
        )

        if trailer_correlation > self.correlation_threshold:
            print("\nTrailer detected! Message complete.")
            # Skip past the trailer
            self.audio_buffer = self.audio_buffer[len(self.trailer):]
            return

        # Process tone pairs
        if len(self.audio_buffer) >= self.buffer_size * 2:
            # Get two consecutive tone segments
            tone1_segment = self.audio_buffer[:self.buffer_size]
            tone2_segment = self.audio_buffer[self.buffer_size:self.buffer_size*2]

            # Identify the tones
            tone1 = self._identify_tone(tone1_segment)
            tone2 = self._identify_tone(tone2_segment)

            if tone1 > 0 and tone2 > 0:
                # Look up the character
                tone_pair = (tone1, tone2)
                if tone_pair in self.reverse_map:
                    char = self.reverse_map[tone_pair]
                    print(char, end='', flush=True)

            # Remove processed tones from buffer
            self.audio_buffer = self.audio_buffer[self.buffer_size*2:]

    def transmit(self, message: str):
        """Transmit a message using fart tones."""
        if len(message) > 100:
            print("Error: Message too long (max 100 characters)")
            return

        print("Transmitting...")

        # Play fake preamble to trigger VOX
        self._play_audio(self.fake_preamble)
        time.sleep(0.5)  # Small pause after fake preamble

        # Play actual preamble
        self._play_audio(self.preamble)
        time.sleep(0.5)  # Small pause after preamble

        # Encode and play each character
        for char in message:
            if char in self.character_map:
                tone1, tone2 = self.character_map[char]
                self._play_audio(self.fart_tones[tone1 - 1])
                time.sleep(0.05)  # Small pause between tones
                self._play_audio(self.fart_tones[tone2 - 1])
                time.sleep(0.05)  # Small pause between characters
            else:
                print(f"Warning: Character '{char}' not supported")

        # Play trailer
        time.sleep(0.5)  # Small pause before trailer
        self._play_audio(self.trailer)

        # Send call sign in Morse code
        time.sleep(0.5)  # Pause before call sign
        print("Sending call sign...")
        for char in "KM3ASS":
            self._send_morse_char(char)

        print("Transmission complete!")

    def _play_audio(self, audio_data: np.ndarray):
        """Play audio data through the default output device."""
        if len(audio_data) == 0:
            return

        # Ensure audio data is float32 and in the correct range
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        # Normalize if needed
        if np.max(np.abs(audio_data)) > 1.0:
            audio_data = audio_data / np.max(np.abs(audio_data))

        stream = self.p.open(format=FORMAT,
                             channels=CHANNELS,
                             rate=RATE,
                             output=True)

        # Convert to bytes and ensure proper buffer size
        audio_bytes = audio_data.tobytes()
        stream.write(audio_bytes)
        stream.stop_stream()
        stream.close()

    def receive(self):
        """Start receiving mode."""
        self.is_receiving = True
        print("Entering receive mode...")

        def audio_callback(in_data, frame_count, time_info, status):
            if status:
                print(f"Status: {status}")

            # Convert incoming audio to numpy array
            audio_data = np.frombuffer(in_data, dtype=np.float32)

            # Add to buffer
            self.audio_buffer = np.append(self.audio_buffer, audio_data)

            # Process buffer
            self._process_audio_buffer()

            return (in_data, pyaudio.paContinue)

        stream = self.p.open(format=FORMAT,
                             channels=CHANNELS,
                             rate=RATE,
                             input=True,
                             frames_per_buffer=CHUNK_SIZE,
                             stream_callback=audio_callback)

        stream.start_stream()

        try:
            while self.is_receiving:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopping receive mode...")
        finally:
            stream.stop_stream()
            stream.close()
            self.is_receiving = False

    def __del__(self):
        """Cleanup PyAudio resources."""
        self.p.terminate()

    def _send_morse_char(self, char: str):
        """Send a single character in Morse code using fart tones."""
        if char not in self.morse_map:
            return

        morse = self.morse_map[char]
        for symbol in morse:
            if symbol == '.':
                self._play_audio(self.short_morse)
            else:  # symbol == '-'
                self._play_audio(self.long_morse)
            time.sleep(0.1)  # Pause between symbols
        time.sleep(0.2)  # Pause between characters


def main():
    ham_fart = HAMFART()

    print("\nHAM FART Communication System")
    print("Starting in receive mode...")
    print("Press Enter to transmit a message")
    print("Press Ctrl+C to exit")

    # Start receive mode in a separate thread
    receive_thread = threading.Thread(target=ham_fart.receive)
    receive_thread.daemon = True  # Thread will exit when main program exits
    receive_thread.start()

    try:
        while True:
            # Wait for Enter key to transmit
            input()
            message = input("Enter message to transmit (max 100 chars): ")
            ham_fart.transmit(message)
            print("\nBack to receive mode...")
            print("Press Enter to transmit a message")
            print("Press Ctrl+C to exit")

    except KeyboardInterrupt:
        print("\nShutting down...")
        ham_fart.is_receiving = False
        receive_thread.join(timeout=1.0)  # Wait for receive thread to finish
        print("Goodbye!")


if __name__ == "__main__":
    main()
