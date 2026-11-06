from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from datetime import timedelta
from io import BufferedReader, BytesIO, FileIO, RawIOBase
from itertools import chain
from typing import TYPE_CHECKING, TypeAlias, TypeVar
import zlib

from ariblib.mnemonics import (
    bslbf,
    case,
    char,
    loop,
    otm,
    raw,
    times,
    uimsbf,
)
from ariblib.sections import Section
from ariblib.syntax import Syntax


if TYPE_CHECKING:
    from _typeshed import StrOrBytesPath

    # on() で設定するコールバック関数
    SectionCallback: TypeAlias = 'Callable[[Section], object]'

# sections() が返すセクションのクラス
_SectionT = TypeVar('_SectionT', bound=Section)

# MPEG-2 の CRC を zlib.crc32() で計算するために、1バイトごとにビットを反転させる変換表
BIT_REVERSE_TABLE = bytes(int(format(i, '08b')[::-1], 2) for i in range(256))

"""
パケット層の定義

188バイトずつの各パケットを class として実装するとかなり遅くなるので、
パケットについては bytearray のまま受け渡して、関数で適宜対応することにした。
"""


class TransportStreamFile(BufferedReader):

    """TSファイル"""

    PACKET_SIZE = 188

    def __init__(self, path: StrOrBytesPath | int | RawIOBase | BytesIO, chunk_size: int = 10000) -> None:
        # ファイルのパスかファイルディスクリプタのほか、メモリ上の TS (BytesIO など) のようなバイナリのストリームも読み込める
        if isinstance(path, (RawIOBase, BytesIO)):
            BufferedReader.__init__(self, path)
        else:
            BufferedReader.__init__(self, FileIO(path))
        self.chunk_size = chunk_size
        self._callbacks: dict[type[Section], SectionCallback] = dict()

    def __iter__(self) -> Iterator[bytes]:
        packet_size = self.PACKET_SIZE
        chunk_size = self.chunk_size
        buffer_size = packet_size * chunk_size
        packets = iter(lambda: self.read(buffer_size), b'')
        for packet in packets:
            for start, stop in zip(
                range(0, buffer_size - packet_size + 1, packet_size),
                range(packet_size, buffer_size + 1, packet_size)
            ):
                next = packet[start:stop]
                # ファイルの終端で途中で切れたパケットは、不完全なので読み飛ばす
                if len(next) < packet_size:
                    return
                yield next

    def __next__(self) -> bytes:
        return self.read(self.PACKET_SIZE)

    def on(self, Section: type[Section]) -> Callable[[SectionCallback], None]:
        """セクションごとにコールバック関数を設定する
        いまのところ、一つのセクションについてコールバック関数は1つのみ定義できる。
        """

        def attach_callback(callback: SectionCallback) -> None:
            self._callbacks[Section] = callback
        return attach_callback

    def execute(self) -> None:
        """指定されたセクションがyieldされるごとにコールバック関数を実行する"""

        for section in self.sections(*self._callbacks.keys()):
            self._callbacks[type(section)](section)

    def sections(self, *Sections: type[_SectionT], pids: Iterable[int] | None = None) -> Iterator[_SectionT]:
        """パケットストリームから指定のセクションを返す

        Args:
            *Sections: 取り出すセクションのクラス
            pids: セクションを取り出す PID の一覧。PMT のように PID が PAT で決まるセクションに指定する
                (省略すると、各セクションのクラスの _pids を使う)

        Returns:
            Iterator: セクションのイテレータ。各セクションの pcr には、そのセクションの先頭を含むパケットの時点で
                最後に現れた PCR (PID を問わない。90kHz 単位の program_clock_reference_base) が入る
        """

        buf: defaultdict[int, bytearray] = defaultdict(bytearray)
        # PID ごとの直前の continuity_counter
        last_counters: dict[int, int] = dict()
        # 直前までに現れた PCR と、PID ごとの組み立て中のセクションの先頭を含むパケットの時点の PCR
        last_pcr: int | None = None
        start_pcrs: dict[int, int | None] = dict()

        # pids を指定したときは、各セクションのクラスの _pids の代わりにそれを使う
        # pids はジェネレータ (PAT の pmt_pids など) のこともあるため、最初に1回だけリストにする
        given_pids = list(pids) if pids is not None else None
        pids_of_sections: dict[type[_SectionT], list[int | None]] = dict()
        for SectionClass in Sections:
            if given_pids is not None:
                pids_of_sections[SectionClass] = list(given_pids)
            else:
                pids_of_sections[SectionClass] = list(SectionClass._pids)
        target_pids = set(chain.from_iterable(pids_of_sections.values()))
        target_ids: dict[tuple[int | None, int], type[_SectionT]] = dict()
        # CRC_32 を持つセクションは、CRC が正しいものだけを返す
        crc_sections: set[type[_SectionT]] = set()
        for SectionClass in Sections:
            if any('CRC_32' in vars(cls) for cls in SectionClass.__mro__):
                crc_sections.add(SectionClass)
            for PID in pids_of_sections[SectionClass]:
                for table_id in SectionClass._table_ids:
                    target_ids[(PID, table_id)] = SectionClass

        def extract(PID: int, buffer: bytearray) -> Iterator[_SectionT]:
            """バッファの先頭から、完成しているセクションを順に取り出して返す"""

            while buffer:
                # スタッフィングバイトが現れたら、その後ろにセクションはない
                if buffer[0] == 0xFF:
                    buffer.clear()
                    return
                if buffer[0:3] == b'\x00\x00\x01':
                    # PES の全長は PES_packet_length + 6 (0 は長さ不定を表す)
                    if len(buffer) < 6:
                        return
                    length = ((buffer[4] << 8) | buffer[5]) + 6
                    if length == 6:
                        return
                else:
                    # セクションの全長は section_length + 3
                    if len(buffer) < 3:
                        return
                    length = (((buffer[1] & 0x0F) << 8) | buffer[2]) + 3
                if len(buffer) < length:
                    return
                unit = buffer[:length]
                del buffer[:length]

                Section = target_ids.get((PID, unit[0]))
                if Section is None:
                    continue
                # CRC_32 まで含めて計算した MPEG-2 の CRC は、正しければ 0 になる
                # zlib.crc32() は反転した多項式の CRC なので、ビットを反転した入力の結果が 0xFFFFFFFF になるかで判定する
                if Section in crc_sections and\
                        zlib.crc32(unit.translate(BIT_REVERSE_TABLE)) != 0xFFFFFFFF:
                    continue
                section = Section(unit)
                section._pid = PID
                section.pcr = start_pcrs.get(PID)
                yield section

        for packet in self:
            # PCR は取り出す対象ではない PID (PCR_PID) で運ばれるため、PID で絞り込む前に記録する
            # PCR_flag が立っていて、adaptation_field が PCR (6バイト) を含む長さを持っているときだけ
            if has_adaptation(packet) and packet[4] >= 7 and packet[5] & 0x10:
                last_pcr = ((packet[6] << 25) | (packet[7] << 17) |
                            (packet[8] << 9) | (packet[9] << 1) |
                            ((packet[10] & 0x80) >> 7))

            PID = pid(packet)
            if PID not in target_pids:
                continue

            buffer = buf[PID]

            # 誤りを含むパケットは、そのパケットを捨てて組み立て中のデータも捨てる
            if transport_error_indicator(packet):
                buffer.clear()
                continue

            # continuity_counter が連続していなければパケットが欠落しているので、組み立て中のデータを捨てる
            # 同じ値が続くパケットは重複して送られたパケットなので無視する (ISO/IEC 13818-1 2.4.3.3)
            # discontinuity_indicator が立っているパケットの不連続は意図されたものなので、データを捨てない
            if has_payload(packet):
                counter = continuity_counter(packet)
                last_counter = last_counters.get(PID)
                last_counters[PID] = counter
                if last_counter is not None:
                    if counter == last_counter:
                        continue
                    discontinuity =\
                        has_adaptation(packet) and packet[4] > 0 and\
                        packet[5] & 0x80
                    if counter != (last_counter + 1) & 0x0F and\
                            not discontinuity:
                        buffer.clear()

            prev, current = payload(packet)
            if payload_unit_start_indicator(packet):
                # 前のセクションの続きを足して、完成していれば返す
                # 完成していないデータは捨てて、新しいセクションの組み立てを始める
                if buffer:
                    buffer.extend(prev)
                    yield from extract(PID, buffer)
                buffer[:] = current
                start_pcrs[PID] = last_pcr
            elif not buffer:
                continue
            else:
                buffer.extend(current)

            # このパケットで完成したセクションを返す
            yield from extract(PID, buffer)

    tables = sections

    def get_caption_pid(self) -> int | None:
        """字幕パケットの PID を返す

        FIXME: 2か国語対応の場合複数の PID で字幕が提供されているかも? (未確認)
        """

        from ariblib.sections import (
            ProgramAssociationSection,
            ProgramMapSection,
        )
        from ariblib.descriptors import StreamIdentifierDescriptor

        pat = next(self.sections(ProgramAssociationSection))
        for pmt in self.sections(ProgramMapSection, pids=pat.pmt_pids):
            for tsmap in pmt.maps:
                if tsmap.stream_type != 0x06:
                    continue
                for si in tsmap.descriptors.get(StreamIdentifierDescriptor,
                                                []):
                    if si.component_tag == 0x87:
                        return tsmap.elementary_PID

    def get_video_pid(self, video_encode_format: int) -> int | None:
        """指定のエンコードフォーマットの動画PIDを返す"""

        from ariblib.sections import (
            ProgramAssociationSection,
            ProgramMapSection,
        )
        from ariblib.descriptors import VideoDecodeControlDescriptor

        pat = next(self.sections(ProgramAssociationSection))
        for pmt in self.sections(ProgramMapSection, pids=pat.pmt_pids):
            for tsmap in pmt.maps:
                for vdc in tsmap.descriptors.get(VideoDecodeControlDescriptor,
                                                 []):
                    if vdc.video_encode_format == video_encode_format:
                        return tsmap.elementary_PID

    def pcrs(self) -> Iterator[timedelta]:
        """adaptation filed にある PCR から求めた timedelta オブジェクトを返す"""

        for packet in self:
            # PCR_flag が立っていて、adaptation_field が PCR (6バイト) を含む長さを持っているときだけ
            if has_adaptation(packet) and packet[4] >= 7 and\
                    packet[5] & 0x10:
                pcr = ((packet[6] << 25) | (packet[7] << 17) |
                       (packet[8] << 9) | (packet[9] << 1) |
                       ((packet[10] & 0x80) >> 7))
                yield timedelta(seconds=pcr / 90000)


