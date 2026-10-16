"""Separate, least-privileged network reader. No writes to the closure feed/DB."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
from monitoring import read_cache, merge


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect rejected')


def validate_url(url):
    u=urlsplit(url)
    if (u.scheme!='https' or u.netloc!='www.waze.com' or u.fragment or u.query!='format=1'
            or not re.fullmatch(r'/(?:row-)?partnerhub-api/partners/[0-9]+/waze-feeds/[a-f0-9-]{36}',u.path)):
        raise ValueError('Invalid feed URL')
    return url


def fetch(url):
    request=urllib.request.Request(validate_url(url),headers={'User-Agent':'Via-Municipal-Monitor/1.0','Accept':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
        raw=response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:
            raise ValueError('Feed too large')
        return json.loads(raw)


def atomic(path,data):
    fd,name=tempfile.mkstemp(prefix='.monitor-',dir=str(path.parent))
    try:
        os.chmod(name,0o640)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def collect(secret,output,city,now=None,fetcher=fetch):
    now=time.time() if now is None else now
    previous=read_cache(output)
    try:
        url=validate_url(Path(secret).read_text(encoding='utf-8').strip())
        result=merge(previous,fetcher(url),city['bounds'],now)
        ok=True
    except Exception:
        # Neither exceptions nor URLs go to logs/cache: the URL carries a token.
        result={**previous,'meta':{**previous['meta'],'lastAttempt':now,'error':'fetch_failed'}}
        ok=False
    atomic(Path(output),result)
    return ok


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--secret',default='/etc/waze-monitor/feed.url')
    parser.add_argument('--output',default='/var/lib/waze-monitor/current.json')
    parser.add_argument('--city',default='/opt/waze-monitor/city.json')
    args=parser.parse_args()
    city=json.loads(Path(args.city).read_text(encoding='utf-8'))
    ok=collect(args.secret,args.output,city)
    print('Waze observations refreshed' if ok else 'Waze unavailable; last observations preserved')
    raise SystemExit(0 if ok else 1)


if __name__=='__main__':main()
