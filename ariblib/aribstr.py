#!/usr/bin/env python3

import array
import io
import sys
from collections.abc import Iterable

from ariblib.aribgaiji import GAIJI_MAP, GAIJI_MAP_OTHER, GAIJI_MAP_TITLE

"""
ARIB文字の実装クラス
original: https://github.com/murakamiy/epgdump_py/blob/master/aribstr.py
Copyright (C) 2011 Yasumasa Murakami. All Rights Reserved.
"""


class Code:
    (
        KANJI,
        ALPHANUMERIC,
        HIRAGANA,
        KATAKANA,
        MOSAIC_A,
        MOSAIC_B,
        MOSAIC_C,
        MOSAIC_D,
        PROP_ALPHANUMERIC,
        PROP_HIRAGANA,
        PROP_KATAKANA,
        JIS_X0201_KATAKANA,
        JIS_KANJI_PLANE_1,
        JIS_KANJI_PLANE_2,
        ADDITIONAL_SYMBOLS,
        UNSUPPORTED,
    ) = range(16)


CODE_SET_G = {
    0x42: (Code.KANJI, 2),
    0x4A: (Code.ALPHANUMERIC, 1),
    0x30: (Code.HIRAGANA, 1),
    0x31: (Code.KATAKANA, 1),
    0x32: (Code.MOSAIC_A, 1),
    0x33: (Code.MOSAIC_B, 1),
    0x34: (Code.MOSAIC_C, 1),
    0x35: (Code.MOSAIC_D, 1),
    0x36: (Code.PROP_ALPHANUMERIC, 1),
    0x37: (Code.PROP_HIRAGANA, 1),
    0x38: (Code.PROP_KATAKANA, 1),
    0x49: (Code.JIS_X0201_KATAKANA, 1),
    0x39: (Code.JIS_KANJI_PLANE_1, 2),
    0x3A: (Code.JIS_KANJI_PLANE_2, 2),
    0x3B: (Code.ADDITIONAL_SYMBOLS, 2),
}

CODE_SET_DRCS = {
    0x40: (Code.UNSUPPORTED, 2),  # DRCS-0
    0x41: (Code.UNSUPPORTED, 1),  # DRCS-1
    0x42: (Code.UNSUPPORTED, 1),  # DRCS-2
    0x43: (Code.UNSUPPORTED, 1),  # DRCS-3
    0x44: (Code.UNSUPPORTED, 1),  # DRCS-4
    0x45: (Code.UNSUPPORTED, 1),  # DRCS-5
    0x46: (Code.UNSUPPORTED, 1),  # DRCS-6
    0x47: (Code.UNSUPPORTED, 1),  # DRCS-7
    0x48: (Code.UNSUPPORTED, 1),  # DRCS-8
    0x49: (Code.UNSUPPORTED, 1),  # DRCS-9
    0x4A: (Code.UNSUPPORTED, 1),  # DRCS-10
    0x4B: (Code.UNSUPPORTED, 1),  # DRCS-11
    0x4C: (Code.UNSUPPORTED, 1),  # DRCS-12
    0x4D: (Code.UNSUPPORTED, 1),  # DRCS-13
    0x4E: (Code.UNSUPPORTED, 1),  # DRCS-14
    0x4F: (Code.UNSUPPORTED, 1),  # DRCS-15
    0x70: (Code.UNSUPPORTED, 1),  # MACRO
}

CODE_SET_KEYS = list(CODE_SET_DRCS.keys()) + list(CODE_SET_G.keys())

ARIB_BASE = {
    0x79: 0x3C,
    0x7A: 0x23,
    0x7B: 0x56,
    0x7C: 0x57,
    0x7D: 0x22,
    0x7E: 0x26,
}

ARIB_HIRAGANA_MAP = {
    0x77: 0x35,
    0x78: 0x36,
}

ARIB_KATAKANA_MAP = {
    0x77: 0x33,
    0x78: 0x34,
}

ARIB_KATAKANA_MAP.update(ARIB_BASE)
ARIB_HIRAGANA_MAP.update(ARIB_BASE)
# ひらがな  カタカナ
# ゝ 35     ヽ 33
# ゞ 36     ヾ 34
# ー 3c     ー 3c
# 。 23     。 23
# 「 56     「 56
# 」 57     」 57
# 、 22     、 22
# ・ 26     ・ 26

