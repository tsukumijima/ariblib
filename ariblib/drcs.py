from collections.abc import Iterable
from hashlib import md5
from typing import TYPE_CHECKING, cast

try:
    from PIL import Image
    from PIL.ImageDraw import Draw
except ImportError:
    text_output = True
else:
    text_output = False

import csv
import os

import ariblib


if TYPE_CHECKING:
    # DRCSImage は PIL を読み込めたときだけ使う (読み込めなければ末尾で DRCSText に置き換える) ため、
    # 型検査では PIL を読み込めたものとして扱う
    from PIL import Image
    from PIL.ImageDraw import Draw

    from ariblib.syntax import Syntax


save_dir = os.path.expanduser('~/.ariblib/drcs/')
mapping_path = os.path.join((os.path.split(ariblib.__file__)[0]), 'drcs.tsv')
user_mapping_path = os.path.expanduser('~/.ariblib/drcs.tsv')
# DRCS のパターンのハッシュと、対応する文字列
mapping: dict[str, str] = {}

# DRCSイメージ保存ディレクトリが存在しない場合は作成する
if not os.path.isdir(save_dir):
    import os

    os.makedirs(save_dir)

for path in (mapping_path, user_mapping_path):
    try:
        with open(path, encoding='utf-8') as f:
            reader = csv.reader(f, delimiter='\t')
            # 各行はハッシュと文字列の2列 (2列でない行は dict.update() と同じく ValueError になる)
            for line in reader:
                key, value = line
                mapping[key] = value
    except OSError:
        pass


class DRCSImage(object):
    """DRCSの画像イメージ"""

    def __init__(self, width: int, height: int, bgcolor: str = 'white') -> None:
        self.image = Image.new('RGB', (width, height), bgcolor)
        self.hash: str | None = None

    def point(self, patterns: Iterable['Syntax'], color: str = 'black') -> None:
        hasher = md5()
        draw = Draw(self.image)
        for y, pattern in enumerate(patterns):
            pattern_data = pattern.pattern_data
            hasher.update(pattern_data)
            points = [(x, y) for x, dot in enumerate(_to_bit(pattern_data)) if dot == '1']
            draw.point(points, color)
        self.hash = hasher.hexdigest()

    def save(self, ext: str = 'png', path: str | None = None) -> None:
        if path is None:
            if self.hash is None:
                raise ValueError('画像イメージが作成されていません')
            path = self.hash
        self.image.save(os.path.join(save_dir, path + '.' + ext))


class DRCSText(object):
    """DRCSのテキストイメージ"""

    def __init__(self, width: int, height: int, bgcolor: str = 'white') -> None:
        self.hash: str | None = None
        self.dots: list[str] = []

    def point(self, patterns: Iterable['Syntax'], color: str = 'black') -> None:
        hasher = md5()
        for pattern in patterns:
            pattern_data = pattern.pattern_data
            hasher.update(pattern_data)
            points = _to_bit(pattern_data).replace('0', '　').replace('1', '■')
            self.dots.append(points)
        self.hash = hasher.hexdigest()

    def save(self, ext: str = 'txt', path: str | None = None) -> None:
        if path is None:
            if self.hash is None:
                raise ValueError('画像イメージが作成されていません')
            path = self.hash
        with open(os.path.join(save_dir, path + '.' + ext), mode='w', encoding='utf-8') as out:
            out.write('\n'.join(self.dots))


def _to_bit(raw: Iterable[int]) -> str:
    """bytearrayを0,1のビットを表す文字列に変換する"""

    return ''.join(map(lambda s: format(s, '08b'), raw))


# 画像出力ができないときはテキスト出力を行う
# 実行時にクラスを差し替えるため、型検査では DRCSText を DRCSImage と同じ使い方 (生成・point()・hash・save()) ができるクラスとして扱う
if text_output:
    DRCSImage = cast('type[DRCSImage]', DRCSText)
