import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import CHAR_GAP, HAMFART, SECTION_GAP


@pytest.fixture(scope="module")
def ham():
    instance = HAMFART()
    yield instance
    instance.close()


def test_audio_assets_load(ham):
    assert len(ham.fart_tones) == 20
    assert len(ham.preamble) > 0
    assert len(ham.trailer) > 0
    assert len(ham.pair_templates) > 0


def test_character_map_matches_readme_samples(ham):
    assert ham.character_map['A'] == (1, 2)
    assert ham.character_map['B'] == (1, 3)
    assert ham.character_map['J'] == (2, 3)
    assert ham.character_map['R'] == (3, 3)
    assert ham.character_map['Z'] == (4, 5)
    assert ham.character_map['a'] == (3, 4)
    assert ham.character_map['0'] == (5, 6)
    assert ham.character_map['9'] == (7, 8)


def test_checksum_roundtrip(ham):
    cases = [
        ("HELLO", "FF"),
        ("HELLO LUCIEN", "AB"),
        ("hello world", "12"),
        ("", "01"),
    ]
    for message, wrong in cases:
        checksum = ham._compute_checksum(message)
        assert len(checksum) == 2
        assert ham._validate_checksum(message, checksum)
        assert not ham._validate_checksum(message, wrong)
        assert not ham._validate_checksum(message + "!", checksum)


def test_mixing_produces_single_burst(ham):
    mixed = ham._mix_tones(1, 2)
    assert len(mixed) == ham.buffer_size
    assert mixed.dtype == np.float32
    assert np.max(np.abs(mixed)) <= 1.0


def test_uppercase_layered_roundtrip(ham):
    charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for char in charset:
        segment = ham._get_char_audio(char)
        decoded, score = ham.decode_character(segment, charset=charset)
        assert decoded == char
        assert score > 0.9


def test_tone_pair_roundtrip_for_all_pairs(ham):
    for pair in ham.pair_templates:
        segment = ham.pair_templates[pair]
        decoded_pair, score = ham.decode_tone_pair(segment)
        assert decoded_pair == pair
        assert score > 0.9


def test_layered_templates_are_order_independent(ham):
    forward = ham._mix_tones(3, 7)
    reverse = ham._mix_tones(7, 3)
    assert ham.decode_tone_pair(forward)[0] == (3, 7)
    assert ham.decode_tone_pair(reverse)[0] == (3, 7)


def test_preamble_prefix_detection(ham):
    score = ham._prefix_correlation(ham.preamble, ham.preamble)
    assert score >= 0.99


def _feed_signed_message(ham, message: str):
    ham.reset_receiver(expected_payload=message)
    ham.decode_state = "message"
    ham.feed_audio(np.concatenate([
        ham._build_payload_audio(message, include_checksum=True),
        ham._silence(SECTION_GAP),
        ham.trailer,
    ]))


def test_continuous_decode_full_message(ham):
    message = "HELLO"
    _feed_signed_message(ham, message)
    assert ham.message_complete.is_set()
    assert ham.checksum_valid is True
    assert ham.get_decoded_message() == message


def test_continuous_decode_long_message(ham):
    message = "HELLO LUCIEN"
    _feed_signed_message(ham, message)
    assert ham.message_complete.is_set()
    assert ham.checksum_valid is True
    assert ham.get_decoded_message() == message


def test_signed_recording_decode(ham):
    message = "HELLO LUCIEN"
    audio = np.concatenate([
        ham._build_payload_audio(message, include_checksum=True),
        ham._silence(SECTION_GAP),
        ham.trailer,
    ])
    payload, valid = ham._decode_signed_from_recording(audio, message)
    assert valid is True
    assert payload == message


def test_fast_gap_message_roundtrip(ham):
    message = "HELLO"
    parts = []
    for char in message:
        parts.append(ham._get_char_audio(char))
        parts.append(ham._silence(CHAR_GAP))

    recording = np.concatenate(parts)
    for char in message:
        best = -1.0
        for start in range(0, len(recording) - ham.buffer_size, 256):
            segment = recording[start:start + ham.buffer_size]
            _, score = ham.decode_tone_pair(segment)
            best = max(best, score)
        assert best >= 0.95


@pytest.mark.skipif(
    os.environ.get("HAM_FART_LIVE_TEST") != "1",
    reason="Set HAM_FART_LIVE_TEST=1 to run speaker+mic loopback test")
def test_live_loopback(ham):
    assert ham.loopback_test("HELLO LUCIEN")
