# ariblib

ARIB-STD-B10 や ARIB-STD-B24 などの Python3+ での実装です。

m2ts をパースするライブラリと、応用を行ういくつかのコマンドからなります。

## インストール

pip からインストールするには以下のようにします:

```
$ sudo pip install ariblib
```

パッケージインストールがうまくいかない場合や、直接ソースコードからパッケージを作成する場合は:

```
$ git clone https://github.com/youzaka/ariblib.git
$ sudo python setup.py install
```

## コマンド利用例

### WebVTT 互換の字幕ファイルを作成する

```
$ python -m ariblib vtt SRC DST
```

とすると、 SRC にある ts ファイルを読みこみ、 DST に出力します。

- DST に `-` を指定すると標準出力に書き出します。

### ts から必要なストリームのみを取り出す(ワンセグなどの削除)

```
$ python -m ariblib split SRC DST
```

とすると、 SRC にある ts ファイルが指定する PAT 情報を読み込み、最初のストリームの動画・音声のみを保存した TS ファイルを DST に保存します。 TSSplitter のようなことができます。

### 録画された番組情報を抽出する
```
$ python -m ariblib epg SRC [DST]
```
とすると、 SRC にある ts ファイルが指定する情報を読み込み、録画された番組のEPG情報を DST に保存します（DSTは省略でき、その場合は標準出力に出力します）。

## ライブラリ利用例

コマンド化されていないことも、直接ライブラリを使って操作すると実現できます。 (PullRequest は随時受け付けています)

### 例 1: 字幕を表示

```python

from ariblib import tsopen
from ariblib.caption import captions

import sys

with tsopen(sys.argv[1]) as ts:
    for caption in captions(ts, color=True):
        body = str(caption.body)

        # アダプテーションフィールドの PCR の値と、そこから一番近い TOT テーブルの値から、
        # 字幕の表示された時刻を計算します (若干誤差が出ます)
        # PCR が一周した場合の処理は実装されていません
        datetime = caption.datetime.strftime('%Y-%m-%d %H:%M:%S')
        print('\033[35m' + datetime + '\33[37m')
        print(body)
```

### 例 2: いま放送中の番組と次の番組を表示

```python

import sys

from ariblib import tsopen
from ariblib.descriptors import ShortEventDescriptor
from ariblib.sections import EventInformationSection

def show_program(eit):
    event = iter(eit.events).__next__()
    program_title = event.descriptors[ShortEventDescriptor][0].event_name_char
    start = event.start_time
    return "{} {}".format(program_title, start)

with tsopen(sys.argv[1]) as ts:
    # 自ストリームの現在と次の番組を表示する
    EventInformationSection._table_ids = [0x4E]
    current = next(table for table in ts.sections(EventInformationSection)
                   if table.section_number == 0)
    following = next(table for table in ts.sections(EventInformationSection)
                     if table.section_number == 1)
    print('今の番組', show_program(current))
    print('次の番組', show_program(following))
```

### 例 3: 放送局名の一欄を表示

(地上波ではその局, BS では全局が表示される)

```python

import sys

from ariblib import tsopen
from ariblib.constants import SERVICE_TYPE
from ariblib.descriptors import ServiceDescriptor
from ariblib.sections import ServiceDescriptionSection

with tsopen(sys.argv[1]) as ts:
    for sdt in ts.sections(ServiceDescriptionSection):
        for service in sdt.services:
            for sd in service.descriptors[ServiceDescriptor]:
                print(service.service_id, SERVICE_TYPE[sd.service_type],
                      sd.service_provider_name, sd.service_name)
```

### 例 4: 動画パケットの PID とその動画の解像度を表示

```python

import sys

from ariblib import tsopen
from ariblib.constants import VIDEO_ENCODE_FORMAT
from ariblib.descriptors import VideoDecodeControlDescriptor
from ariblib.sections import ProgramAssociationSection, ProgramMapSection

with tsopen(sys.argv[1]) as ts:
    pat = next(ts.sections(ProgramAssociationSection))
    ProgramMapSection._pids = list(pat.pmt_pids)
    for pmt in ts.sections(ProgramMapSection):
        for tsmap in pmt.maps:
            for vd in tsmap.descriptors.get(VideoDecodeControlDescriptor, []):
                print(tsmap.elementary_PID, VIDEO_ENCODE_FORMAT[vd.video_encode_format])
```

### 例 5: EPG 情報の表示

```python
from ariblib import tsopen
from ariblib.event import events

import sys

with tsopen(sys.argv[1]) as ts:
    for event in events(ts):
        max_len = max(map(len, event.__dict__.keys()))
        template = "{:%ds}  {}" % max_len
        for key, value in event.__dict__.items():
            print(template.format(key, value))
        print('-' * 80)
```

### 例 6: 深夜アニメの出力

```python

import sys

from ariblib import tsopen
from ariblib.descriptors import ContentDescriptor, ShortEventDescriptor
from ariblib.sections import EventInformationSection

with tsopen(sys.argv[1]) as ts:
    EventInformationSection._table_ids = range(0x50, 0x70)
    for eit in ts.sections(EventInformationSection):
        for event in eit.events:
            for genre in event.descriptors.get(ContentDescriptor, []):
                nibble = genre.nibbles[0]
                # ジャンルがアニメでないイベント、アニメであっても放送開始時刻が5時から21時のものを除きます
                if nibble.content_nibble_level_1 != 0x07 or 4 < event.start_time.hour < 22:
                    continue
                for sed in event.descriptors.get(ShortEventDescriptor, []):
                    print(eit.service_id, event.event_id, event.start_time,
                          event.duration, sed.event_name_char, sed.text_char)
```

### 例 7: データ放送ファイルの抽出

Type Hints を使うため，Python 3.5+が必要

