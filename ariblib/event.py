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
    BroadcastIdDescriptor,
    CAIdentifierDescriptor,
    ComponentDescriptor,
    ContentDescriptor,
    DigitalCopyControlDescriptor,
    EventGroupDescriptor,
    ExtendedEventDescriptor,
    PartialTSTimeDescriptor,
    ShortEventDescriptor,
)
from ariblib.packet import TransportStreamFile
from ariblib.sections import (
    ActualStreamEventInformationSection,
    EventInformationSection,
    SelectionInformationSection,
)
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


def events(
    ts: TransportStreamFile,
    section: type[EventInformationSection | SelectionInformationSection] = ActualStreamEventInformationSection,
) -> Iterator['Event']:
    """トランスポートストリームから Event オブジェクトを返すジェネレータ

    section に SelectionInformationSection を指定すると、パーシャルトランスポートストリーム (BD などに記録された TS) の
    SIT の各サービスの、現在のイベントを返す
    """

    for table in ts.sections(section):
        if isinstance(table, SelectionInformationSection):
            for service in table.services:
                yield Event(table, service)
        else:
            for event in table.events:
                yield Event(table, event)


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

    # 以下は、イベントが対応する記述子を持つときだけ __init__() が設定する値
    # 記述子がなければ属性そのものが存在しないため、利用する側は hasattr() で確認してから参照する
    # 短形式イベント記述子
    title: AribString
    desc: AribString
    # コンポーネント記述子
    video_content: int
    video_component: int
    video: str
    video_text: AribString
    # デジタルコピー制御記述子
    copy_control_type: int
    digital_recording_control_data: int
    copy: str
    # 音声コンポーネント記述子 (主音声)
    audio_content: int
    audio_component: int
    samplint_rate_type: int
    audio: str
    sampling_rate: int
    sampling_rate_string: str
    audio_text: AribString
    # 音声コンポーネント記述子 (副音声)
    second_audio_content: int
    second_audio_component: int
    second_audio: str
    second_sampling_rate: int
    second_sampling_rate_string: str
    second_audio_text: AribString
    # イベントグループ記述子 (service_id から event_id への辞書)
    group_type: int
    group_type_string: str
    events: dict[int, int]
    # コンテント記述子
    nibble1: list[int]
    nibble2: list[int]
    user_nibble: list[int]
    genre: list[str]
    subgenre: list[str]
    user_genre: list[str]
    # 拡張形式イベント記述子 (項目名から本文への辞書と、項目名と本文を連結した文字列)
    detail: dict[str, AribString]
    longdesc: str

    def __init__(self, eit: EventInformationSection | SelectionInformationSection, event: Syntax) -> None:
        """
        Args:
            eit: イベントを含む EIT。パーシャルトランスポートストリームでは、EIT の代わりに SIT を渡す
            event: EIT のイベントのループの要素。SIT を渡したときは、SIT のサービスのループの要素

        Raises:
            ValueError: SIT のサービスに、元の放送の各種 ID を記述する放送 ID 記述子がないとき
        """

        if isinstance(eit, SelectionInformationSection):
            self._init_from_sit(event)
        else:
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


    def _init_from_sit(self, service: Syntax) -> None:
        """SIT のサービスの記述子から、EIT から写す値と同じ値を設定する

        パーシャルトランスポートストリームの SIT は、元の放送の ID を放送 ID 記述子に、
        イベントの開始時刻と継続時間をパーシャルトランスポートストリームタイム記述子に記述する (ARIB STD-B21 9.1.8, ARIB TR-B15 第一部 第二編 6.2.4)

        Args:
            service: SIT のサービスのループの要素
        """

        desc = service.descriptors
        self.service_id = service.service_id
        broadcast_ids = desc.get(BroadcastIdDescriptor, [])
        if not broadcast_ids:
            raise ValueError('service 0x{:04X} of the SIT has no broadcast_id_descriptor'.format(service.service_id))
        self.transport_stream_id = broadcast_ids[0].transport_stream_id
        self.original_network_id = broadcast_ids[0].original_network_id
        self.event_id = broadcast_ids[0].event_id

        # 開始時刻と継続時間 (記述されていなければ None)
        # offset_flag が 0 ならオフセット時間を加算し、1 なら減算する
        self.start_time = None
        self.duration = None
        for time in desc.get(PartialTSTimeDescriptor, []):
            self.duration = time.duration
            if time.event_start_time is not None:
                offset = time.offset if time.offset is not None else timedelta()
                if time.offset_flag:
                    self.start_time = time.event_start_time - offset
                else:
                    self.start_time = time.event_start_time + offset
            break

        # SIT のサービスのループは free_CA_mode を持たないため、SDT と同じく CA 識別記述子の有無で有料のサービスかを判定する
        self.free_CA_mode = 1 if desc.get(CAIdentifierDescriptor, []) else 0


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
