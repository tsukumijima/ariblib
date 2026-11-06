"""字幕処理関係"""

from collections.abc import Iterator
from datetime import datetime

from ariblib.aribgaiji import GAIJI_MAP
from ariblib.drcs import DRCSImage, mapping
from ariblib.packet import SynchronizedPacketizedElementaryStream, TransportStreamFile
from ariblib.sections import TimeOffsetSection


def captions(ts: TransportStreamFile, color: bool = False) -> Iterator['Caption']:
    """トランスポートストリームから字幕オブジェクトを返すジェネレータ"""

    SynchronizedPacketizedElementaryStream._pids = [ts.get_caption_pid()]
    # 関数内で CProfileString に代入するとローカル変数になるので、別の名前で使うクラスを選ぶ
    string_class: type[CProfileString]
    if color:
        string_class = ColoredCProfileString
    else:
        string_class = CProfileString

    base_pcr = next(ts.pcrs())
    base_time = next(ts.sections(TimeOffsetSection)).JST_time

    for spes in ts.sections(SynchronizedPacketizedElementaryStream):
        # TOT の JST_time は、全ビットが 1 (時刻が未定義) のときだけ None になる
        assert base_time is not None
        caption_date = base_time + (spes.pts - base_pcr)
        for data in spes.data_units:
            if data.data_unit_parameter == 0x20:
                yield Caption(caption_date,
                              string_class(data.data_unit_data))
            elif data.data_unit_parameter == 0x30:
                for code in data.codes:
                    drcs_code = code.character_code & 0xFF
                    for font in code.fonts:
                        image = DRCSImage(font.width, font.height)
                        image.point(font.patterns)
                        # point() で hash が設定される
                        assert image.hash is not None
                        string_class.drcs[drcs_code] = image.hash
                        image.save()


class Caption(object):
    def __init__(self, datetime: datetime, body: 'CProfileString') -> None:
        self.datetime = datetime
        self.body = body


class CProfileString(object):
    """CProfile文字列"""
    mapping = {
        0: ' ',
        7: '\a',
        12: '\n',
        13: '\n',
        32: ' ',
    }

    # DRCS の文字符号ごとの、DRCS のパターンのハッシュ (drcs.tsv の mapping のキー)
    drcs: dict[int, str] = {}
    strings = {}

    def __init__(self, data: bytearray) -> None:
        self.data = data
        # __str__() で文字列化した結果を保持する (文字列化すると self.data を消費するため)
        self._string: str | None = None

    def __iter__(self) -> 'CProfileString':
        return self

    def __next__(self) -> str:
        return next(self.character())

    def character(self) -> Iterator[str]:
        """一文字ずつUnicode型として返すジェネレータ"""
        while self.data:
            char1 = self.data.pop(0)
            if 0xa0 < char1 < 0xff:
                # 2バイト目がなければ、1バイトの外字として扱う
                try:
                    char2 = self.data.pop(0)
                except IndexError:
                    try:
                        yield GAIJI_MAP[char1]
                    except KeyError:
                        yield '(0x{:x})'.format(char1)
                    continue
                try:
                    yield bytes((char1, char2)).decode('euc-jp')
                except UnicodeDecodeError:
                    gaiji = ((char1 & 0x7f) << 8) | (char2 & 0x7f)
                    if gaiji == 0x7c21:
                        # 次の字幕パケットへセリフが続いていることを示す矢印
                        continue
                    try:
                        yield GAIJI_MAP[gaiji]
                    except KeyError:
                        yield '(0x{:x}{:x}, {}区{}点)'.format(
                            char1, char2, char1 & 0x5f, char2 & 0x5f)
            elif 0x20 < char1 < 0x2f:
                if char1 in self.drcs:
                    if self.drcs[char1] in mapping:
                        yield mapping[self.drcs[char1]]
                    else:
                        yield '{{{{drcs:0x{:02X}:{}}}}}'.format(
                            char1, self.drcs[char1])
            elif char1 in self.mapping:
                yield self.mapping[char1]

    def __str__(self) -> str:
        if self._string is None:
            self._string = ''.join(self).strip()
        return self._string


class ColoredCProfileString(CProfileString):

    """色付き CProfile 文字列"""

    mapping = {
        0: ' ',
        7: '\a',
        12: '\n',
        13: '\n',
        32: ' ',
        0x80: '\033[30m',
        0x81: '\033[31m',
        0x82: '\033[32m',
        0x83: '\033[33m',
        0x84: '\033[34m',
        0x85: '\033[35m',
        0x86: '\033[36m',
        0x87: '\033[37m',
    }

    def __str__(self) -> str:
        if self._string is None:
            self._string = ''.join(self).strip() + '\033[0m'
        return self._string


class WebVTTCProfileString(ColoredCProfileString):

    """WebVTT用 CProfile 文字列"""

    mapping = {
        0: ' ',
        7: '\a',
        12: '\n',
        13: '\n',
        32: ' ',
        0x80: '</c>',
        0x81: '<c.red>',
        0x82: '<c.green>',
        0x83: '<c.yellow>',
        0x84: '<c.blue>',
        0x85: '<c.purple>',
        0x86: '<c.cyan>',
        0x87: '</c>',
    }

    def __str__(self) -> str:
        if self._string is None:
            self._string = ''.join(self).strip()
            if self._string:
                self._string += '</c>'
        return self._string
