"""Run: python -m unittest discover -s tests -v"""
import importlib
import os
import tempfile
import unittest
from fastapi.testclient import TestClient

class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        os.environ['DATABASE_PATH']=os.path.join(cls.temp.name,'test.db')
        cls.mod=importlib.import_module('app')
        cls.client=TestClient(cls.mod.app)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def send(self,method,path,data=None,token=None):
        return self.client.request(method,path,json=data,headers={'Authorization':'Bearer '+token} if token else {})

    def test_end_to_end_and_isolation(self):
        a=self.send('POST','/api/auth/register',{'email':'a@example.com','password':'verylongpassword1'})
        b=self.send('POST','/api/auth/register',{'email':'b@example.com','password':'verylongpassword2'})
        self.assertEqual(a.status_code,200)
        ta=a.json()['token'];tb=b.json()['token']
        s=self.send('PUT','/api/settings',{'titles':['Warehouse Manager'],'min_salary':75000,'ideal_salary':100000,'work_modes':['onsite'],'max_travel':25},ta)
        self.assertEqual(s.status_code,200)
        add=self.send('POST','/api/jobs',{'company':'Test Logistics','title':'Warehouse Manager','url':'https://jobs.example.org/role/23?utm_source=foo','salary_max':105000,'work_mode':'onsite','posted_at':'2026-11-07','description':'Manage warehouse staffing and KPIs'},ta)
        self.assertEqual(add.status_code,200,add.text)
        jid=add.json()['id']
        self.assertTrue(add.json()['created'])
        dup=self.send('POST','/api/jobs',{'company':'Test Logistics','title':'Warehouse Manager','url':'http://www.jobs.example.org/role/23?utm_campaign=x'},ta)
        self.assertFalse(dup.json()['created'])
        self.assertEqual(self.send('GET',f'/api/jobs/{jid}',token=tb).status_code,404)
        self.assertEqual(self.send('GET','/api/jobs',token=tb).json(),[])
        self.assertGreater(self.send('GET','/api/jobs',token=ta).json()[0]['score'],0)
        self.assertEqual(self.send('POST',f'/api/jobs/{jid}/status',{'status':'submitted','receipt':'test receipt'},ta).status_code,409)
        self.assertEqual(self.send('POST',f'/api/jobs/{jid}/outreach',{'contact_name':'Recruiter','profile_url':'https://linkedin.com/in/recruiter','draft':'Hello'},ta).status_code,409)
        for state in ['verified','shortlisted','draft_ready','approval_pending']:
            response=self.send('POST',f'/api/jobs/{jid}/status',{'status':state},ta)
            self.assertEqual(response.status_code,200,response.text)
        packet=self.send('POST',f'/api/jobs/{jid}/application',{'resume':'https://example.com/resume','approve':True},ta)
        self.assertTrue(packet.json()['approved']);self.assertFalse(packet.json()['submitted'])
        self.assertEqual(self.send('POST',f'/api/jobs/{jid}/status',{'status':'submitted'},ta).status_code,400)
        self.assertEqual(self.send('POST',f'/api/jobs/{jid}/status',{'status':'submitted','receipt':'ATS-0123456'},ta).status_code,200)
        message=self.send('POST',f'/api/jobs/{jid}/outreach',{'contact_name':'Jane Recruiter','profile_url':'https://www.linkedin.com/in/jane','draft':'Hi Jane, I applied and wanted to introduce myself.','approve':True},ta)
        self.assertTrue(message.json()['approved']);self.assertFalse(message.json()['sent'])
        oid=message.json()['id']
        self.assertEqual(self.send('POST',f'/api/outreach/{oid}/mark-sent',{'evidence':'LinkedIn thread #1'},tb).status_code,404)
        self.assertEqual(self.send('POST',f'/api/outreach/{oid}/mark-sent',{'evidence':'LinkedIn thread #1'},ta).status_code,200)
        f=self.send('POST','/api/followups',{'job_id':jid,'due_at':'2026-11-15','kind':'recruiter'},ta)
        self.assertEqual(f.status_code,200)
        self.assertEqual(len(self.send('GET','/api/followups',token=ta).json()),1)
        self.assertEqual(self.send('POST',f"/api/followups/{f.json()['id']}/done",token=ta).status_code,200)
        self.assertEqual(len(self.send('GET','/api/followups',token=ta).json()),0)
        self.assertEqual(self.send('GET','/api/metrics',token=ta).json()['submitted'],1)
        self.assertTrue(self.send('GET','/api/events',token=ta).json())
        self.assertEqual(self.send('GET','/api/metrics',token=tb).json()['total'],0)

    def test_hard_filters_and_csv(self):
        t=self.send('POST','/api/auth/register',{'email':'c@example.com','password':'verylongpassword3'}).json()['token']
        self.send('PUT','/api/settings',{'min_salary':150000,'work_modes':['remote'],'exclusions':['commission only']},t)
        r=self.send('POST','/api/jobs',{'company':'Sales Inc','title':'Sales Manager','url':'https://sales.example.com/job/1','salary_max':70000,'work_mode':'onsite','description':'commission only'},t)
        job=self.send('GET',f"/api/jobs/{r.json()['id']}",token=t).json()
        self.assertEqual(job['score'],0)
        result=self.send('POST','/api/import/csv',{'csv_text':'company,title,url\nABC,Director,https://abc.example.com/a\nABC,Director,https://abc.example.com/a'},t)
        self.assertEqual(result.json()['created'],1)
        self.assertEqual(result.json()['duplicates'],1)
        self.assertIn('company,title,url',self.send('GET','/api/export/csv',token=t).text)

    def test_security_and_account_lifecycle(self):
        email='security@example.com';password='this-is-a-strong-password'
        reg=self.send('POST','/api/auth/register',{'email':email,'password':password})
        self.assertEqual(reg.status_code,200)
        token=reg.json()['token']
        self.assertEqual(reg.headers['x-content-type-options'],'nosniff')
        self.assertIn("script-src 'self'",reg.headers['content-security-policy'])
        self.assertEqual(reg.headers['x-frame-options'],'DENY')
        for _ in range(5):
            r=self.send('POST','/api/auth/login',{'email':email,'password':'wrong-password'})
            self.assertEqual(r.status_code,401,r.text)
        r=self.send('POST','/api/auth/login',{'email':email,'password':password})
        self.assertEqual(r.status_code,429,'A correct password must not bypass rate limiting')
        self.assertEqual(self.send('DELETE','/api/account',{'password':password,'confirm':'not yet'},token).status_code,400)
        self.assertEqual(self.send('DELETE','/api/account',{'password':password,'confirm':'DELETE MY ACCOUNT'},token).status_code,200)
        self.assertEqual(self.send('GET','/api/settings',token=token).status_code,401)

    def test_pilot_signup_switch_and_untrusted_host(self):
        os.environ['ALLOW_PUBLIC_SIGNUP']='0'
        try:
            r=self.send('POST','/api/auth/register',{'email':'blocked@example.com','password':'sufficient-password'})
            self.assertEqual(r.status_code,403)
        finally:
            os.environ.pop('ALLOW_PUBLIC_SIGNUP',None)
        result=self.client.get('/health',headers={'host':'malicious.example'})
        self.assertEqual(result.status_code,400)

if __name__=='__main__':unittest.main()
