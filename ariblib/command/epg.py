import json
import sys
from argparse import ArgumentParser, Namespace, _SubParsersAction
from collections import defaultdict
from collections.abc import Iterator

from ariblib import tsopen
from ariblib.event import Event
from ariblib.packet import TransportStreamFile
from ariblib.sections import (
    ActualStreamPresentFollowingEventInformationSection,
    ProgramAssociationSection,
)


def dump_json(event: Event) -> str:
    # extract_epg() は duration を持つイベントだけを渡す
    assert event.duration is not None
    return json.dumps(
        {
            "start_time": str(event.start_time),
            "duration": event.duration.total_seconds(),
            "title": str(event.title),
            "desc": str(event.desc),
            "genre": event.genre,
            "subgenre": event.subgenre,
            "user_genre": event.user_genre,
        },
        ensure_ascii=False,
    )


def extract_epg(ts: TransportStreamFile, threshold: int = 5) -> Iterator[str]:
    count: defaultdict[tuple[str, str], int] = defaultdict(int)
    programs: dict[tuple[str, str], str] = {}

    # Use the first service in the PAT, because the output has no field to
    # tell which service each program belongs to.
    pat = next(ts.sections(ProgramAssociationSection), None)
    if pat is None:
        return
    service_id = next((number for number, _pid in pat.pmt_items), None)
    if service_id is None:
        return
    if ts.seekable():
        ts.seek(0)

    for section in ts.sections(ActualStreamPresentFollowingEventInformationSection):
        # In EIT[p/f], section_number 0 is the current program and 1 is the
        # next program. We only care about current program information.
        if section.section_number != 0 or section.service_id != service_id:
            continue

        events = [Event(section, e) for e in section.events]
        for event in events:
            duration = getattr(event, "duration", None)
            genre = getattr(event, "genre", None)
            if duration and genre:
                # The key to uniquely identify each program.
                key = (str(event.start_time), str(event.title))
                count[key] += 1
                programs[key] = dump_json(event)

    for key in sorted(programs):
        # EPG info is transmitted once per second over network, and we
        # sometimes see artifacts from the previous/subsequent programs.
        # Let's ignore EPGs whose frequency is too low.
        if count[key] > threshold:
            yield programs[key]


def epg(args: Namespace) -> None:
    outpath: str | int
    if args.outpath == '-':
        outpath = sys.stdout.fileno()
    else:
        outpath = args.outpath

    with tsopen(args.inpath) as ts:
        with open(outpath, mode='w', encoding='utf-8') as fw:
            for epg in extract_epg(ts):
                print(epg, file=fw)


def add_parser(parsers: '_SubParsersAction[ArgumentParser]') -> None:
    parser = parsers.add_parser('epg')
    parser.set_defaults(command=epg)
    parser.add_argument('inpath', help='input file path')
    parser.add_argument('outpath', nargs='?', help='output file path', default='-')