def tsopen(path: StrOrBytesPath | int | RawIOBase | BytesIO, chunk: int = 10000) -> TransportStreamFile:
    """TransportStreamFileオブジェクトを返すラッパー関数"""
    return TransportStreamFile(path, chunk)


def transport_error_indicator(packet: bytes | bytearray) -> int:
    """パケットの transport_error_indocator を返す"""
    return (packet[1] & 0x80) >> 7


def payload_unit_start_indicator(packet: bytes | bytearray) -> int:
    """パケットの payload_unit_start_indicator を返す"""
    return (packet[1] & 0x40) >> 6


def transport_priority(packet: bytes | bytearray) -> int:
    """パケットの transport_priority を返す"""
    return (packet[1] & 0x20) >> 5


def pid(packet: bytes | bytearray) -> int:
    """パケットの pid を返す"""
    return ((packet[1] & 0x1F) << 8) | packet[2]


def transport_scrambling_control(packet: bytes | bytearray) -> int:
    return (packet[3] & 0xC0) >> 6


def has_adaptation(packet: bytes | bytearray) -> int:
    """このパケットが adaptation field を持っているかどうかを返す"""
    return (packet[3] & 0x20) >> 5


def has_payload(packet: bytes | bytearray) -> int:
    """このパケットが payload を持っているかどうかを返す"""
    return (packet[3] & 0x10) >> 4


