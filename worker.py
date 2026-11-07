"""Daily scheduler for opt-in employer board discovery."""
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from agent import run_all

def next_run(tz_name,hour):
    tz=ZoneInfo(tz_name)
    current=datetime.now(tz)
    scheduled=current.replace(hour=hour,minute=0,second=0,microsecond=0)
    if scheduled<=current:scheduled+=timedelta(days=1)
    return scheduled

if __name__=='__main__':
    tz_name=os.environ.get('WORKER_TZ','America/Chicago')
    hour=int(os.environ.get('WORKER_HOUR','8'))
    if not 0<=hour<=23:raise ValueError('WORKER_HOUR must be 0-23')
    while True:
        scheduled=next_run(tz_name,hour)
        delay=max(1,(scheduled-datetime.now(ZoneInfo(tz_name))).total_seconds())
        print('Next discovery scan:',scheduled.isoformat(),flush=True)
        time.sleep(delay)
        try:print('Scan completed:',run_all(),flush=True)
        except Exception as exc:print('Scan error:',type(exc).__name__,str(exc)[:200],flush=True)
