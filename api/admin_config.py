"""Protected admin-only local config read/update, with sanitized errors (#36)."""

import sqlite3
from typing import Annotated
from uuid import UUID

from fastapi import Depends,HTTPException
from ruamel.yaml.error import YAMLError

from common.config_loader import ConfigError
from common.config_editor import ConfigEditor,ConfigConflict,ConfigStorageError
from .farmer_data import (FarmerDataStore,SupabaseSettings,get_farmer_http_client,require_farmer_token)
from .schemas import ConfigView,ConfigReceipt


def require_admin(token:Annotated[str,Depends(require_farmer_token)],
                  client=Depends(get_farmer_http_client)):
    store = FarmerDataStore(SupabaseSettings.from_env(),token,client)
    user = store.request('GET','/auth/v1/user')
    try:
        actor = UUID(user['id'])
        if user.get('is_anonymous') is True: raise ValueError
    except (KeyError,TypeError,ValueError,AttributeError):
        raise HTTPException(401,detail={'code':'invalid_user','message':'A registered user is required'}) from None
    metadata = user.get('app_metadata')
    if not isinstance(metadata,dict) or metadata.get('role')!='admin':
        raise HTTPException(403,detail={'code':'admin_required','message':'Protected administrator role required'})
    return actor


def get_config_editor():
    return ConfigEditor()


def invoke(editor,name,body=None,actor=None):
    try:
        if body is None:
            return ConfigView.model_validate(editor.read(name))
        result = editor.update(name,body.values,body.expected_revision,body.request_key,actor,body.reason)
        return ConfigReceipt.model_validate(result)
    except ConfigConflict:
        raise HTTPException(409,detail={'code':'config_conflict','message':'Refresh the config or replay the identical request'}) from None
    except (ConfigStorageError,OSError,sqlite3.Error):
        raise HTTPException(503,detail={'code':'config_storage_unavailable','message':'Check the current revision and retry with the same request key'}) from None
    except (ConfigError,ValueError,TypeError,YAMLError,RecursionError):
        code = 'invalid_config_bundle' if body is not None else 'config_unavailable'
        raise HTTPException(422 if body is not None else 503,detail={'code':code,'message':'Configuration validation failed'}) from None
