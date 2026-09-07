from __future__ import annotations

import struct

import pytest

from mommi.byond import ByondError, build_packet, decode_payload, parse_response
from mommi.gameserver import parse_ss13_status


def test_build_packet_adds_leading_question_mark():
    assert build_packet(b"status") == build_packet(b"?status")


def test_build_packet_layout():
    packet = build_packet(b"?status")
    assert packet[:2] == b"\x00\x83"

    assert struct.unpack(">H", packet[2:4])[0] == len(b"?status") + 6
    assert packet[4:9] == b"\x00" * 5
    assert packet[9:] == b"?status\x00"


def test_decode_float_payload():
    payload = b"\x2a" + struct.pack(">f", 12.5)
    assert decode_payload(payload) == pytest.approx(12.5)


def test_decode_string_payload_strips_terminator():
    assert decode_payload(b"\x06players=5\x00") == "players=5"


def test_decode_rejects_unknown_tag():
    with pytest.raises(ByondError):
        decode_payload(b"\xff garbage")


def test_decode_rejects_empty():
    with pytest.raises(ByondError):
        decode_payload(b"")


def test_decode_rejects_truncated_float():
    with pytest.raises(ByondError):
        decode_payload(b"\x2a\x00")


def test_parse_response_urldecodes():

    payload = b"\x06players=2&map_name=Box+Station&station_time=19%3a49\x00"
    parsed = parse_response(payload)
    assert parsed["map_name"] == ["Box Station"]
    assert parsed["station_time"] == ["19:49"]


REAL_STATUS = (
    b"\x06version=veegee&mode=Dynamic+Mode&respawn=0&enter=1&ai=1"
    b"&host=Guest-5486742&players=2&map_name=Box+Station&station_time=19%3a49"
    b"&gamestate=3&active_players=2&player0=Killette2&player1=Saull38"
    b"&admins=0&afk_admins=0\x00"
)


def test_parse_real_vgstation_status():
    snapshot = parse_ss13_status(parse_response(REAL_STATUS))
    assert snapshot.players == 2
    assert snapshot.player_names == ["Killette2", "Saull38"]
    assert snapshot.map_name == "Box Station"
    assert snapshot.station_time == "19:49"
    assert snapshot.gamestate == "in progress"
    assert snapshot.admins == 0
    assert snapshot.afk_admins == 0
    assert snapshot.names_available


def test_player_keys_sort_numerically_not_lexically():
    names = "&".join(f"player{i}=user{i:02d}" for i in range(12))
    payload = b"\x06players=12&" + names.encode() + b"\x00"
    snapshot = parse_ss13_status(parse_response(payload))
    assert snapshot.player_names == [f"user{i:02d}" for i in range(12)]


def test_status_without_player_count_is_an_error():
    with pytest.raises(ValueError):
        parse_ss13_status(parse_response(b"\x06mode=extended\x00"))
