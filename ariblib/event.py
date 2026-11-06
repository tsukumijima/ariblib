#!/usr/bin/env python3

"""イベントラッパー"""

from collections.abc import Iterator
from datetime import datetime, timedelta

from ariblib.aribstr import AribString
from ariblib.constants import (
    COMPONENT_TYPE,
    CONTENT_TYPE,
    DIGITAL_RECORDING_CONTROL_TYPE,
    EVENT_GROUP_TYPE,
    SAMPLING_RATE,
    USER_TYPE,
)
from ariblib.descriptors import (
    AudioComponentDescriptor,
    ComponentDescriptor,
    ContentDescriptor,
    DigitalCopyControlDescriptor,
    EventGroupDescriptor,
    ExtendedEventDescriptor,
    ShortEventDescriptor,
)
from ariblib.packet import TransportStreamFile
from ariblib.sections import ActualStreamEventInformationSection, EventInformationSection
from ariblib.syntax import Syntax


def component_type_string(stream_content: int, component_type: int) -> str:
    """コンポーネント内容とコンポーネント種別に対応する記述を返す。表にない値は '未定義' になる"""

    table = COMPONENT_TYPE.get(stream_content, {})
    if component_type in table:
        return table[component_type]
    # 音声の component_type は、下位5ビットが音声モード、上位3ビットがダイアログ制御と障がい者用音声の有無を表す
    # 表にない組み合わせは、音声モードだけで記述を引く (ARIB STD-B10 第2部 6.2.26 表6-43)
    if stream_content in (0x02, 0x03, 0x04):
        return table.get(component_type & 0x1F, '未定義')
    return '未定義'


def events(ts: TransportStreamFile, section: type[EventInformationSection] = ActualStreamEventInformationSection) -> Iterator['Event']:
    """トランスポートストリームから Event オブジェクトを返すジェネレータ"""

    for eit in ts.sections(section):
        for event in eit.events:
            yield Event(eit, event)


