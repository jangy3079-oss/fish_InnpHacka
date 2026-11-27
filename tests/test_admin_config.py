import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from api.main import app
from api.admin_config import get_config_editor
from api.farmer_data import get_farmer_http_client
from common.config_loader import ConfigLoader,DEFAULT_DIRECTORY
from common.config_editor import ConfigEditor


class AdminConfigApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'config'; shutil.copytree(DEFAULT_DIRECTORY,self.root)
        self.editor=ConfigEditor(ConfigLoader(self.root))
        self.user={'id':'00000000-0000-4000-8000-000000000001','app_metadata':{'role':'admin'}}
        self.upstream_status=200; self.auth_requests=[]
        def auth(request):
            self.auth_requests.append(request)
            self.assertEqual(request.url.path,'/auth/v1/user')
            return httpx.Response(self.upstream_status,json=self.user)
        client=httpx.Client(transport=httpx.MockTransport(auth)); self.addCleanup(client.close)
        env=patch.dict(os.environ,{'SUPABASE_URL':'https://test.supabase.co','SUPABASE_PUBLISHABLE_KEY':'public-key'})
        env.start(); self.addCleanup(env.stop)
        app.dependency_overrides[get_farmer_http_client]=lambda:client
        app.dependency_overrides[get_config_editor]=lambda:self.editor
        self.addCleanup(app.dependency_overrides.clear)
        self.client=TestClient(app); self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer administrator-token'}

    def payload(self):
        response=self.client.get('/admin/config/thresholds',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        view=response.json()
        values=copy.deepcopy(view['values']); values['release_hours']=7
        return {'values':values,'expected_revision':view['revision'],'request_key':str(uuid4()),'reason':'운영 검토에 따른 시험 변경'}

    def test_admin_read_update_reload_etag_and_replay(self):
        payload=self.payload()
        response=self.client.put('/admin/config/thresholds',headers=self.headers,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['persisted'])
        self.assertTrue(response.json()['changed'])
        self.assertEqual(response.headers['etag'],'"'+response.json()['revision']+'"')
        self.assertEqual(response.headers['x-data-mode'],'configuration')
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertIn('Authorization',response.headers['vary'])
        self.assertEqual(response.json(),self.client.put('/admin/config/thresholds',headers=self.headers,json=payload).json())
        view=self.client.get('/admin/config/thresholds',headers=self.headers).json()
        self.assertEqual(view['values']['release_hours'],7)
        self.assertEqual(view['revision'],response.json()['revision'])
        self.assertTrue(all(r.headers['Authorization']=='Bearer administrator-token' for r in self.auth_requests))

    def test_missing_token_and_expired_token_are_rejected(self):
        self.assertEqual(self.client.get('/admin/config/thresholds').status_code,401)
        self.assertEqual(self.client.put('/admin/config/thresholds',json={}).status_code,401)
        self.assertFalse(self.auth_requests)
        self.upstream_status=401
        self.assertEqual(self.client.get('/admin/config/thresholds',headers=self.headers).status_code,401)
        self.assertFalse((self.root/'.admin-config.sqlite3').exists())

    def test_farmer_official_edge_analysis_and_user_metadata_cannot_administer(self):
        for role in ('farmer','official','edge','analysis',None):
            self.user['app_metadata']={'role':role}; self.user['user_metadata']={'role':'admin'}
            with self.subTest(role=role):
                self.assertEqual(self.client.get('/admin/config/thresholds',headers=self.headers).status_code,403)
                self.assertEqual(self.client.put('/admin/config/thresholds',headers=self.headers,json={}).status_code,403)
        self.assertFalse((self.root/'.admin-config.sqlite3').exists())

    def test_anonymous_or_malformed_user_cannot_administer(self):
        self.user['is_anonymous']=True
        self.assertEqual(self.client.get('/admin/config/costs',headers=self.headers).status_code,401)
        self.user={'id':'not-a-uuid','app_metadata':{'role':'admin'}}
        self.assertEqual(self.client.get('/admin/config/costs',headers=self.headers).status_code,401)

    def test_invalid_fields_and_payloads_never_reflect_values(self):
        payload=self.payload()
        for change in ({'values':{}},{'expected_revision':'bad-secret'}, {'request_key':'bad-secret'},
                       {'reason':'  '},{'unknown':'private-secret'}):
            data={**payload,**change}
            result=self.client.put('/admin/config/thresholds',headers=self.headers,json=data)
            self.assertEqual(result.status_code,422,result.text)
            self.assertNotIn('secret',result.text)
        result=self.client.put('/admin/config/thresholds',headers=self.headers,json={**payload,'values':{'private-secret':1}})
        self.assertEqual(result.status_code,422)
        self.assertNotIn('private-secret',result.text)
        self.assertEqual(self.client.get('/admin/config/unknown',headers=self.headers).status_code,422)

    def test_stale_revision_and_reused_key_have_conflict_status(self):
        payload=self.payload()
        self.assertEqual(self.client.put('/admin/config/thresholds',headers=self.headers,json=payload).status_code,200)
        changed=copy.deepcopy(payload); changed['values']['release_hours']=8
        self.assertEqual(self.client.put('/admin/config/thresholds',headers=self.headers,json=changed).status_code,409)
        changed['request_key']=str(uuid4())
        self.assertEqual(self.client.put('/admin/config/thresholds',headers=self.headers,json=changed).status_code,409)

    def test_storage_failure_is_sanitized_and_identical_request_can_recover(self):
        payload=self.payload()
        with patch.object(self.editor,'atomic_replace',side_effect=OSError('private-path credential details')):
            result=self.client.put('/admin/config/thresholds',headers=self.headers,json=payload)
        self.assertEqual(result.status_code,503)
        self.assertNotIn('private-path',result.text)
        self.assertEqual(self.client.put('/admin/config/thresholds',headers=self.headers,json=payload).status_code,200)


if __name__=='__main__': unittest.main()
