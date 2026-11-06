import sys
from argparse import ArgumentParser, Namespace, _SubParsersAction
from datetime import datetime

from ariblib import tsopen
from ariblib.caption import WebVTTCProfileString
from ariblib.packet import SynchronizedPacketizedElementaryStream


def vtt(args: Namespace) -> None:
    outpath: str | int
    if args.outpath == '-':
        outpath = sys.stdout.fileno()
    else:
        outpath = args.outpath
    with tsopen(args.inpath) as ts, open(outpath, mode='w', encoding='utf-8') as out:
        out.write('WEBVTT\n\n')

        caption_pid = ts.get_caption_pid()
        # 1seg の字幕を含まない録画では、cue のない WebVTT を書き出して終了する
        if caption_pid is None:
            return
        SynchronizedPacketizedElementaryStream._pids = [caption_pid]

        # 字幕の時刻は、ファイルの先頭の PCR を 0 秒とした base_date からの相対時刻で書き出す
        # get_caption_pid() がチャンク単位で先読みしているため、PCR と字幕はそれぞれ先頭から読み直す
        # (チューナーのパイプのようにシークできない入力では、そのまま続きから読む)
        if ts.seekable():
            ts.seek(0)
        base_pcr = next(ts.pcrs(), None)
        # PCR を1つも含まないファイルでは時刻を決められないので、cue のない WebVTT のまま終了する
        if base_pcr is None:
            return
        if ts.seekable():
            ts.seek(0)

        number = 1
        base_date = datetime(2000, 1, 1)
        # fixme: DRCS処理を隠蔽して字幕文字オブジェクトだけ返すラッパークラスが必要
        # for caption in ts.captions(drcs=True, color=True):
        #     print(caption.datetime, caption.body) みたいな
        prev_caption_date: datetime | None = None
        prev_caption: WebVTTCProfileString | str = ''
        # 最後の字幕の終了時刻に使う、最後の字幕パケットの時刻
        # 字幕パケットがなければ最後の字幕も書き出さないので、この初期値は使われない
        caption_date = base_date
        for spes in ts.sections(SynchronizedPacketizedElementaryStream):
            caption_date = base_date + (spes.pts - base_pcr)
            for data in spes.data_units:
                if data.data_unit_parameter == 0x20:
                    caption = WebVTTCProfileString(data.data_unit_data)
                    if prev_caption_date:
                        out.write(
                            '{}\n{}.{:03d} --> {}.{:03d}\n{}\n\n'.format(
                                number,
                                prev_caption_date.strftime('%H:%M:%S'),
                                prev_caption_date.microsecond // 1000,
                                caption_date.strftime('%H:%M:%S'),
                                caption_date.microsecond // 1000,
                                prev_caption,
                            )
                        )
                        # cue の番号は、書き出した cue ごとに1から順に振る
                        number += 1
                    prev_caption_date = caption_date
                    prev_caption = caption
        if prev_caption:
            # prev_caption と同時に設定するので、prev_caption_date も必ず設定されている
            assert prev_caption_date is not None
            out.write(
                '{}\n{}.{:03d} --> {}.{:03d}\n{}\n\n'.format(
                    number,
                    prev_caption_date.strftime('%H:%M:%S'),
                    prev_caption_date.microsecond // 1000,
                    caption_date.strftime('%H:%M:%S'),
                    caption_date.microsecond // 1000,
                    prev_caption,
                )
            )


def add_parser(parsers: '_SubParsersAction[ArgumentParser]') -> None:
    parser = parsers.add_parser('vtt')
    parser.set_defaults(command=vtt)
    parser.add_argument('inpath', help='input file path')
    parser.add_argument('outpath', help='output file path')