class Event(object):

    """イベントラッパークラス"""

    from_eit = ['service_id', 'transport_stream_id', 'original_network_id']
    from_event = ['event_id', 'start_time', 'duration', 'free_CA_mode']

    # __init__() が from_eit と from_event の名前で EIT とイベントから setattr() で写す値
    service_id: int
    transport_stream_id: int
    original_network_id: int
    event_id: int
    start_time: datetime | None
    duration: timedelta | None
    free_CA_mode: int

    def __init__(self, eit: EventInformationSection, event: Syntax) -> None:
        for field in self.from_eit:
            setattr(self, field, getattr(eit, field))
        for field in self.from_event:
            setattr(self, field, getattr(event, field))

        desc = event.descriptors
        for sed in desc.get(ShortEventDescriptor, []):
            self.title = sed.event_name_char
            self.desc = sed.text_char
        for cd in desc.get(ComponentDescriptor, []):
            self.video_content = cd.stream_content
            self.video_component = cd.component_type
            self.video = component_type_string(
                cd.stream_content, cd.component_type)
            self.video_text = cd.component_text
        for dccd in desc.get(DigitalCopyControlDescriptor, []):
            self.copy_control_type = dccd.copy_control_type
            self.digital_recording_control_data =\
                dccd.digital_recording_control_data
            self.copy = DIGITAL_RECORDING_CONTROL_TYPE[
                dccd.digital_recording_control_data]
        for acd in desc.get(AudioComponentDescriptor, []):
            if acd.main_component_flag:
                self.audio_content = acd.stream_content
                self.audio_component = acd.component_type
                self.samplint_rate_type = acd.sampling_rate
                self.audio = component_type_string(
                    acd.stream_content, acd.component_type)
                self.sampling_rate = acd.sampling_rate
                self.sampling_rate_string = SAMPLING_RATE[acd.sampling_rate]
                self.audio_text = acd.audio_text
            else:
                self.second_audio_content = acd.stream_content
                self.second_audio_component = acd.component_type
                self.second_audio = component_type_string(
                    acd.stream_content, acd.component_type)
                self.second_sampling_rate = acd.sampling_rate
                self.second_sampling_rate_string =\
                    SAMPLING_RATE[acd.sampling_rate]
                self.second_audio_text = acd.audio_text
        for egd in desc.get(EventGroupDescriptor, []):
            self.group_type = egd.group_type
            self.group_type_string = EVENT_GROUP_TYPE[egd.group_type]
            self.events = dict((e.service_id, e.event_id) for e in egd.events)
        for ctd in desc.get(ContentDescriptor, []):
            self.nibble1 = []
            self.nibble2 = []
            self.user_nibble = []
            self.genre = []
            self.subgenre = []
            self.user_genre = []
            for nibble in ctd.nibbles:
                self.nibble1.append(
                    nibble.content_nibble_level_1)
                self.nibble2.append(
                    nibble.content_nibble_level_2)
                self.genre.append(
                    CONTENT_TYPE[nibble.content_nibble_level_1][0])
                self.subgenre.append(
                    CONTENT_TYPE[nibble.content_nibble_level_1][1]
                                [nibble.content_nibble_level_2])
                self.user_nibble.append(nibble.user_nibble)
                self.user_genre.append(USER_TYPE.get(nibble.user_nibble, ''))
        # (項目名, 本文のバイト列のリスト) のリスト
        detail = []
        for eed in desc.get(ExtendedEventDescriptor, []):
            for item in eed.items:
                # 項目名の長さが 0 の項目は、本文をバイナリレベルで一つ前のものにつなげてからデコードする
                # マルチバイト文字や ESC シーケンスの途中で記述子が分かれていても、つなげれば正しくデコードできる
                # ref: ARIB TR-B14 第四分冊 第四編 第1部 4.4.3
                if item.item_description_length == 0:
                    if detail:
                        detail[-1][1][-1].extend(item.item_char)
                    continue
                key = str(item.item_description_char)
                # 項目名の長さが 0 でない項目は、文字符号の指示・呼び出しを初期状態に戻してからデコードする
                # (前の項目が LS1 などで終わっていても影響しない)
                # 項目名が空白だけの項目 (WOWOW など) は、一つ前の項目の本文に別の断片として追加する
                if key == '' and detail:
                    detail[-1][1].append(bytearray(item.item_char))
                    continue
                # 項目名が重複する場合はタブ文字を追加して区別する
                while key in dict(detail):
                    key += '\t'
                detail.append((key, [bytearray(item.item_char)]))
        # 断片の間に、文字符号の指示と呼び出しを初期状態に戻す符号列を挟んで 1 つの AribString にする
        # G0 に漢字 (ESC 0x24 0x42)・G1 に英数 (ESC 0x29 0x4A)・G2 に平仮名 (ESC 0x2A 0x30)・G3 に片仮名 (ESC 0x2B 0x31) を指示し、
        # GL に G0 (LS0)・GR に G2 (LS2R) を呼び出す
        reset_code_set = bytearray(b'\x1b\x24\x42\x1b\x29\x4a\x1b\x2a\x30\x1b\x2b\x31\x0f\x1b\x7d')
        # 断片の分割位置には改行が含まれないため、続きの断片が「【」で始まる見出しのときだけ改行 (CR) を挟む
        # (WOWOW では本文の中の「【」は常に行頭にあり、それ以外の分割位置は語や列挙の途中になっている)
        joined_detail: list[tuple[str, AribString]] = []
        for key, chunks in detail:
            value = bytearray(chunks[0])
            for chunk in chunks[1:]:
                if str(AribString(chunk)).startswith('【'):
                    value += b'\x0d'
                value += reset_code_set + chunk
            joined_detail.append((key, AribString(value)))
        detail = joined_detail
        if detail:
            self.detail = dict(detail)
            self.longdesc = '\n'.join(
                "{}\n{}\n".format(key, value) for key, value in detail)


if __name__ == '__main__':
    from subprocess import Popen, PIPE
    from ariblib import tsopen
    recpt1 = Popen(['recpt1', '236', '130', '-'], stdout=PIPE)
    # stdout=PIPE を指定したので stdout は必ず存在する
    assert recpt1.stdout is not None
    with tsopen(recpt1.stdout.fileno()) as ts:
        for event in events(ts):
            print(dir(event))
            try:
                print(event.title, event.free_CA_mode)
            except BaseException:
                pass
