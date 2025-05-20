# HAM FART (Harry and Morimoto's Flatulence Acoustic Radio Transmission)

Created for the 2025 Georgetown Steam Plant Science Fair 2025 which has a ["Break the System"](https://www.georgetownsteamplant.org/events/georgetown-steam-plant-science-fair-1) theme. 

We're breaking wind; breaking social norms around flatulence; disrupting the local radio waves; and breaking through any attempt at censorship of communication. HAM FART challenges social taboos and censorship by exploring the transmission of messages and data via acoustic flatulence.

A unique approach to digital communication using HAM radios and custom audio tones using small laptops or handheld devices connected to radios. This project demonstrates how to transmit text data using specially crafted audio signals over HAM radio frequencies. In our brave new world where privacy is evaporating and trust in institutions is low, flatulence-based transmission could become the last true **butt**ress of free speech.

## System Overview

- **Transmission Setup**: LAPTOP1 → HAM RADIO 1
- **Reception Setup**: HAM RADIO 2 → LAPTOP2
- **Mode**: Half-duplex (one-way communication at a time)
- **Message Format**: Preamble + Payload + Trailer
- **Audio Format**: 20 unique fart tones, each 0.1 seconds long

## Character Mapping Table

The system uses 20 unique fart tones to encode up to 100 different characters. Each character is represented by a combination of two fart tones. We will attempt to layer them, if possible, using the HAM FART version of DTMF--(Dual-Toot Multi-Frequency).

### Basic Character Set (95 printable ASCII characters)

| Character | Tone Pair | Character | Tone Pair | Character | Tone Pair |
|-----------|-----------|-----------|-----------|-----------|-----------|
| A         | T1-T2     | a         | T3-T4     | 0         | T5-T6     |
| B         | T1-T3     | b         | T3-T5     | 1         | T5-T7     |
| C         | T1-T4     | c         | T3-T6     | 2         | T5-T8     |
| D         | T1-T5     | d         | T3-T7     | 3         | T5-T9     |
| E         | T1-T6     | e         | T3-T8     | 4         | T5-T10    |
| F         | T1-T7     | f         | T3-T9     | 5         | T6-T7     |
| G         | T1-T8     | g         | T3-T10    | 6         | T6-T8     |
| H         | T1-T9     | h         | T4-T5     | 7         | T6-T9     |
| I         | T1-T10    | i         | T4-T6     | 8         | T6-T10    |
| J         | T2-T3     | j         | T4-T7     | 9         | T7-T8     |
| K         | T2-T4     | k         | T4-T8     | Space     | T7-T9     |
| L         | T2-T5     | l         | T4-T9     | Tab       | T7-T10    |
| M         | T2-T6     | m         | T4-T10    | Newline   | T8-T9     |
| N         | T2-T7     | n         | T5-T6     | !         | T8-T10    |
| O         | T2-T8     | o         | T5-T7     | .         | T9-T10    |
| P         | T2-T9     | p         | T5-T8     | ,         | T1-T1     |
| Q         | T2-T10    | q         | T5-T9     | (         | T2-T2     |
| R         | T3-T3     | r         | T5-T10    | )         | T3-T3     |
| S         | T3-T4     | s         | T6-T7     | %         | T4-T4     |
| T         | T3-T5     | t         | T6-T8     | #         | T5-T5     |
| U         | T3-T6     | u         | T6-T9     | ?         | T6-T6     |
| V         | T3-T7     | v         | T6-T10    | +         | T7-T7     |
| W         | T3-T8     | w         | T7-T8     | *         | T8-T8     |
| X         | T3-T9     | x         | T7-T9     | @         | T9-T9     |
| Y         | T3-T10    | y         | T7-T10    | ^         | T10-T10   |
| Z         | T4-T5     | z         | T8-T9     | {         | T1-T2     |

### Special Characters

| Character | Tone Pair | Character | Tone Pair | Character | Tone Pair |
|-----------|-----------|-----------|-----------|-----------|-----------|
| <         | T1-T3     | >         | T2-T4     | /         | T3-T5     |
| '         | T4-T6     | "         | T5-T7     | =         | T6-T8     |
| &         | T7-T9     | ;         | T8-T10    | :         | T1-T4     |
| -         | T2-T5     | _         | T3-T6     | }         | T4-T7     |
| [         | T5-T8     | ]         | T6-T9     |           |           |

## Usage

1. Run `app.py` on both laptops
2. Select mode (Transmit/Receive) on each laptop
3. In Transmit mode:
   - Type your message (up to 100 characters)
   - Press Enter to start transmission
   - System will play Preamble.wav, followed by the encoded message, and end with Trailer.wav
4. In Receive mode:
   - System will listen for incoming transmissions
   - Decodes received audio into text
   - Displays received message

## Requirements

- Python 3.x
- HAM Radio equipment
- Audio input/output capabilities
- Required audio files:
  - Preamble.wav
  - Trailer.wav
  - 20 fart tone .wav files in "Official_Farts" folder

## Note

This project does not work yet and is for educational and entertainment purposes only. Please ensure you have the proper HAM radio licenses and follow all relevant regulations before transmitting. 
