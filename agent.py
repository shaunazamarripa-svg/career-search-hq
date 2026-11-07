"""Unattended discovery. Run daily via host cron or container scheduler.

No application is submitted and no recruiter is contacted by this worker.
"""
from app import db,run_discovery_for_user

def run_all():
    with db() as con:
        ids=[r['id'] for r in con.execute('SELECT id FROM users')]
    results={}
    for uid in ids:results[uid]=run_discovery_for_user(uid)
    return results

if __name__=='__main__':
    result=run_all()
    print('Discovery cycles:',len(result),'accounts; scans:',sum(len(v) for v in result.values()))
