"""ビット列表記の実装
仕様書ではビット列表記は全て小文字になっているので、ここで実装するクラスも全て小文字とする
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar, cast

from ariblib.aribstr import AribString


if TYPE_CHECKING:
    from ariblib.syntax import Syntax

    # ビット列の長さの指定
    # ビット数を表す整数・長さ (バイト数) を持つニーモニックかその名前・インスタンスから長さ (バイト数) を求める関数・
    # 子シンタックスの長さを数え上げることを表す None のいずれか
    Length: TypeAlias = 'int | str | mnemonic[Syntax] | Callable[[Syntax], int] | None'

    # ループの回数の指定
    # 回数を表す整数・回数を持つニーモニックかその名前・インスタンスから回数を求める関数のいずれか
    Count: TypeAlias = 'int | str | mnemonic[Syntax] | Callable[[Syntax], int]'

# デコレータが受け取るメソッドの self (ニーモニックの各クラス) と戻り値の型
_M = TypeVar('_M', bound='mnemonic[Syntax]')
_R = TypeVar('_R')

# ループや条件分岐の子シンタックスのクラス
_S = TypeVar('_S', bound='Syntax')
_S_co = TypeVar('_S_co', bound='Syntax', covariant=True)


def meta_cache(suffix: str) -> Callable[[Callable[[_M, Syntax], int]], Callable[[_M, Syntax], int]]:
    """real_length, real_count用のキャッシュデコレータ"""

    def cache(func: Callable[[_M, Syntax], int]) -> Callable[[_M, Syntax], int]:
        def cached(self: _M, instance: Syntax) -> int:
            cache_name = '_{}_{}'.format(self.name, suffix)
            # Pyright はメタクラスを持つクラスのインスタンスの __dict__ を MappingProxyType と解釈するため、同じ辞書を返す vars() で取得する
            caches = vars(instance)
            if cache_name in caches:
                return caches[cache_name]
            result = func(self, instance)
            caches[cache_name] = result
            return result
        return cached
    return cache


def cache(func: Callable[[_M, Syntax, type[Syntax]], _R]) -> Callable[[_M, Syntax, type[Syntax]], _R]:
    """ビット列キャッシュデコレータ"""

    def cached(self: _M, instance: Syntax, owner: type[Syntax]) -> _R:
        result = func(self, instance, owner)
        setattr(instance, self.name, result)
        return result
    return cached


class mnemonic(Generic[_S_co]):

    """ビット列表記の親クラス

    型引数は、ループや条件分岐の子シンタックスのクラス (子シンタックスを持たないニーモニックは Syntax) を表す。
    各ニーモニックが返す値の型は、それぞれの __get__() の戻り値で表す。
    """

    # 子シンタックスを持つニーモニック (ループ・条件分岐) が __init__() で設定する子シンタックスのクラス
    # 長さが None のときに real_length() が参照する
    cls: type[_S_co]

    def __init__(self, length: Length) -> None:
        self.length = length
        self.start: Callable[[Syntax], int] = lambda instance: 0
        self.name = ''

    @meta_cache('len')
    def real_length(self, instance: Syntax) -> int:
        """ビット列の実際の長さを求める"""

        if isinstance(self.length, int):
            return self.length

        if self.length is None:
            return sum(mnemonic.real_length(instance)
                       for mnemonic in self.cls._mnemonics)
        if isinstance(self.length, mnemonic):
            return getattr(instance, self.length.name) * 8
        if isinstance(self.length, str):
            return getattr(instance, self.length) * 8
        if callable(self.length):
            return self.length(instance) * 8
        return self.length


class uimsbf(mnemonic['Syntax']):

    """unsigned integer most significant bit first[符号無し整数、最上位ビットが先頭]"""

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> int:
        start = self.start(instance)
        length = self.real_length(instance)
        return self.uimsbf(instance._packet, start, length)

    @staticmethod
    def uimsbf(packet: bytes | bytearray, index: int, length: int) -> int:
        if length == 0:
            return 0

        block = index // 8
        start = index % 8
        pos = 8 - start
        if length + start <= 8:
            shift = pos - length
            filter = 2 ** pos - 2 ** shift
            return (packet[block] & filter) >> shift

        return (uimsbf.uimsbf(packet, index, pos) << (length - pos) |
                uimsbf.uimsbf(packet, index + pos, length - pos))


class bslbf(uimsbf):

    """bit string,left bit first[ビット列、左ビットが先頭]

    実質 uimsbf と同じ処理をすればよい。"""


class rpchof(uimsbf):

    """remainder polynominal coefficients, highest order first[多項式係数の剰余、最上位階数が先頭]

    正しい CRC の値かどうか検証する必要があるが、とりあえず uimsbf と同じ処理とする。"""


class mjd(mnemonic['Syntax']):

    """Modified Julian Date[修正ユリウス日]"""

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> datetime | None:
        start = self.start(instance)
        block = start // 8
        last = block + self.real_length(instance) // 8
        pmjd = instance._packet[block:last]
        if pmjd == bytearray(b'\xff\xff\xff\xff\xff'):
            return None
        # mjd2datetime() は年月日の後に pmjd の3バイト目以降を時分秒として返す可変長のタプルを返す
        # pmjd は最大5バイトなので要素は最大6個 (年月日時分秒) で、datetime の tzinfo 以降の引数には届かない
        return datetime(*cast('tuple[int, int, int, int, int, int]', mjd2datetime(pmjd)))


class bcd(mnemonic['Syntax']):

    """二進化十進数で表現された数値"""

    def __init__(self, length: Length, decimal_point: int = 0) -> None:
        self.decimal_point = decimal_point
        mnemonic.__init__(self, length)

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> float:
        # 4ビットごとに1桁を読む (symbol_rate の28ビットのようにバイト境界にそろわない長さもある)
        start = self.start(instance)
        value = 0
        for digit in range(self.real_length(instance) // 4):
            value = value * 10 + uimsbf.uimsbf(
                instance._packet, start + digit * 4, 4)
        return value / (10 ** self.decimal_point)


class bcdtime(mnemonic['Syntax']):

    """二進化十進数で表現された時分秒"""

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> timedelta | None:
        start = self.start(instance)
        block = start // 8
        last = block + 3
        bcd = instance._packet[block:last]
        if bcd == bytearray(b'\xff\xff\xff'):
            return None
        hour, minute, second = map(bcd2int, bcd)
        return timedelta(hours=hour, minutes=minute, seconds=second)


class otm(mnemonic['Syntax']):

    """オフセット時刻。二進化十進数で表現された時・分・秒・ミリ秒 (9桁の BCD と 4 ビットの予約)"""

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> timedelta:
        start = self.start(instance)
        block = start // 8
        hour, minute, second = map(bcd2int, instance._packet[block:block + 3])
        millisecond = (
            ((instance._packet[block + 3] & 0xF0) >> 4) * 100 +
            (instance._packet[block + 3] & 0x0F) * 10 +
            ((instance._packet[block + 4] & 0xF0) >> 4)
        )
        return timedelta(hours=hour, minutes=minute, seconds=second,
                         milliseconds=millisecond)


class aribstr(mnemonic['Syntax']):

    """8単位符号で符号化された文字列"""

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> AribString:
        start = self.start(instance)
        block = start // 8
        last = block + self.real_length(instance) // 8
        binary = bytearray(instance._packet[block:last])
        return AribString(binary)


class char(mnemonic['Syntax']):

    """ISO 8859-1に従って8ビットで符号化された文字列"""

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> str:
        start = self.start(instance)
        block = start // 8
        last = block + self.real_length(instance) // 8
        return ''.join(map(chr, instance._packet[block:last]))


class raw(mnemonic['Syntax']):
    """アレイそのまま"""

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> bytes | bytearray:
        start = self.start(instance)
        block = start // 8
        last = block + self.real_length(instance) // 8
        return instance._packet[block:last]


class fixed_size_loop(mnemonic[_S]):

    """バイト数固定のループ"""

    def __init__(self, cls: type[_S], length: Length) -> None:
        self.cls = cls
        mnemonic.__init__(self, length)

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> list[_S]:
        length = self.real_length(instance) // 8
        start = self.start(instance) // 8
        end = start + length
        result: list[_S] = []
        while start < end:
            start_pos = start * 8
            obj = self.cls(instance._packet, pos=start_pos)
            result.append(obj)
            start += len(obj) // 8
        return result


class fixed_count_loop(mnemonic[_S]):

    """回数固定のループ"""

    def __init__(self, cls: type[_S], count: Count) -> None:
        self.cls = cls
        self.count = count
        mnemonic.__init__(self, None)

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> list[_S]:
        start = self.start(instance) // 8
        result: list[_S] = []
        for _ in range(self.real_count(instance)):
            start_pos = start * 8
            obj = self.cls(instance._packet, pos=start_pos)
            result.append(obj)
            start += len(obj) // 8
        return result

    @meta_cache('count')
    def real_count(self, instance: Syntax) -> int:
        if isinstance(self.count, int):
            return self.count
        if isinstance(self.count, mnemonic):
            return getattr(instance, self.count.name)
        if isinstance(self.count, str):
            return getattr(instance, self.count)
        if callable(self.count):
            return self.count(instance)
        return self.count

    @meta_cache('len')
    def real_length(self, instance: Syntax) -> int:
        return sum(mnemonic.real_length(sub)
                   for sub in getattr(instance, self.name)
                   for mnemonic in sub._mnemonics)


class case_table(mnemonic[_S_co]):

    """if"""

    def __init__(self, cls: type[_S_co], condition: mnemonic[Syntax] | Callable[[Syntax], object]) -> None:
        self.cls = cls
        self.count = 1
        self.condition: Callable[[Syntax], object]
        if isinstance(condition, mnemonic):
            self.condition = lambda instance: getattr(instance, condition.name)
        else:
            self.condition = condition

        mnemonic.__init__(self, None)

    @cache
    def __get__(self, instance: Syntax, owner: type[Syntax]) -> _S_co | None:
        if self.condition(instance):
            start_pos = self.start(instance)
            return self.cls(instance._packet, pos=start_pos, parent=instance)
        return None

    @meta_cache('len')
    def real_length(self, instance: Syntax) -> int:
        if self.condition(instance):
            # サブシンタックスの各フィールドの長さは、親ではなくサブシンタックスのインスタンスで求める
            # 親で求めると、サブシンタックスの中の値で長さや回数が決まるフィールドを、親の __getattr__() 経由で別の条件分岐の中から探してしまう
            sub = getattr(instance, self.name)
            return sum(mnemonic.real_length(sub)
                       for mnemonic in sub._mnemonics)
        return 0


def bindump(target: Iterable[int]) -> str:
    """バイナリダンプ"""
    return ' '.join(map(lambda x: format(x, '08b'), target))


def hexdump(target: Iterable[int]) -> str:
    """16進ダンプ"""
    return ' '.join(map(lambda x: format(x, '02X'), target))


def mjd2datetime(pmjd: Sequence[int]) -> tuple[int, ...]:
    """mjdを年月日時分秒のタプルとして返す
    ARIB-STD-B10第2部付録Cの通りに実装"""

    mjd = (pmjd[0] << 8) | pmjd[1]
    if len(pmjd) > 2:
        bcd = pmjd[2:]
    else:
        bcd = [0, 0, 0]
    yy_ = int((mjd - 15078.2) / 365.25)
    mm_ = int((mjd - 14956.1 - int(yy_ * 365.25)) / 30.6001)
    k = 1 if 14 <= mm_ <= 15 else 0
    day = mjd - 14956 - int(yy_ * 365.25) - int(mm_ * 30.6001)
    year = 1900 + yy_ + k
    month = mm_ - 1 - k * 12
    return (year, month, day, *tuple(map(bcd2int, bcd)))


def bcd2int(bcd: int) -> int:
    """bcdを10進数にする"""

    return ((bcd & 0xF0) >> 4) * 10 + (bcd & 0x0F)


def loop(length: Length) -> Callable[[type[_S]], fixed_size_loop[_S]]:
    """サイズ固定のサブシンタックスを返すデコレータ"""

    return lambda cls: fixed_size_loop(cls, length)


def times(count: Count) -> Callable[[type[_S]], fixed_count_loop[_S]]:
    """回数固定のサブシンタックスを返すデコレータ"""

    return lambda cls: fixed_count_loop(cls, count)


def case(condition: mnemonic[Syntax] | Callable[[Syntax], object]) -> Callable[[type[_S]], case_table[_S]]:
    """ifセクションを返すデコレータ"""

    return lambda cls: case_table(cls, condition)
