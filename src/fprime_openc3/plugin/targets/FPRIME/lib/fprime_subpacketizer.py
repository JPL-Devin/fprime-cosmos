"""Splits an F Prime channelized telemetry packet into one COSMOS subpacket per channel

F Prime's TlmChan component concatenates channel records (id, time, value) into a single
downlink packet. Each channel is declared as a COSMOS SUBPACKET keyed on the channel id, so
this subpacketizer identifies records one at a time from the CHANNELS block and emits them
alongside the parent packet.
"""

from openc3.subpacketizers.subpacketizer import Subpacketizer
from openc3.system.system import System


class FprimeSubpacketizer(Subpacketizer):
    def call(self, packet):
        packets = []
        channels = packet.read("CHANNELS")
        while channels:
            subpacket = System.telemetry.identify(channels, target_names=[packet.target_name], subpackets=True)
            if subpacket is None:
                break
            subpacket.buffer = channels[: subpacket.defined_length]
            subpacket = subpacket.clone()
            subpacket.received_time = packet.received_time
            packets.append(subpacket)
            channels = channels[subpacket.defined_length :]
        packets.append(packet)
        return packets
