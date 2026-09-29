"""Emitted COSMOS definitions match the golden files generated from the reference dictionary"""

import re
from pathlib import Path

import pytest

from fprime_openc3 import emit

GOLDEN = Path(__file__).parent / "golden"
EMITTERS = {"commands.txt": emit.emit_commands, "channels.txt": emit.emit_channels, "packets.txt": emit.emit_packets}


@pytest.mark.parametrize("filename", sorted(EMITTERS))
def test_golden(reference_dictionary, filename):
    generated = EMITTERS[filename](reference_dictionary)
    expected = (GOLDEN / filename).read_text()
    assert generated == expected, f"{filename} differs from golden; regenerate with tests/update_golden.py"


@pytest.mark.parametrize("filename", sorted(EMITTERS))
def test_only_target_name_erb_remains(reference_dictionary, filename):
    generated = EMITTERS[filename](reference_dictionary)
    assert set(re.findall(r"<%.*?%>", generated)) == {emit.TARGET}


def test_commands_have_descriptor_then_opcode(reference_dictionary):
    command = next(c for c in reference_dictionary.commands if c.get_full_name().endswith("CMD_NO_OP_STRING"))
    lines = emit.emit_command(command, reference_dictionary.layout)
    assert lines[0].startswith("COMMAND <%= target_name %> CdhCore.cmdDisp.CMD_NO_OP_STRING BIG_ENDIAN")
    assert lines[1] == '  APPEND_ID_PARAMETER FPRIME_DESCRIPTOR 16 UINT 0 65535 0 "F Prime packet descriptor"'
    assert (
        lines[3]
        == f'  APPEND_ID_PARAMETER FPRIME_OPCODE 32 UINT 0 4294967295 {command.get_op_code()} "F Prime command opcode"'
    )
    assert '  APPEND_PARAMETER arg1_LENGTH 16 UINT 0 65535 0 "Length in bytes of arg1 (maximum 40)"' in lines
    assert "    VARIABLE_BIT_SIZE arg1_LENGTH 8 0" in lines


def test_channel_subpacket_layout(reference_dictionary):
    channel = next(c for c in reference_dictionary.channels if c.get_name() == "CommandsDispatched")
    lines = emit.emit_channel(channel, reference_dictionary.layout)
    assert lines[1] == "  SUBPACKET"
    assert lines[2] == f'  APPEND_ID_ITEM FPRIME_CHANNEL_ID 32 UINT {channel.get_id()} "F Prime channel identifier"'
    assert '  APPEND_ITEM FPRIME_TIME_BASE 16 UINT "F Prime time base"' in lines
    assert '  APPEND_ITEM FPRIME_TIME_CONTEXT 8 UINT "F Prime time context"' in lines
    assert lines[-2] == '  APPEND_ITEM CommandsDispatched 32 UINT "Number of commands dispatched"'


def test_parent_packet_uses_subpacketizer(reference_dictionary):
    lines = emit.emit_channelized_parent(reference_dictionary.layout)
    assert lines[1] == "  SUBPACKETIZER fprime_subpacketizer.py"
    assert '  APPEND_ID_ITEM FPRIME_DESCRIPTOR 16 UINT 1 "F Prime packet descriptor"' in lines
    assert lines[-2:] == ['  APPEND_ITEM CHANNELS 0 BLOCK "Concatenated channel records"', "    HIDDEN"]


def test_packetized_packet_ids(reference_dictionary):
    packet = reference_dictionary.packets[0]
    lines = emit.emit_packet(packet, reference_dictionary.layout)
    assert '  APPEND_ID_ITEM FPRIME_DESCRIPTOR 16 UINT 4 "F Prime packet descriptor"' in lines
    assert f'  APPEND_ID_ITEM FPRIME_PACKET_ID 16 UINT {packet.get_id()} "F Prime packet identifier"' in lines


@pytest.mark.parametrize(
    ("python", "printf"),
    [
        ("{}", None),
        (None, None),
        ("{:d}", "%d"),
        ("{:.3f} V", "%.3f V"),
        ("{:08x}", "%08x"),
        ("{:>10}", None),
        ("{:.2e}", "%.2e"),
    ],
)
def test_printf_format(python, printf):
    assert emit.printf_format(python) == printf


def test_quote_flattens_whitespace_and_quotes():
    assert emit.quote(' say "hi"\n there ') == "\"say 'hi' there\""
    assert emit.quote(None) == '""'
