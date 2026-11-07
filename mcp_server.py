"""Local MCP bridge to a user's authenticated Career Search HQ account.

Set HQ_URL (default http://127.0.0.1:8080) and HQ_TOKEN environment variables.
Tokens must never appear in source control or chat logs.
"""
import json
import os
import urllib.error
import urllib.request
from mcp.server.fastmcp import FastMCP

server=FastMCP('career-search-hq')
base=os.environ.get('HQ_URL','http://127.0.0.1:8080').rstrip('/')
token=os.environ.get('HQ_TOKEN')

def request(route,method='GET',data=None):
    if not token:raise RuntimeError('Set HQ_TOKEN to a valid personal session token')
    payload=json.dumps(data).encode() if data is not None else None
    req=urllib.request.Request(base+'/api'+route,data=payload,method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=30) as res:return json.loads(res.read())
    except urllib.error.HTTPError as err:
        raise RuntimeError(f'API returned {err.code}: {err.read().decode()[:250]}') from err

@server.tool()
def list_opportunities():
    """Read this user's current jobs, scores, states and evidence."""
    return request('/jobs')

@server.tool()
def get_search_criteria():
    """Read current titles, salary thresholds, work modes and scoring weights."""
    return request('/settings')

@server.tool()
def add_opportunity(company:str,title:str,url:str,source:str,description:str='',work_mode:str='',location:str=''):
    """Add a real job posting for deduplication and scoring. Never invent URL."""
    return request('/jobs','POST',{'company':company,'title':title,'url':url,'source':source,'description':description,'work_mode':work_mode or None,'location':location or None})

@server.tool()
def get_weekly_scorecard():
    """Summarize last seven days of activity with recommendations."""
    return request('/weekly-review')

@server.tool()
def list_due_followups():
    """Read incomplete follow-ups; do not falsely claim a message was sent."""
    return request('/followups')

@server.tool()
def draft_application(job_id:int,resume_reference:str,cover_letter_reference:str='',screening_answers_json:str='{}'):
    """Save a draft. This does not approve or submit an application."""
    return request(f'/jobs/{job_id}/application','POST',{'resume':resume_reference,'cover_letter':cover_letter_reference,'screening':json.loads(screening_answers_json),'approve':False})

@server.tool()
def draft_recruiter_outreach(job_id:int,contact_name:str,profile_url:str,message:str):
    """Save a recruiter message draft only after a confirmed application. Never send."""
    return request(f'/jobs/{job_id}/outreach','POST',{'contact_name':contact_name,'profile_url':profile_url,'draft':message,'approve':False})

if __name__=='__main__':server.run(transport='stdio')
