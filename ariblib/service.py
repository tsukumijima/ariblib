"""サービスラッパー"""

from collections.abc import Callable, Iterator

from ariblib.descriptors import (
    LogoTransmissionDescriptor,
    ServiceDescriptor,
)
from ariblib.packet import TransportStreamFile
from ariblib.sections import (
    ActualStreamServiceDescriptionSection,
    OtherStreamServiceDescriptionSection,
    ServiceDescriptionSection,
)
from ariblib.syntax import Syntax


def services(
    ts: TransportStreamFile, channel_id: object = None, single: bool = False, stream: str | None = None
) -> Iterator['Service']:
    """トランスポートストリームから Service オブジェクトを返すジェネレータ"""

    get_channel_id: Callable[[ServiceDescriptionSection], str]
    if channel_id is None:
        get_channel_id = lambda sdt: tsid2channel(sdt.transport_stream_id, sdt.original_network_id)
    else:
        get_channel_id = lambda sdt: str(channel_id)

    SDT: type[ServiceDescriptionSection]
    if stream == 'actual':
        SDT = ActualStreamServiceDescriptionSection
    elif stream == 'other':
        SDT = OtherStreamServiceDescriptionSection
    else:
        SDT = ServiceDescriptionSection

    if single:
        sdt = next(ts.sections(SDT))
        for service in sdt.services:
            yield Service(service, get_channel_id(sdt))
    else:
        for sdt in ts.sections(SDT):
            for service in sdt.services:
                yield Service(service, get_channel_id(sdt))


def parse_tsid(tsid: int) -> tuple[int, int, int, int]:
    # NHK-BS対応
    if 16625 <= tsid <= 16626:
        tsid -= 1
    network_lower_4bit = (tsid & 0xF000) >> 12
    new = (tsid & 0x0E00) >> 9
    repeater = (tsid & 0x01F0) >> 4
    slot = tsid & 0x0007

    return (network_lower_4bit, new, repeater, slot)


def tsid2channel(tsid: int, original_network_id: int | None = None) -> str:
    """transport_stream_id を recpt1 が認識する channel 形式に変える

    Args:
        tsid: transport_stream_id
        original_network_id: original_network_id (省略すると、BS か CS の TS として扱う)

    Returns:
        recpt1 の channel 形式の文字列 (BS15_0・CS02 など)

    Raises:
        ValueError: 地上波など、transport_stream_id から channel を決められないネットワークのとき
    """
    _lower, _new, repeater, slot = parse_tsid(tsid)

    # original_network_id でネットワークを判別する
    # 4 は BS デジタル、6・7 は 110度 CS デジタル
    # 地上波の channel は物理チャンネルで、transport_stream_id からは求められない
    if original_network_id is not None:
        if original_network_id == 4:
            return "BS{:02d}_{}".format(repeater, slot)
        if original_network_id in (6, 7):
            return "CS{:02d}".format(repeater)
        raise ValueError(
            'cannot convert transport_stream_id 0x{:04X} of original_network_id 0x{:04X} '
            'to a recpt1 channel; specify channel_id'.format(tsid, original_network_id)
        )

    # original_network_id がないときは、BS の中継器番号が奇数、CS が偶数であることで判別する
    if repeater % 2 == 0:
        return "CS{:02d}".format(repeater)
    else:
        return "BS{:02d}_{}".format(repeater, slot)


class Service(object):
    """サービスラッパークラス"""

    def __init__(self, service: Syntax, channel_id: str) -> None:
        self.channel_id = channel_id
        if 'BS' in channel_id:
            self.broadcasting_type = 'BS'
            self.channel_number = int(channel_id.split('_')[0].replace('BS', ''))
        elif 'CS' in channel_id:
            self.broadcasting_type = 'CS'
            self.channel_number = int(channel_id.replace('CS', ''))
        else:
            self.broadcasting_type = 'GR'
            self.channel_number = int(channel_id)
        self.service_id = service.service_id
        self.eit_flags = service.EIT_user_defined_flags
        self.eit_schedule = service.EIT_schedule_flag
        self.pseit = service.EIT_present_following_flag
        descs = service.descriptors
        sd = descs[ServiceDescriptor][0]
        self.service_type = sd.service_type
        self.provider = sd.service_provider_name
        self.name = sd.service_name
        self.free_CA_mode = service.free_CA_mode
        self.logo = ''
        for ltd in descs[LogoTransmissionDescriptor]:
            if ltd.logo_transmission_type == 3:
                self.logo = ltd.logo_char
                break