# 文字符号集合に未定義の区点を表す文字 (EDCB と同じ 〓)
UNDEFINED_CHAR = '\u3013'

# 制御符号の後ろに付くパラメータのバイト数 (ARIB STD-B24 第一編 第2部 表 7-14 以降)
# C0: PAPF (P1)・APS (P1, P2) / C1: SZX・COL・FLC・CDC・POL・WMM・HLC・RPC の P1 (1バイト)
# CSI は長さが可変なので do_control() で個別に読み飛ばす
CONTROL_PARAMETER_SIZE = {
    0x16: 1,  # PAPF
    0x1C: 2,  # APS
    0x8B: 1,  # SZX
    0x90: 1,  # COL (P1 が 0x20 のときは P2 がさらに続く)
    0x91: 1,  # FLC
    0x92: 1,  # CDC (P1 が 0x20 のときは P2 がさらに続く)
    0x93: 1,  # POL
    0x94: 1,  # WMM
    0x97: 1,  # HLC
    0x98: 1,  # RPC
}


class Buffer:
    G0, G1, G2, G3 = range(4)


class CodeArea:
    LEFT, RIGHT = range(2)


class AribIndexError(Exception):
    pass


class EscapeSequenceError(Exception):
    pass


class DesignationError(Exception):
    pass


class CodeSetController:
    def __init__(self) -> None:
        self.v_buffer = {
            Buffer.G0: CODE_SET_G[0x42],  # KANJI
            Buffer.G1: CODE_SET_G[0x4A],  # ALPHANUMERIC
            Buffer.G2: CODE_SET_G[0x30],  # HIRAGANA
            Buffer.G3: CODE_SET_G[0x31],  # KATAKANA
        }
        self.single_shift: int | None = None
        self.graphic_left = Buffer.G0  # KANJI
        self.graphic_right = Buffer.G2  # HIRAGANA
        self.esc_seq_count = 0
        self.esc_buffer_index = Buffer.G0
        self.esc_drcs = False

    def designate(self, code: int) -> None:
        if code not in CODE_SET_KEYS:
            raise DesignationError(
                'esc_seq_count=%i esc_buffer_index=%s code=0x%02X' % (self.esc_seq_count, self.esc_buffer_index, code)
            )
        if self.esc_drcs:
            self.v_buffer[self.esc_buffer_index] = CODE_SET_DRCS[code]
        else:
            self.v_buffer[self.esc_buffer_index] = CODE_SET_G[code]
        self.esc_seq_count = 0

    def invoke(self, buffer_index: int, area: int, locking_shift: bool = True) -> None:
        if CodeArea.LEFT == area:
            if locking_shift:
                self.graphic_left = buffer_index
            else:
                self.single_shift = buffer_index
        elif CodeArea.RIGHT == area:
            self.graphic_right = buffer_index
        self.esc_seq_count = 0

    def get_current_code(self, data: int) -> tuple[int, int] | None:
        if 0x21 <= data <= 0x7E:
            if self.single_shift is not None:
                code = self.v_buffer[self.single_shift]
                self.single_shift = None
                return code
            return self.v_buffer[self.graphic_left]
        elif 0xA1 <= data <= 0xFE:
            return self.v_buffer[self.graphic_right]
        return None

    def set_escape(self, buffer_index: int | None, drcs: bool) -> None:
        if buffer_index is not None:
            self.esc_buffer_index = buffer_index
        self.esc_drcs = drcs
        self.esc_seq_count += 1


class AribArray(bytearray):
    def pop0(self) -> int:
        try:
            return self.pop(0)
        except IndexError:
            raise AribIndexError