```python
#!/usr/bin/python
# -*- coding: UTF-8 -*
from collections import defaultdict
from ariblib import tsopen
from ariblib.diidescriptors import TypeDescriptor, CompressionTypeDescriptor
from ariblib.sections import ProgramAssociationSection, ProgramMapSection, DSMCCSection
import pathlib
import zlib
import email.parser
import sys


class Module_info:
    number_of_modules: int
    download_id: int
    version_number: int
    block_size: int
    is_compressed: bool
    type_name: str

    def __init__(self, number_of_modules, download_id, version_number, block_size, is_compressed, type_name) -> None:
        self.number_of_modules = number_of_modules
        self.download_id = download_id
        self.version_number = version_number
        self.block_size = block_size
        self.is_compressed = is_compressed
        self.type_name = type_name


def save_file(component_tag, module_id, diis, cache_ddbs, parser: email.parser.BytesParser):
    print("Start saving")
    module_info: Module_info = diis[component_tag][module_id]
    data = bytearray()

    for i in range(module_info.number_of_modules):
        data.extend(cache_ddbs[component_tag][module_id][i])

    if module_info.is_compressed:
        try:
            data = zlib.decompress(data)
        except zlib.error:
            print("Decompress failed. Data may be broken.")
            return
    message = parser.parsebytes(data)
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        else:
            headers = part._headers

            for header in headers:
                if header[0] == "Content-Location":
                    if header[1] == "":
                        print("Found x-arib-resource-list, Skip.")
                        continue
                    p = pathlib.Path(
                        f"output/{component_tag}/{module_id}/{module_info.download_id}/")
                    p.mkdir(parents=True, exist_ok=True)
                    filename = header[1]
                    filepath = p/filename
                    print(
                        f"Save {filename} to output/{component_tag}/{module_id}/{module_info.download_id}/")
                    with filepath.open("wb") as f:
                        f.write(part.get_payload(decode=True))

    return


def main():
    parser = email.parser.BytesParser()
    diis = defaultdict(dict)
    cache_ddbs = defaultdict(lambda: defaultdict(dict))

    with tsopen("f:/nhk.ts") as ts:
        pat = next(ts.sections(ProgramAssociationSection))
        ProgramMapSection._pids = list(pat.pmt_pids)
        typs = set()

        pmt = next(ts.sections(ProgramMapSection))
        pids_with_comoponent_tag = {x[0]: x[1]
                                    for x in list(pmt.data_pids_with_comoponent_tag)}
        DSMCCSection._pids = pids_with_comoponent_tag.keys()
        try:
            for dsmcc in ts.sections(DSMCCSection):
                PID = dsmcc._pid
                dsmcc._component_tag = pids_with_comoponent_tag[PID]
                if dsmcc.table_id == 0x3B:

                    for module in dsmcc.userNetWorkMessage.modules:
                        download_id = dsmcc.userNetWorkMessage.downloadId
                        version_number = module.moduleVersion
                        if module.moduleId in diis[dsmcc._component_tag] and diis[dsmcc._component_tag][module.moduleId].download_id == download_id and diis[dsmcc._component_tag][module.moduleId].version_number == version_number:
                            print(
                                f"Component tag {dsmcc._component_tag} Module {module.moduleId} has no update, pass...")
                            continue
                        else:
                            print(
                                f"Component tag {dsmcc._component_tag} Module {module.moduleId} updated!")
                            if module.moduleId in cache_ddbs[dsmcc._component_tag]:
                                cache_ddbs[dsmcc._component_tag][module.moduleId].clear(
                                )

                        type_descriptor = module.moduleDescriptors.get(
                            Type_descriptor, [])
                        type_name = "" if len(
                            type_descriptor) == 0 else type_descriptor[0].text
                        compression_type_descriptor = module.moduleDescriptors.get(
                            Compression_Type_descriptor, [])
                        is_compressed = len(compression_type_descriptor) != 0
                        number_of_modules = module.moduleSize // dsmcc.blockSize + \
                            (1 if module.moduleSize % dsmcc.blockSize else 0)

                        module_info = Module_info(
                            number_of_modules, download_id, version_number, dsmcc.blockSize, is_compressed, type_name)

                        diis[dsmcc._component_tag][module.moduleId] = module_info

                elif dsmcc.table_id == 0x3C:
                    component_tag = dsmcc._component_tag
                    module_id = dsmcc.downloadDataMessage.moduleId
                    if module_id not in diis[component_tag]:
                        print(
                            f"No information of Component tag {component_tag} Module {module_id} found, pass...")
                        continue

                    module_info: Module_info = diis[component_tag][module_id]

                    if len(cache_ddbs[component_tag][module_id]) == module_info.number_of_modules:
                        print(
                            f"Component tag {component_tag} Module {module_id} is already downloaded, pass...")
                        continue

                    if len(dsmcc.downloadDataMessage.blockDataBytes) != module_info.block_size and dsmcc.downloadDataMessage.blockNumber != module_info.number_of_modules-1:
                        print(
                            f"Component tag {component_tag} Module {module_id} Block {dsmcc.downloadDataMessage.blockNumber} is broken, pass...")
                        continue

                    target: dict = cache_ddbs[component_tag][module_id]
                    target[dsmcc.downloadDataMessage.blockNumber] = dsmcc.downloadDataMessage.blockDataBytes.copy()

                    if len(cache_ddbs[component_tag][module_id]) == module_info.number_of_modules:
                        print(
                            f"Component tag {component_tag} Module {module_id} is completely downloaded, start saving...")
                        save_file(component_tag, module_id,
                                  diis, cache_ddbs, parser)
        except IndexError:
            print("Seems like file is over. Exit.")


if __name__ == "__main__":
    main()
```
