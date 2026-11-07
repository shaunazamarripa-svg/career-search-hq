"""Career Search HQ: intentionally small, inspectable self-hosted backend."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Header, Response, Request
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DB_PATH = os.environ.get('DATABASE_PATH', str(ROOT / 'career-search.db'))
app = FastAPI(title='Career Search HQ', docs_url='/api/docs', openapi_url='/api/openapi.json')
ALLOWED_HOSTS=[x.strip() for x in os.environ.get('ALLOWED_HOSTS','localhost,127.0.0.1,testserver').split(',') if x.strip()]
app.add_middleware(TrustedHostMiddleware,allowed_hosts=ALLOWED_HOSTS)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
        if os.environ.get('SECURE_HTTPS','0')=='1':response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
        return response

app.add_middleware(SecurityHeadersMiddleware)

DEFAULT_WEIGHTS = {'title':25, 'pay':20, 'location':15, 'skills':15, 'leadership':15, 'freshness':10}
DEFAULT_SETTINGS = {'titles':[], 'min_salary':0, 'ideal_salary':0, 'work_modes':['remote','hybrid','onsite'], 'location':'', 'max_travel':100, 'exclusions':[], 'skills':[], 'greenhouse_boards':[], 'weights':DEFAULT_WEIGHTS}
VALID_STATES = ['discovered','verified','shortlisted','draft_ready','approval_pending','approved','submitted','interview','offer','rejected','withdrawn']
NEXT_STATES = {
    'discovered':{'verified','rejected','withdrawn'},
    'verified':{'shortlisted','rejected','withdrawn'},
    'shortlisted':{'draft_ready','rejected','withdrawn'},
    'draft_ready':{'approval_pending','rejected','withdrawn'},
    'approval_pending':{'approved','draft_ready','rejected','withdrawn'},
    'approved':{'submitted','draft_ready','withdrawn'},
    'submitted':{'interview','offer','rejected','withdrawn'},
    'interview':{'offer','rejected','withdrawn'},
    'offer':{'withdrawn'}, 'rejected':set(), 'withdrawn':set()
}

@contextmanager
def db():
    con=sqlite3.connect(DB_PATH,timeout=30)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:con.close()

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True,exist_ok=True)
    with db() as con:
        con.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,settings TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at REAL NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,company TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,canonical_url TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'manual',posted_at TEXT,discovered_at TEXT NOT NULL,salary_min INTEGER,salary_max INTEGER,work_mode TEXT,location TEXT,travel_percent INTEGER,description TEXT NOT NULL DEFAULT '',verified INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'discovered',score INTEGER NOT NULL DEFAULT 0,score_reason TEXT NOT NULL DEFAULT '',submitted_at TEXT,receipt TEXT,notes TEXT NOT NULL DEFAULT '',UNIQUE(user_id,canonical_url),FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,job_id INTEGER NOT NULL UNIQUE,resume TEXT NOT NULL DEFAULT '',cover_letter TEXT NOT NULL DEFAULT '',screening TEXT NOT NULL DEFAULT '{}',approved_at TEXT,FOREIGN KEY(user_id) REFERENCES users(id),FOREIGN KEY(job_id) REFERENCES jobs(id));
        CREATE TABLE IF NOT EXISTS outreach(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,job_id INTEGER NOT NULL,contact_name TEXT NOT NULL,profile_url TEXT NOT NULL,draft TEXT NOT NULL,approved_at TEXT,sent_at TEXT,evidence TEXT,FOREIGN KEY(user_id) REFERENCES users(id),FOREIGN KEY(job_id) REFERENCES jobs(id));
        CREATE TABLE IF NOT EXISTS followups(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,job_id INTEGER NOT NULL,due_at TEXT NOT NULL,kind TEXT NOT NULL,done_at TEXT,notes TEXT NOT NULL DEFAULT '',FOREIGN KEY(user_id) REFERENCES users(id),FOREIGN KEY(job_id) REFERENCES jobs(id));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,job_id INTEGER,kind TEXT NOT NULL,details TEXT NOT NULL,occurred_at TEXT NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS agent_runs(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,agent TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,created INTEGER NOT NULL DEFAULT 0,duplicates INTEGER NOT NULL DEFAULT 0,error TEXT,FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS auth_attempts(bucket TEXT PRIMARY KEY,window_start REAL NOT NULL,attempts INTEGER NOT NULL);
        ''')

init_db()

def now():return datetime.now(timezone.utc).isoformat()
def error(status,msg):raise HTTPException(status_code=status,detail=msg)
def asdict(row):return dict(row) if row else None
def json_settings(row):return json.loads(row['settings'])
def audit(con,uid,kind,job_id=None,**details):con.execute('INSERT INTO events(user_id,job_id,kind,details,occurred_at) VALUES(?,?,?,?,?)',(uid,job_id,kind,json.dumps(details),now()))
def hash_password(password,salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),350_000)
    return f'{salt}${digest.hex()}'
def verify_password(password,encoded):
    try:
        salt,digest=encoded.split('$',1)
        return hmac.compare_digest(hash_password(password,salt).split('$')[1],digest)
    except (ValueError,TypeError):return False
def token_for(con,uid):
    token=secrets.token_urlsafe(36)
    con.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,time.time()+60*60*24*7))
    return token
def throttle(con,key,limit=5,seconds=900):
    """Persistent per-account and per-IP sliding-window approximation."""
    current=time.time();entry=con.execute('SELECT * FROM auth_attempts WHERE bucket=?',(key,)).fetchone()
    if not entry or current-entry['window_start']>=seconds:
        con.execute('INSERT INTO auth_attempts(bucket,window_start,attempts) VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,attempts=1',(key,current))
        return
    if entry['attempts']>=limit:error(429,'Too many attempts. Try again later.')
    con.execute('UPDATE auth_attempts SET attempts=attempts+1 WHERE bucket=?',(key,))
def user_id(auth):
    if not auth or not auth.startswith('Bearer '):error(401,'Sign in required')
    token=auth[7:]
    with db() as con:
        row=con.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
    if not row:error(401,'Session expired')
    return row['user_id']
def own_job(con,uid,jid):
    row=con.execute('SELECT * FROM jobs WHERE id=? AND user_id=?',(jid,uid)).fetchone()
    if not row:error(404,'Job not found')
    return row
def canonical(url):
    p=urllib.parse.urlsplit(url.strip())
    if p.scheme not in ('https','http') or not p.netloc or p.username or p.password:error(400,'Valid employer/job URL required')
    host=(p.hostname or '').lower().removeprefix('www.')
    keep=[(k,v) for k,v in urllib.parse.parse_qsl(p.query) if k.lower() in ('gh_jid','jobid','job_id','lever-source')]
    return urllib.parse.urlunsplit(('https',host,p.path.rstrip('/') or '/',urllib.parse.urlencode(keep),''))

class Credentials(BaseModel):
    email:str
    password:str

@app.post('/api/auth/register')
def register(data:Credentials,request:Request):
    if os.environ.get('ALLOW_PUBLIC_SIGNUP','1')!='1':error(403,'Public registration is disabled until account verification is configured')
    email=data.email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(data.password)<12:error(400,'Valid email and password of at least 12 characters required')
    with db() as con:
        throttle(con,'register-ip:'+str(request.client.host),limit=10,seconds=3600)
        try:
            c=con.execute('INSERT INTO users(email,password_hash,settings,created_at) VALUES(?,?,?,?)',(email,hash_password(data.password),json.dumps(DEFAULT_SETTINGS),now()))
        except sqlite3.IntegrityError:error(409,'Email already registered')
        uid=c.lastrowid;token=token_for(con,uid);audit(con,uid,'registered')
    return {'token':token,'email':email}

@app.post('/api/auth/login')
def login(data:Credentials,request:Request):
    email=data.email.strip().lower()
    with db() as con:
        throttle(con,'login-email:'+hashlib.sha256(email.encode()).hexdigest(),limit=5)
        throttle(con,'login-ip:'+str(request.client.host),limit=30)
    with db() as con:
        row=con.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        verified=verify_password(data.password,row['password_hash'] if row else hash_password('not-the-real-password','00'*16))
        if not row or not verified:
            # The separate attempt counter transaction has already committed.
            invalid=True
        else:
            invalid=False
            con.execute('DELETE FROM auth_attempts WHERE bucket=?',('login-email:'+hashlib.sha256(email.encode()).hexdigest(),))
            token=token_for(con,row['id'])
    if invalid:error(401,'Invalid credentials')
    return {'token':token,'email':row['email']}

@app.post('/api/auth/logout')
def logout(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:con.execute('DELETE FROM sessions WHERE token_hash=? AND user_id=?',(hashlib.sha256(authorization[7:].encode()).hexdigest(),uid))
    return {'ok':True}

@app.post('/api/auth/revoke-all')
def revoke_all(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        con.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        audit(con,uid,'all_sessions_revoked')
    return {'ok':True}

class DeleteAccount(BaseModel):
    password:str
    confirm:str

@app.delete('/api/account')
def delete_account(data:DeleteAccount,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    if data.confirm!='DELETE MY ACCOUNT':error(400,'Exact confirmation required')
    with db() as con:
        row=con.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()
        if not verify_password(data.password,row['password_hash']):error(403,'Password incorrect')
        for table in ('sessions','followups','outreach','applications','agent_runs','events','jobs'):
            con.execute(f'DELETE FROM {table} WHERE user_id=?',(uid,))
        con.execute('DELETE FROM users WHERE id=?',(uid,))
    return {'deleted':True}

@app.get('/api/settings')
def get_settings(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:return json_settings(con.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone())

@app.put('/api/settings')
def set_settings(data:dict[str,Any],authorization:str|None=Header(None)):
    uid=user_id(authorization)
    allowed=set(DEFAULT_SETTINGS)
    if set(data)-allowed:error(400,'Unknown settings field')
    with db() as con:
        original=json_settings(con.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone())
        original.update(data)
        weights=original['weights']
        if set(weights)!=set(DEFAULT_WEIGHTS) or sum(weights.values())!=100 or any(type(v)!=int or v<0 for v in weights.values()):error(400,'Scoring weights must be nonnegative integers totaling 100')
        if not isinstance(original['titles'],list) or not isinstance(original['skills'],list):error(400,'Titles and skills must be lists')
        if not isinstance(original['greenhouse_boards'],list) or len(original['greenhouse_boards'])>30 or any(not isinstance(b,str) or not re.fullmatch(r'[a-zA-Z0-9_-]+',b) for b in original['greenhouse_boards']):error(400,'Invalid employer board list')
        if original['min_salary']<0 or not 0<=original['max_travel']<=100:error(400,'Invalid salary/travel settings')
        con.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(original),uid))
        audit(con,uid,'settings_updated')
        for row in con.execute('SELECT * FROM jobs WHERE user_id=?',(uid,)).fetchall():
            score,reason=score_job(row,original)
            con.execute('UPDATE jobs SET score=?,score_reason=? WHERE id=?',(score,reason,row['id']))
    return original

class JobInput(BaseModel):
    company:str=Field(min_length=1,max_length=200)
    title:str=Field(min_length=1,max_length=250)
    url:str
    source:str='manual'
    posted_at:str|None=None
    salary_min:int|None=None
    salary_max:int|None=None
    work_mode:str|None=None
    location:str|None=None
    travel_percent:int|None=None
    description:str=''
    notes:str=''
    verified:bool=False

def score_job(job,s):
    getter=lambda k:job[k] if isinstance(job,sqlite3.Row) else job.get(k)
    title=(getter('title') or '').lower();desc=(getter('description') or '').lower()
    reasons=[];hard=[];breakdown={}
    target=[str(x).lower() for x in s.get('titles',[]) if x]
    breakdown['title']=s['weights']['title'] if any(t in title or title in t for t in target) else (s['weights']['title']//3 if any(t.split()[0] in title for t in target) else 0)
    minimum=int(s.get('min_salary') or 0);salary=getter('salary_max') or getter('salary_min')
    if minimum and salary is not None and int(salary)<minimum:hard.append('Pay below minimum')
    breakdown['pay']=s['weights']['pay'] if salary and int(salary)>=int(s.get('ideal_salary') or minimum) else (s['weights']['pay']//2 if salary and int(salary)>=minimum else 0)
    mode=(getter('work_mode') or '').lower()
    modes=[str(x).lower() for x in s.get('work_modes',[])]
    if mode and mode not in modes:hard.append('Work arrangement excluded')
    requested_location=str(s.get('location') or '').strip().lower()
    actual_location=str(getter('location') or '').strip().lower()
    location_ok=(not requested_location or (actual_location and requested_location in actual_location) or mode=='remote')
    breakdown['location']=s['weights']['location'] if mode in modes and mode and location_ok else 0
    words=[str(x).lower() for x in s.get('skills',[]) if x]
    matched=sum(k in (title+' '+desc) for k in words)
    breakdown['skills']=round(s['weights']['skills']*(matched/len(words))) if words else 0
    breakdown['leadership']=s['weights']['leadership'] if any(k in title for k in ('manager','director','head','supervisor','vp','chief','lead')) else 0
    posted=getter('posted_at')
    fresh=0
    if posted:
        try:
            post_dt=datetime.fromisoformat(posted.replace('Z','+00:00'))
            if post_dt.tzinfo is None:post_dt=post_dt.replace(tzinfo=timezone.utc)
            days=(datetime.now(timezone.utc)-post_dt).days
            fresh=s['weights']['freshness'] if days<=7 else (s['weights']['freshness']//2 if days<=30 else 0)
        except (ValueError,TypeError):pass
    breakdown['freshness']=fresh
    travel=getter('travel_percent')
    if travel is not None and int(travel)>s.get('max_travel',100):hard.append('Travel exceeds maximum')
    for exclusion in s.get('exclusions',[]):
        if exclusion and str(exclusion).lower() in (title+' '+desc):hard.append('Excluded keyword: '+str(exclusion))
    value=sum(breakdown.values())
    reasons.append('Hard filter: '+', '.join(hard) if hard else 'Eligible under known hard filters')
    if not salary and minimum:reasons.append('Compensation unverified')
    if not mode:reasons.append('Work arrangement unverified')
    if requested_location and mode!='remote' and not actual_location:reasons.append('Job location unverified')
    if not posted:reasons.append('Posting date unverified')
    return (0 if hard else value),'; '.join(reasons)+' | '+json.dumps(breakdown)

def upsert_job(con,uid,data:dict):
    normalized=canonical(data['url'])
    row=con.execute('SELECT id FROM jobs WHERE user_id=? AND canonical_url=?',(uid,normalized)).fetchone()
    settings=json_settings(con.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone())
    score,reason=score_job(data,settings)
    if row:return {'id':row['id'],'created':False}
    cols=['user_id','company','title','url','canonical_url','source','posted_at','discovered_at','salary_min','salary_max','work_mode','location','travel_percent','description','verified','score','score_reason','notes']
    values=[uid,data['company'],data['title'],data['url'],normalized,data.get('source','manual'),data.get('posted_at'),now(),data.get('salary_min'),data.get('salary_max'),data.get('work_mode'),data.get('location'),data.get('travel_percent'),data.get('description',''),int(data.get('verified',False)),score,reason,data.get('notes','')]
    cur=con.execute(f"INSERT INTO jobs({','.join(cols)}) VALUES({','.join(['?']*len(cols))})",values)
    audit(con,uid,'job_discovered',cur.lastrowid,source=data.get('source','manual'),score=score)
    return {'id':cur.lastrowid,'created':True}

@app.post('/api/jobs')
def add_job(data:JobInput,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:return upsert_job(con,uid,data.model_dump())

@app.get('/api/jobs')
def jobs(status:str|None=None,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        q='SELECT * FROM jobs WHERE user_id=?';args=[uid]
        if status:q+=' AND status=?';args.append(status)
        q+=' ORDER BY score DESC, discovered_at DESC'
        return [dict(x) for x in con.execute(q,args)]

@app.get('/api/jobs/{jid}')
def get_job(jid:int,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        job=dict(own_job(con,uid,jid))
        job['application']=asdict(con.execute('SELECT * FROM applications WHERE user_id=? AND job_id=?',(uid,jid)).fetchone())
        job['outreach']=[dict(x) for x in con.execute('SELECT * FROM outreach WHERE user_id=? AND job_id=?',(uid,jid))]
        job['followups']=[dict(x) for x in con.execute('SELECT * FROM followups WHERE user_id=? AND job_id=?',(uid,jid))]
        return job

class Transition(BaseModel):
    status:str
    receipt:str|None=None

@app.post('/api/jobs/{jid}/status')
def change_status(jid:int,data:Transition,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        job=own_job(con,uid,jid)
        if data.status not in NEXT_STATES.get(job['status'],set()):error(409,f"Cannot move from {job['status']} to {data.status}")
        if data.status=='verified' and not job['url']:error(400,'Job URL missing')
        if data.status=='submitted':
            a=con.execute('SELECT approved_at FROM applications WHERE user_id=? AND job_id=?',(uid,jid)).fetchone()
            if not a or not a['approved_at']:error(409,'Approve application first')
            if not data.receipt or len(data.receipt.strip())<6:error(400,'Verified submission receipt or application ID required')
        con.execute('UPDATE jobs SET status=?,verified=?,submitted_at=CASE WHEN ?="submitted" THEN ? ELSE submitted_at END,receipt=COALESCE(?,receipt) WHERE id=?',(data.status,1 if data.status!='discovered' else job['verified'],data.status,now(),data.receipt,jid))
        audit(con,uid,'status_changed',jid,from_status=job['status'],to_status=data.status,receipt_provided=bool(data.receipt))
    return {'ok':True,'status':data.status}

class ApplicationDraft(BaseModel):
    resume:str=''
    cover_letter:str=''
    screening:dict[str,Any]={}
    approve:bool=False

@app.post('/api/jobs/{jid}/application')
def application(jid:int,data:ApplicationDraft,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        job=own_job(con,uid,jid)
        if data.approve and not data.resume.strip():error(400,'Resume reference required for approval')
        if data.approve and job['status'] in ('discovered','rejected','withdrawn'):error(409,'Job must be qualified before approval')
        existing=con.execute('SELECT * FROM applications WHERE user_id=? AND job_id=?',(uid,jid)).fetchone()
        if existing and existing['approved_at'] and (data.resume!=existing['resume'] or data.cover_letter!=existing['cover_letter'] or json.dumps(data.screening,sort_keys=True)!=json.dumps(json.loads(existing['screening']),sort_keys=True)):
            # A changed application is a new unapproved draft.
            data.approve=False
        approved=now() if data.approve else None
        con.execute('INSERT INTO applications(user_id,job_id,resume,cover_letter,screening,approved_at) VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET resume=excluded.resume,cover_letter=excluded.cover_letter,screening=excluded.screening,approved_at=excluded.approved_at',(uid,jid,data.resume,data.cover_letter,json.dumps(data.screening),approved))
        if data.approve and job['status'] not in ('submitted','interview','offer'):
            con.execute('UPDATE jobs SET status="approved" WHERE id=?',(jid,))
        audit(con,uid,'application_approved' if data.approve else 'application_drafted',jid)
    return {'approved':data.approve,'submitted':False,'message':'Approval never sends automatically; record a verified receipt after a supported submission.'}

class OutreachDraft(BaseModel):
    contact_name:str
    profile_url:str
    draft:str
    approve:bool=False
    outreach_id:int|None=None

@app.post('/api/jobs/{jid}/outreach')
def outreach(jid:int,data:OutreachDraft,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        job=own_job(con,uid,jid)
        if job['status'] not in ('submitted','interview','offer'):error(409,'Recruiter outreach is unlocked only after confirmed submission')
        if not data.contact_name.strip() or not data.draft.strip():error(400,'Contact and draft required')
        canonical(data.profile_url)
        if data.outreach_id:
            previous=con.execute('SELECT * FROM outreach WHERE id=? AND user_id=? AND job_id=?',(data.outreach_id,uid,jid)).fetchone()
            if not previous:error(404,'Outreach draft not found')
            con.execute('UPDATE outreach SET contact_name=?,profile_url=?,draft=?,approved_at=?,sent_at=NULL,evidence=NULL WHERE id=?',(data.contact_name,data.profile_url,data.draft,now() if data.approve else None,data.outreach_id));oid=data.outreach_id
        else:
            cur=con.execute('INSERT INTO outreach(user_id,job_id,contact_name,profile_url,draft,approved_at) VALUES(?,?,?,?,?,?)',(uid,jid,data.contact_name,data.profile_url,data.draft,now() if data.approve else None));oid=cur.lastrowid
        audit(con,uid,'outreach_approved' if data.approve else 'outreach_drafted',jid,outreach_id=oid)
    return {'id':oid,'approved':data.approve,'sent':False}

class SentEvidence(BaseModel):
    evidence:str

@app.post('/api/outreach/{oid}/mark-sent')
def mark_sent(oid:int,data:SentEvidence,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    if len(data.evidence.strip())<6:error(400,'Message link or evidence required')
    with db() as con:
        row=con.execute('SELECT * FROM outreach WHERE id=? AND user_id=?',(oid,uid)).fetchone()
        if not row:error(404,'Message not found')
        if not row['approved_at']:error(409,'Approve outreach first')
        if row['sent_at']:error(409,'Already recorded as sent')
        con.execute('UPDATE outreach SET sent_at=?,evidence=? WHERE id=?',(now(),data.evidence,oid))
        audit(con,uid,'outreach_sent_confirmed',row['job_id'],outreach_id=oid)
    return {'ok':True}

class Followup(BaseModel):
    job_id:int
    kind:str='application'
    due_at:str
    notes:str=''

@app.post('/api/followups')
def add_followup(data:Followup,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    try:datetime.fromisoformat(data.due_at.replace('Z','+00:00'))
    except ValueError:error(400,'Invalid ISO date')
    with db() as con:
        own_job(con,uid,data.job_id)
        cur=con.execute('INSERT INTO followups(user_id,job_id,kind,due_at,notes) VALUES(?,?,?,?,?)',(uid,data.job_id,data.kind,data.due_at,data.notes))
        audit(con,uid,'followup_scheduled',data.job_id,followup_id=cur.lastrowid)
        return {'id':cur.lastrowid}

@app.get('/api/followups')
def followups(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:return [dict(x) for x in con.execute('SELECT f.*,j.title,j.company FROM followups f JOIN jobs j ON j.id=f.job_id WHERE f.user_id=? AND f.done_at IS NULL ORDER BY f.due_at',(uid,))]

@app.post('/api/followups/{fid}/done')
def complete_followup(fid:int,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        row=con.execute('SELECT * FROM followups WHERE id=? AND user_id=?',(fid,uid)).fetchone()
        if not row:error(404,'Follow-up not found')
        con.execute('UPDATE followups SET done_at=? WHERE id=?',(now(),fid))
        audit(con,uid,'followup_done',row['job_id'],followup_id=fid)
    return {'ok':True}

@app.get('/api/metrics')
def metrics(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:
        states={row['status']:row['n'] for row in con.execute('SELECT status,COUNT(*) n FROM jobs WHERE user_id=? GROUP BY status',(uid,))}
        n=lambda query:con.execute(query,(uid,)).fetchone()[0]
        total=sum(states.values())
        submitted=n("SELECT COUNT(*) FROM jobs WHERE user_id=? AND submitted_at IS NOT NULL")
        interview=n("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN ('interview','offer')")
        approved=n("SELECT COUNT(*) FROM applications WHERE user_id=? AND approved_at IS NOT NULL")
        outreach_sent=n("SELECT COUNT(*) FROM outreach WHERE user_id=? AND sent_at IS NOT NULL")
        overdue=con.execute('SELECT COUNT(*) FROM followups WHERE user_id=? AND done_at IS NULL AND due_at<?',(uid,now())).fetchone()[0]
        return {'total':total,'states':states,'submitted':submitted,'interviews':interview,'approved_applications':approved,'outreach_sent':outreach_sent,'overdue_followups':overdue,'interview_conversion':round(interview/submitted*100,1) if submitted else 0,'generated_at':now()}

@app.get('/api/events')
def events(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:return [dict(x) for x in con.execute('SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT 200',(uid,))]

class CsvInput(BaseModel):
    csv_text:str

@app.post('/api/import/csv')
def import_csv(data:CsvInput,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    reader=csv.DictReader(io.StringIO(data.csv_text))
    if not reader.fieldnames or not {'company','title','url'}<=set(reader.fieldnames):error(400,'CSV requires company,title,url headers')
    rows=list(reader)
    if len(rows)>1000:error(400,'Maximum 1000 jobs per import')
    created=0;duplicates=0;errors=[]
    with db() as con:
        for idx,row in enumerate(rows,2):
            try:
                parsed=JobInput(**{k:v for k,v in row.items() if k in JobInput.model_fields and v not in (None,'')}).model_dump()
                result=upsert_job(con,uid,parsed)
                created+=result['created'];duplicates+=not result['created']
            except Exception as e:
                errors.append({'line':idx,'error':str(e)[:150]})
    return {'created':created,'duplicates':duplicates,'errors':errors}

@app.get('/api/export/csv')
def export_csv(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:rows=[dict(x) for x in con.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY id',(uid,))]
    fields=['company','title','url','source','posted_at','salary_min','salary_max','work_mode','location','status','score','score_reason','submitted_at','receipt','notes']
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    return PlainTextResponse(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="career-search-hq.csv"'})

class BoardInput(BaseModel):
    board:str=Field(min_length=1,max_length=100)

def scan_greenhouse(con,uid,board):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',board):error(400,'Invalid board identifier')
    url=f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true'
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'CareerSearchHQ/0.1'})
        with urllib.request.urlopen(req,timeout=12) as response:
            raw=response.read(4_000_001)
            if len(raw)>4_000_000:error(400,'Board feed too large')
            payload=json.loads(raw)
    except Exception:error(502,'Board unavailable or returned invalid data')
    created=0;duplicates=0
    for item in payload.get('jobs',[])[:500]:
        try:
            fields={'company':board,'title':item['title'],'url':item['absolute_url'],'source':'greenhouse:'+board,'posted_at':item.get('updated_at'),'location':item.get('location',{}).get('name'),'description':re.sub('<[^>]+>',' ',item.get('content',''))[:15000]}
            res=upsert_job(con,uid,fields)
            created+=res['created'];duplicates+=not res['created']
        except Exception:continue
    return {'created':created,'duplicates':duplicates,'source':url}

@app.post('/api/discovery/greenhouse')
def discover_greenhouse(data:BoardInput,authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:return scan_greenhouse(con,uid,data.board)

def run_discovery_for_user(uid):
    results=[]
    with db() as con:
        row=con.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()
        if not row:return []
        boards=json_settings(row).get('greenhouse_boards',[])
    for board in boards:
        with db() as con:
            run=con.execute('INSERT INTO agent_runs(user_id,agent,started_at) VALUES(?,?,?)',(uid,'greenhouse_discovery',now()))
            rid=run.lastrowid
        try:
            with db() as con:
                result=scan_greenhouse(con,uid,board)
                con.execute('UPDATE agent_runs SET finished_at=?,created=?,duplicates=? WHERE id=?',(now(),result['created'],result['duplicates'],rid))
                audit(con,uid,'agent_scan_completed',board=board,created=result['created'])
            results.append({'board':board,**result})
        except Exception as exc:
            with db() as con:con.execute('UPDATE agent_runs SET finished_at=?,error=? WHERE id=?',(now(),str(exc)[:200],rid))
            results.append({'board':board,'error':str(exc)[:200]})
    return results

@app.post('/api/discovery/run')
def discovery_run(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    return {'results':run_discovery_for_user(uid)}

@app.get('/api/agents/runs')
def agent_runs(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    with db() as con:return [dict(x) for x in con.execute('SELECT * FROM agent_runs WHERE user_id=? ORDER BY id DESC LIMIT 100',(uid,))]

@app.get('/api/weekly-review')
def weekly_review(authorization:str|None=Header(None)):
    uid=user_id(authorization)
    cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    with db() as con:
        events=[dict(x) for x in con.execute('SELECT * FROM events WHERE user_id=? AND occurred_at>=?',(uid,cutoff))]
        high=con.execute('SELECT COUNT(*) FROM jobs WHERE user_id=? AND score>=85',(uid,)).fetchone()[0]
        weak=con.execute('SELECT COUNT(*) FROM jobs WHERE user_id=? AND score<55',(uid,)).fetchone()[0]
    counts={}
    for e in events:counts[e['kind']]=counts.get(e['kind'],0)+1
    recommendations=[]
    if counts.get('job_discovered',0)<10:recommendations.append('Add more verified company sources or broaden title synonyms.')
    if counts.get('application_approved',0)<3:recommendations.append('Review the highest-ranked eligible applications; do not lower hard filters automatically.')
    if counts.get('outreach_sent_confirmed',0)==0:recommendations.append('Find relevant recruiters after confirmed submissions and review outreach drafts.')
    return {'period_start':cutoff,'activity':counts,'high_score_jobs':high,'weak_score_jobs':weak,'recommendations':recommendations,'policy':'Suggestions only: no automatic edits to minimum compensation, location, or approval policies.'}

@app.get('/')
def index():return FileResponse(ROOT/'static'/'index.html')

@app.get('/health')
def health():return {'status':'ok'}

@app.get('/static/{name}')
def static(name:str):
    if name not in ('app.js','styles.css'):error(404,'Not found')
    return FileResponse(ROOT/'static'/name)
