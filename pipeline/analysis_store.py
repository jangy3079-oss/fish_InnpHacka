"""Narrow analysis-worker RPC client; user/edge tokens cannot publish results."""

import os
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5
import json

import httpx

from api.farmer_data import SupabaseSettings


class AnalysisError(RuntimeError):
    pass


class AnalysisStore:
    def __init__(self, settings, token, client):
        self.settings,self.token,self.client = settings,token,client

    @classmethod
    def from_env(cls, client):
        token = os.getenv('SUPABASE_ANALYSIS_TOKEN','')
        if not token:
            raise AnalysisError('A dedicated analysis-worker access token is required')
        return cls(SupabaseSettings.from_env(),token,client)

    def call(self, method, path, payload=None):
        try:
            response = self.client.request(method,self.settings.url+path,
                headers={'apikey':self.settings.key,'Authorization':f'Bearer {self.token}'},json=payload)
            if response.status_code not in (200,201):
                raise AnalysisError(f'Analysis request failed (HTTP {response.status_code}); verify publication before retrying')
            return response.json()
        except (httpx.RequestError,ValueError):
            raise AnalysisError('Analysis request failed; verify publication before retrying') from None

    def authenticate(self,cage_id):
        user = self.call('GET','/auth/v1/user')
        try:
            UUID(user['id'])
            metadata = user['app_metadata']
            cages = metadata['analysis_cage_ids']
            if user.get('is_anonymous') is True or metadata.get('role')!='analysis' or not isinstance(cages,list) or any(type(c) is not int for c in cages) or cage_id not in cages:
                raise ValueError
        except (KeyError,TypeError,ValueError,AttributeError):
            raise AnalysisError('Analysis-worker cage permission is required') from None

    def fetch(self,cage_id,start,end,limit=50000):
        return self.call('POST','/rest/v1/rpc/analysis_input',{'p_cage_id':cage_id,'p_from':start.isoformat(),'p_to':end.isoformat(),'p_limit':limit})

    def publish(self,cage_id,start,end,data,bundle,limit=50000):
        identity = json.dumps({'cage_id':cage_id,'from':start.isoformat(),'to':end.isoformat(),
            'input_version':data['input_version'],'bundle':bundle},sort_keys=True,separators=(',',':'),allow_nan=False)
        key = str(uuid5(NAMESPACE_URL,identity))
        receipt = self.call('POST','/rest/v1/rpc/publish_analysis',{'p_cage_id':cage_id,'p_from':start.isoformat(),
            'p_to':end.isoformat(),'p_limit':limit,'p_input_version':data['input_version'],'p_request_key':key,'p_bundle':bundle})
        try:
            valid = (isinstance(receipt,dict) and receipt.get('persisted') is True and receipt.get('cage_id')==cage_id
                     and type(receipt.get('analysis_id')) is int and receipt['analysis_id']>0
                     and receipt.get('config_revision')==bundle['config_revision']
                     and datetime.fromisoformat(receipt['ts'].replace('Z','+00:00'))==end)
        except (KeyError,ValueError,TypeError,AttributeError):
            valid = False
        if not valid:
            raise AnalysisError('Invalid publication acknowledgement; check persistence before retrying')
        return receipt
