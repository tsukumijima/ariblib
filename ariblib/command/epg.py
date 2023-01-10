import json
import sys
from collections import defaultdict

from ariblib import tsopen
from ariblib.event import Event
from ariblib.sections import EventInformationSection


def dump_json(event):
    return json.dumps({
        "start_time": str(event.start_time),
        "duration": event.duration.total_seconds(),
        "title": str(event.title),
        "desc": str(event.desc),
        "genre": event.genre,
        "subgenre": event.subgenre,
        "user_genre": event.user_genre,
    }, ensure_ascii=False)

def extract_epg(ts, threshold=5):
    count = defaultdict(int)
    programs = {}

    for section in ts.sections(EventInformationSection):
        # section_number can be 0 (current program), 1 (next program)
        # or >=2 (programs after next). We only care about current
        # program information.
        if section.section_number != 0:
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

def epg(args):
    if args.outpath == '-':
        outpath = sys.stdout.fileno()
    else:
        outpath = args.outpath

    with tsopen(args.inpath) as ts:
        with open(outpath, mode='w', encoding='utf-8') as fw:
            for epg in extract_epg(ts):
                print(epg, file=fw)

def add_parser(parsers):
    parser = parsers.add_parser('epg')
    parser.set_defaults(command=epg)
    parser.add_argument('inpath', help='input file path')
    parser.add_argument('outpath', nargs='?', help='output file path', default='-')