def continuity_counter(packet: bytes | bytearray) -> int:
    """このパケットの continuity_counter を返す"""
    return packet[3] & 0x0F


def adaptation_field(packet: bytes | bytearray) -> AdaptationField | bytes:
    """パケットから adaptaton field 部分を返す"""

    if not has_adaptation(packet):
        return b''

    start = 4
    adaptation_length = packet[start]
    end = start + adaptation_length + 1
    return AdaptationField(packet[start:end])


def payload(packet: bytes | bytearray) -> tuple[bytes | bytearray, bytes | bytearray]:
    """パケットから payload 部分を返す

    args:
    packet - payloadを求めたい TS パケット

    return:
    (前のパケットの payload の続き, このパケットの payload) の tuple
    """

    if not has_payload(packet):
        return (b'', b'')

    start = 4

    if has_adaptation(packet):
        adaptation_length = packet[start]
        start += 1 + adaptation_length

    if not payload_unit_start_indicator(packet):
        return (b'', packet[start:])

    packet_start_code_prefix = packet[start:start + 3]
    if packet_start_code_prefix == b'\x00\x00\x01':
        return (b'', packet[start:])

    pointer = packet[start]
    prev_start = start + 1
    start += 1 + pointer
    return (packet[prev_start:start], packet[start:])


