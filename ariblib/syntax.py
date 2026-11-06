"""TSシンタックスの実装"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, TypeAlias

from ariblib.mnemonics import case_table, mnemonic


if TYPE_CHECKING:
    from ariblib.descriptors import Descriptor

    # on() で設定するコールバック関数
    # 拡張形式イベント記述子は記述子のリストを、それ以外の記述子は記述子を1つずつ受け取る
    DescriptorCallback: TypeAlias = 'Callable[[Descriptor | list[Descriptor]], object]'


class SyntaxDict(dict[str, Any]):

    """シンタックスを記述する辞書クラス

    責務:
    1: 宣言された順番に記述子を格納するリストを提供する
    2: ビット列表記クラスに開始位置と変数名を与える
    3: ifセクション解決用のリストを提供する

    クラス本体に書かれたすべての値 (ニーモニック・メソッド・定数など) を受け取るため、値の型は Any とする
    """

    def __init__(self) -> None:
        self.mnemonics: list[mnemonic[Syntax]] = []
        self.conditions: list[case_table[Syntax]] = []

    def __setitem__(self, key: str, value: Any) -> None:
        if isinstance(value, case_table):
            self.conditions.append(value)

        if isinstance(value, mnemonic):
            value.name = key
            value.start = self.get_start()
            self.mnemonics.append(value)

        dict.__setitem__(self, key, value)

    def get_start(self) -> Callable[[Syntax], int]:
        mnemonics = self.mnemonics[:]

        def start(instance: Syntax) -> int:
            return sum(m.real_length(instance) for m in mnemonics) +\
                instance._pos

        return start


class SyntaxType(type):

    """シンタックスのメタクラス

    SyntaxDict が生成したサブ情報を各インスタンスに付与する
    see: PEP3115
    """

    # __new__() で各シンタックスのクラスに付与する、宣言順のニーモニックと条件分岐の一覧
    _mnemonics: list[mnemonic[Syntax]]
    _conditions: list[case_table[Syntax]]

    @classmethod
    def __prepare__(cls, name: str, bases: tuple[type, ...], **kwds: Any) -> SyntaxDict:
        return SyntaxDict()

    def __new__(cls, name: str, args: tuple[type, ...], classdict: SyntaxDict) -> SyntaxType:
        instance = type.__new__(cls, name, args, classdict)
        instance._mnemonics = classdict.mnemonics
        instance._conditions = classdict.conditions
        return instance


class Syntax(metaclass=SyntaxType):

    """シンタックスの親クラス"""

    # メタクラスが付与する、宣言順のニーモニックと条件分岐の一覧
    _mnemonics: ClassVar[list[mnemonic[Syntax]]]
    _conditions: ClassVar[list[case_table[Syntax]]]

    def __init__(self, packet: bytes | bytearray, pos: int = 0, parent: Syntax | None = None) -> None:
        self._packet = packet
        self._pos = pos
        self._parent = parent
        self._callbacks: dict[type[Descriptor], DescriptorCallback] = dict()

    def __len__(self) -> int:
        """このシンタックスが持っているビット列表記の長さを全て数え上げ、
        シンタックスの長さをバイト数として返す"""

        return sum(mnemonic.real_length(self) for mnemonic in self._mnemonics)

    def __getattr__(self, name: str) -> Any:
        """指定されたプロパティが直下から見つからない場合に、
        条件に合うifシンタックスを順番に探していく

        条件分岐の中のフィールドは実行時にしか決まらないため、戻り値の型は Any とする"""

        for conditional in self._conditions:
            try:
                if conditional.condition(self):
                    sub = getattr(self, conditional.name)
                    return getattr(sub, name)
            except AttributeError:
                pass

        # 親が与えられている場合は親のプロパティも参照する
        if (
            self._parent is not None and
            any(mnemonic.name == name for mnemonic in self._parent._mnemonics)
        ):
            return getattr(self._parent, name)

    def get_names(self) -> list[str]:
        """このシンタックスインスタンスでアクセスできる
        ビット列プロパティの一覧を返す"""

        result: list[str] = []
        for m in self._mnemonics:
            name = m.name
            if isinstance(m, case_table):
                if m.condition(self):
                    # 条件に合うサブシンタックスは、case_table.__get__() がパケットと位置を渡して生成する
                    result.extend(getattr(self, name).get_names())
            else:
                result.append(name)
        return result

    def dump(self, indent: int = 0) -> None:
        from ariblib.aribstr import AribString
        from ariblib.sections import Section
        from ariblib.descriptors import Descriptor
        from types import GeneratorType
        from collections import defaultdict
        print('{}{}'.format(' ' * indent, '-' * (80 - indent)))
        if isinstance(self, Section) or isinstance(self, Descriptor):
            print('{}<<{}>>'.format(' ' * indent, self.__class__.__name__))
        for name in self.get_names():
            value = getattr(self, name)
            if isinstance(value, Syntax):
                value.dump(indent + 2)
            elif isinstance(value, defaultdict):
                for value in value.values():
                    for child in value:
                        child.dump(indent + 2)
            elif isinstance(value, list):
                for child in value:
                    child.dump(indent + 2)
            elif type(value) is GeneratorType:
                for child in value:
                    child.dump(indent + 2)
            elif isinstance(value, bytearray):
                print("{}{}\t{}".format(' ' * indent, name, AribString(value)))
            else:
                print("{}{}\t{}".format(' ' * indent, name, value))

    def on(self, Descriptor: type[Descriptor], descriptor_name: str = 'descriptors') -> Callable[[DescriptorCallback], None]:
        """記述子ごとにコールバック関数を設定する
        いまのところ、一つの記述子についてコールバック関数は1つのみ定義できる。
        """

        self.descriptor_name = descriptor_name

        def attach_callback(callback: DescriptorCallback) -> None:
            self._callbacks[Descriptor] = callback
        return attach_callback

    def execute(self) -> None:
        """指定された記述子がyieldされるごとにコールバック関数を実行する"""
        from ariblib.descriptors import ExtendedEventDescriptor

        for descriptor_type, descriptors in\
                getattr(self, self.descriptor_name).items():
            if descriptor_type not in self._callbacks:
                continue

            callback = self._callbacks[descriptor_type]
            if descriptor_type == ExtendedEventDescriptor:
                # 拡張形式イベント記述子の場合は別途対応が必要。TODO
                callback(descriptors)
            else:
                for descriptor in descriptors:
                    callback(descriptor)
