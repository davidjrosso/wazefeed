"""Independent Traffic View collector; credentials remain in a private file."""
import argparse
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit
from collect_waze import NoRedirect
from traffic_store import record


def validate_url(url):
    u = urlsplit(url)
    if (u.scheme!='https' or u.netloc!='www.waze.com' or u.fragment
        or not re.fullmatch(r'id=[0-9]+',u.query)
        or not re.fullmatch(r'/(?:row-)?partnerhub-api/feeds-tvt/[a-f0-9-]{36}',u.path)):
        raise ValueError('Invalid Traffic View URL')
    return url


def fetch(url):
    request = urllib.request.Request(validate_url(url),headers={'Accept':'application/json','User-Agent':'Via-Municipal-Monitor/1.0'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
        raw = response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:
            raise ValueError('Oversized feed')
        return json.loads(raw)


def collect(secret, database, fetcher=fetch, now=None):
    if Path(secret).resolve()==Path(database).resolve():
        raise ValueError('Archive must be separate from credentials')
    try:
        payload = fetcher(validate_url(Path(secret).read_text(encoding='utf-8').strip()))
    except Exception:
        return record(database,None,now,failed=True)
    return record(database,payload,now)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--secret',default='/etc/waze-monitor/traffic.url')
    parser.add_argument('--database',default='/var/lib/waze-monitor/traffic.sqlite3')
    args = parser.parse_args()
    try:
        ok = collect(args.secret,args.database)
    except Exception:
        ok = False
    print('Traffic View archived' if ok else 'Traffic View collection failed; check archive status')
    raise SystemExit(0 if ok else 1)