class AribString:
    def __init__(self, array: Iterable[int]) -> None:
        self.control = CodeSetController()
        self.arib_array = AribArray(array)
        self.utf_buffer = io.StringIO()
        self.utf_buffer_symbol = io.StringIO()
        self.split_symbol = False

    def __add__(self, other: 'AribString') -> 'AribString':
        self.arib_array += other.arib_array
        return self

    def __str__(self) -> str:
        return self.convert_utf().rstrip()

    def convert_utf_split(self) -> tuple[str, str]:
        self.split_symbol = True
        self.convert()
        return (self.utf_buffer.getvalue(), self.utf_buffer_symbol.getvalue())

    def convert_utf(self, with_gaiji: bool = True) -> str:
        self.convert(with_gaiji)
        return self.utf_buffer.getvalue()

    def write_euc(self, *euc_bytes: int) -> None:
        # EUC-JIS-2004 (JIS X0213:2004 の面 1 は JIS X0208 の上位互換) として 1 文字をデコードして書き込む
        # 文字符号集合に未定義の区点は、文字列全体を捨てずにその 1 文字だけを 〓 にする
        try:
            self.utf_buffer.write(bytes(euc_bytes).decode('euc_jis_2004'))
        except UnicodeDecodeError:
            self.utf_buffer.write(UNDEFINED_CHAR)

    def convert(self, with_gaiji: bool = True) -> io.StringIO:
        while True:
            try:
                data = self.arib_array.pop0()
                if self.control.esc_seq_count:
                    self.do_escape(data)
                else:
                    if 0x21 <= data <= 0x7E or 0xA1 <= data <= 0xFE:
                        # GL/GR Table
                        self.do_convert(data, with_gaiji)
                    elif data in (
                        0x20,  # space
                        0xA0,  # space (arib)
                        0x09,  # HT
                    ):
                        self.utf_buffer.write(' ')
                    elif data in (
                        0x0D,  # CR
                        0x0A,  # LF
                    ):
                        self.utf_buffer.write('\n')
                    else:
                        # Control Character
                        self.do_control(data)
            except AribIndexError:
                break
        return self.utf_buffer

    def do_convert(self, data: int, with_gaiji: bool = True) -> None:
        # convert() は GL・GR の範囲の符号だけを渡すので、get_current_code() は必ず符号集合を返す
        current_code = self.control.get_current_code(data)
        assert current_code is not None
        code, size = current_code
        char = data
        char2 = 0x0
        if size == 2:
            char2 = self.arib_array.pop0()
        if 0xA1 <= char <= 0xFE:
            char = char & 0x7F
            char2 = char2 & 0x7F

        # 漢字系集合と追加記号集合は 84 区以下が同一で (表 7-4・表 7-10)、85 区以降だけが追加漢字・追加記号になる
        # どちらの集合が指示されていても、84 区以下は漢字、85 区以降は外字として扱う
        if code in (Code.KANJI, Code.ADDITIONAL_SYMBOLS):
            if char >= 0x75:
                code = Code.ADDITIONAL_SYMBOLS
            else:
                code = Code.KANJI

        if code in (Code.KANJI, Code.JIS_KANJI_PLANE_1):
            # 漢字コード出力 (JIS X0213:2004 の面 1 は 13 区などの JIS X0208 にない区も含む)
            self.write_euc(char | 0x80, char2 | 0x80)
        elif code == Code.JIS_KANJI_PLANE_2:
            # JIS X0213:2004 の面 2 (EUC-JIS-2004 では 0x8F が前置される)
            self.write_euc(0x8F, char | 0x80, char2 | 0x80)
        elif code in (Code.ALPHANUMERIC, Code.PROP_ALPHANUMERIC):
            # 英数字コード出力
            self.utf_buffer.write(chr(char))
        elif code in (Code.HIRAGANA, Code.PROP_HIRAGANA):
            # ひらがなコード出力
            if char >= 0x77:
                self.write_euc(0xA1, ARIB_HIRAGANA_MAP[char] | 0x80)
            else:
                self.write_euc(0xA4, char | 0x80)
        elif code in (Code.PROP_KATAKANA, Code.KATAKANA):
            # カタカナコード出力
            if char >= 0x77:
                self.write_euc(0xA1, ARIB_KATAKANA_MAP[char] | 0x80)
            else:
                self.write_euc(0xA5, char | 0x80)
        elif code == Code.JIS_X0201_KATAKANA:
            # 半角カタカナコード出力 (EUC-JIS-2004 では 0x8E が前置される)
            self.write_euc(0x8E, char | 0x80)
        elif code == Code.ADDITIONAL_SYMBOLS:
            # 追加シンボル文字コード出力
            if with_gaiji:
                if self.split_symbol:
                    wchar = (char << 8) + char2
                    gaiji = GAIJI_MAP_TITLE.get(wchar)
                    if gaiji is not None:
                        self.utf_buffer_symbol.write(gaiji)
                    else:
                        self.utf_buffer.write(GAIJI_MAP_OTHER.get(wchar, "??"))
                else:
                    self.utf_buffer.write(GAIJI_MAP.get(((char << 8) + char2), "??"))

    def do_control(self, data: int) -> None:
        if data == 0x0F:
            self.control.invoke(Buffer.G0, CodeArea.LEFT, True)  # LS0
        elif data == 0x0E:
            self.control.invoke(Buffer.G1, CodeArea.LEFT, True)  # LS1
        elif data == 0x19:
            self.control.invoke(Buffer.G2, CodeArea.LEFT, False)  # SS2
        elif data == 0x1D:
            self.control.invoke(Buffer.G3, CodeArea.LEFT, False)  # SS3
        elif data == 0x1B:
            self.control.esc_seq_count = 1
        elif data in CONTROL_PARAMETER_SIZE:
            # 制御符号に続くパラメータは文字符号ではないので、文字として解釈せずに読み飛ばす
            # parameter には最後に読んだパラメータが入る (どの制御符号もパラメータを1バイト以上持つ)
            parameter = 0
            for _ in range(CONTROL_PARAMETER_SIZE[data]):
                parameter = self.arib_array.pop0()
            # COL・CDC は P1 が 0x20 のとき P2 がさらに 1 バイト続く
            if data in (0x90, 0x92) and parameter == 0x20:
                self.arib_array.pop0()
        elif data == 0x9B:
            # CSI は、パラメータ (0x30-0x3F)・中間文字 (0x20-0x2F)・終端文字 (0x40-0x7F) の順に続く
            # いずれも文字符号ではないので、終端文字までを読み飛ばす
            while self.arib_array and 0x20 <= self.arib_array[0] <= 0x3F:
                self.arib_array.pop0()
            if self.arib_array and 0x40 <= self.arib_array[0] <= 0x7F:
                self.arib_array.pop0()

    def do_escape(self, data: int) -> None:
        if self.control.esc_seq_count == 1:
            if data == 0x6E:
                self.control.invoke(Buffer.G2, CodeArea.LEFT, True)  # LS2
            elif data == 0x6F:
                self.control.invoke(Buffer.G3, CodeArea.LEFT, True)  # LS3
            elif data == 0x7E:
                self.control.invoke(Buffer.G1, CodeArea.RIGHT, True)  # LS1R
            elif data == 0x7D:
                self.control.invoke(Buffer.G2, CodeArea.RIGHT, True)  # LS2R
            elif data == 0x7C:
                self.control.invoke(Buffer.G3, CodeArea.RIGHT, True)  # LS3R
            elif data in (0x24, 0x28):
                self.control.set_escape(Buffer.G0, False)
            elif data == 0x29:
                self.control.set_escape(Buffer.G1, False)
            elif data == 0x2A:
                self.control.set_escape(Buffer.G2, False)
            elif data == 0x2B:
                self.control.set_escape(Buffer.G3, False)
            else:
                raise EscapeSequenceError(
                    'esc_seq_count=%i data=0x%02X'
                    % (
                        self.control.esc_seq_count,
                        data,
                    )
                )
        elif self.control.esc_seq_count == 2:
            if data == 0x20:
                self.control.set_escape(None, True)
            elif data == 0x28:
                self.control.set_escape(Buffer.G0, False)
            elif data == 0x29:
                self.control.set_escape(Buffer.G1, False)
            elif data == 0x2A:
                self.control.set_escape(Buffer.G2, False)
            elif data == 0x2B:
                self.control.set_escape(Buffer.G3, False)
            else:
                self.control.designate(data)
        elif self.control.esc_seq_count == 3:
            if data == 0x20:
                self.control.set_escape(None, True)
            else:
                self.control.designate(data)
        elif self.control.esc_seq_count == 4:
            self.control.designate(data)

    def __repr__(self) -> str:
        return self.convert_utf().rstrip()


if __name__ == '__main__':
    f = open(sys.argv[1], mode='rb')
    f.seek(0, 2)
    byte = f.tell()
    f.seek(0)
    arr = array.array('B')
    arr.fromfile(f, byte)
    f.close()

    arib = AribString(arr)
    arib.convert()

    f = open("output.txt", mode='w', encoding='utf-8')
    f.write(arib.utf_buffer.getvalue())
    f.close()