class AdaptationField(Syntax):

    """ISO/IEC 13818-1 2.4.3.5"""

    adaptation_field_length = uimsbf(8)
    discontinuity_indicator = bslbf(1)
    random_access_indicator = bslbf(1)
    elementary_stream_priority_indicator = bslbf(1)
    PCR_flag = bslbf(1)
    OPCR_flag = bslbf(1)
    splicing_point_flag = bslbf(1)
    transport_private_data_flag = bslbf(1)
    adaptation_field_extension_flag = bslbf(1)

    @case(PCR_flag)
    class with_PCR(Syntax):
        program_clock_reference_base = uimsbf(33)
        reserved_1 = bslbf(6)
        program_clock_reference_extension = uimsbf(9)

    @case(OPCR_flag)
    class with_OPCR(Syntax):
        original_program_clock_reference_base = uimsbf(33)
        reserved_2 = bslbf(6)
        original_program_clock_reference_extension = uimsbf(9)

    @case(splicing_point_flag)
    class with_splicing(Syntax):
        splice_countdown = uimsbf(8)  # tcimsbf

    @case(transport_private_data_flag)
    class with_transport_private_data(Syntax):
        transport_private_data_length = uimsbf(8)
        private_data_byte = bslbf(transport_private_data_length)


class SynchronizedPacketizedElementaryStream(Section):

    """ISO/IEC 13818-1 2.4.3.7

    FIXME: 字幕 PES パケット専用の実装になっている
    字幕部分の仕様は ARIB STD-B24-1-3-9
    """

    packet_start_code_prefix = bslbf(24)
    stream_id = uimsbf(8)
    PES_packet_length = uimsbf(16)
    should_be_10 = bslbf(2)
    PES_scrambling_control = bslbf(2)
    PES_priority = bslbf(1)
    data_alignment_indicator = bslbf(1)
    copyright = bslbf(1)
    original_or_copy = bslbf(1)
    PTS_DTS_flags = bslbf(2)
    ESCR_flag = bslbf(1)
    ES_rate_flag = bslbf(1)
    DSM_trick_mode_flag = bslbf(1)
    additional_copy_into_flag = bslbf(1)
    PES_CRC_flag = bslbf(1)
    PES_extension_flag = bslbf(1)
    PES_header_data_length = uimsbf(8)
    should_be_0010 = bslbf(4)
    PTS_1 = uimsbf(3)
    marker_bit_1 = bslbf(1)
    PTS_2 = uimsbf(15)
    marler_bit_2 = bslbf(1)
    PTS_3 = uimsbf(15)
    marker_bit_3 = bslbf(1)
    PES_private_data_flag = bslbf(1)
    pack_header_field_flag = bslbf(1)
    program_packet_sequence_counter_flag = bslbf(1)
    P_STD_buffer_flag = bslbf(1)
    reserved4 = bslbf(3)
    PES_extension_flag_2 = bslbf(1)
    PES_private_data = bslbf(128)
    stuffing_byte = bslbf(lambda self: self.PES_header_data_length - 22)
    data_identifier = uimsbf(8)
    private_stream_id = uimsbf(8)
    reserved_future_use = bslbf(4)
    PES_data_packet_header_length = uimsbf(4)
    PES_data_private_data_byte = bslbf(PES_data_packet_header_length)
    data_group_id = uimsbf(6)
    data_group_version = bslbf(2)
    data_group_link_number = uimsbf(8)
    last_data_group_link_number = uimsbf(8)
    data_group_size = uimsbf(16)

    @case(lambda self: self.data_group_id in (0x0, 0x20))
    class with_languages(Syntax):
        """ARIB-STD-B24-1-3-9.3.1 表9-3 字幕管理データ"""

        TMD = bslbf(2)
        reserved10 = bslbf(6)

        @case(lambda self: self.TMD == 0b10)
        class with_OTM(Syntax):
            OTM = otm(40)

        num_languages = uimsbf(8)

        @times(num_languages)
        class languages(Syntax):
            language_tag = bslbf(3)
            reserved11 = bslbf(1)
            DMF1 = bslbf(2)
            DMF2 = bslbf(2)

            @case(lambda self: self.DMF1 == 0b11)
            class with_DC(Syntax):
                DC = bslbf(8)

            ISO_639_language_code = char(24)
            format = bslbf(4)
            TCS = bslbf(2)
            rollup_mode = bslbf(2)

    @case(lambda self: self.data_group_id not in (0x0, 0x20))
    class without_languages(Syntax):
        """ARIB-STD-B24-1-3-9.3.2 表9-10 字幕文データ"""

        TMD = bslbf(2)
        reserved10 = bslbf(6)

        @case(lambda self: self.TMD in (0b01, 0b10))
        class with_STM(Syntax):
            STM = otm(40)

    data_unit_loop_length = uimsbf(24)

    @loop(data_unit_loop_length)
    class data_units(Syntax):
        unit_separator = uimsbf(8)
        data_unit_parameter = uimsbf(8)

        @case(lambda self: self.data_unit_parameter == 0x20)
        class CProfileString(Syntax):
            data_unit_size = uimsbf(24)
            data_unit_data = raw(data_unit_size)

        @case(lambda self: self.data_unit_parameter == 0x30)
        class DRCSString(Syntax):
            """ARIB-STD-B24-1-2-D 表D-1 DRCS_data_structure

            FIXME: width が16固定"""

            data_unit_size = uimsbf(24)
            number_of_code = uimsbf(8)

            @times(number_of_code)
            class codes(Syntax):
                character_code = uimsbf(16)
                number_of_font = uimsbf(8)

                @times(number_of_font)
                class fonts(Syntax):
                    font_id = uimsbf(4)
                    mode = bslbf(4)
                    depth = uimsbf(8)
                    width = uimsbf(8)
                    height = uimsbf(8)

                    @times(height)
                    class patterns(Syntax):
                        pattern_data = raw(16)

        @case(lambda self: self.data_unit_parameter not in (0x20, 0x30))
        class other_unit(Syntax):
            """文字・DRCS 以外のデータユニットは、data_unit_size の分だけ読み飛ばす"""

            data_unit_size = uimsbf(24)
            data_unit_data = raw(data_unit_size)

    @property
    def pts(self) -> timedelta:
        pts = (self.PTS_1 << 30) | (self.PTS_2 << 15) | self.PTS_3
        pts_hz = 90000
        second = pts / pts_hz
        return timedelta(seconds=second)

    def isfull(self) -> bool:
        """section_length などで指定された分以上の
        パケットを持っているかどうかを返す"""

        return self.PES_packet_length + 6 <= len(self)


def raw_dump(packet: Iterable[int]) -> str:
    return ' '.join(map(lambda s: format(s, '02X'), packet))
