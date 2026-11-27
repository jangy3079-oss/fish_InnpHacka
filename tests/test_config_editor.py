import copy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from common.config_loader import ConfigLoader,ConfigError,DEFAULT_DIRECTORY
from common.config_editor import ConfigEditor,ConfigConflict,ConfigStorageError
from common import config as legacy

ACTOR='00000000-0000-4000-8000-000000000001'


class ConfigEditorTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'config'
        shutil.copytree(DEFAULT_DIRECTORY,self.root)
        self.loader=ConfigLoader(self.root); self.editor=ConfigEditor(self.loader)

    def proposed(self,name='thresholds'):
        view=self.editor.read(name)
        return view,copy.deepcopy(view['values'])

    def put(self,view,values,key=None,editor=None):
        return (editor or self.editor).update(view['file'],values,view['revision'],key or uuid4(),ACTOR,'시험 변경 근거')

    def test_atomic_update_preserves_comments_and_is_seen_by_independent_loaders(self):
        view,values=self.proposed(); values['levels']['주의']=41
        reader=ConfigLoader(self.root); old=reader.snapshot()
        result=self.put(view,values)
        self.assertTrue(result['changed']); self.assertNotEqual(result['revision'],view['revision'])
        self.assertEqual(reader.snapshot()['thresholds']['levels']['주의'],41)
        self.assertEqual(old['thresholds']['levels']['주의'],40)
        text=(self.root/'thresholds.yaml').read_text(encoding='utf-8')
        self.assertIn('설계 가정값',text); self.assertIn('센서 묶음',text)
        self.assertFalse(list(self.root.glob('.config-*.tmp')))
        with closing(sqlite3.connect(self.root/'.admin-config.sqlite3')) as db:
            self.assertEqual(db.execute('select state from edits').fetchone()[0],'committed')

    def test_noop_and_identical_replay_keep_original_receipt(self):
        view,values=self.proposed(); key=uuid4()
        result=self.put(view,values,key)
        self.assertFalse(result['changed']); self.assertEqual(result['revision'],view['revision'])
        self.assertEqual(result,self.put(view,values,key))
        values['release_hours']=7
        with self.assertRaises(ConfigConflict): self.put(view,values,key)

    def test_json_strings_with_yaml_boolean_spelling_keep_their_meaning(self):
        view,values=self.proposed('cause_rules'); values['rules']['R7']['name']='NO'
        self.put(view,values)
        self.assertEqual(self.loader.snapshot()['cause_rules']['rules']['R7']['name'],'NO')

    def test_replay_after_another_change_does_not_rewrite_old_values(self):
        view,values=self.proposed(); values['release_hours']=7; key=uuid4()
        first=self.put(view,values,key)
        view2,values2=self.proposed(); values2['release_hours']=8; self.put(view2,values2)
        self.assertEqual(self.put(view,values,key),first)
        self.assertEqual(self.loader.snapshot()['thresholds']['release_hours'],8)

    def test_invalid_partial_or_nan_never_changes_files(self):
        view,values=self.proposed(); before=(self.root/'thresholds.yaml').read_bytes()
        for bad in ({'release_hours':7},{**values,'release_hours':float('nan')},{**values,'release_hours':True}):
            with self.subTest(bad=bad),self.assertRaises((ConfigError,ValueError)):
                self.put(view,bad)
            self.assertEqual((self.root/'thresholds.yaml').read_bytes(),before)

    def test_stale_revision_and_parallel_writes_do_not_lose_updates(self):
        view,values=self.proposed()
        def write(number):
            new=copy.deepcopy(values); new['release_hours']=number
            try: return self.put(view,new,editor=ConfigEditor(ConfigLoader(self.root)))
            except ConfigConflict: return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(write,[7,8]))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(results.count('conflict'),1)

    def test_failure_before_replace_recovers_as_retryable_without_mutation(self):
        view,values=self.proposed(); values['release_hours']=7; key=uuid4()
        with patch.object(self.editor,'atomic_replace',side_effect=OSError('private storage path')):
            with self.assertRaises(ConfigStorageError): self.put(view,values,key)
        self.assertEqual(self.loader.snapshot().revision,view['revision'])
        self.assertTrue(self.put(view,values,key)['changed'])

    def test_failure_after_replace_recovers_original_receipt(self):
        view,values=self.proposed(); values['release_hours']=7; key=uuid4()
        replace=self.editor.atomic_replace
        def interrupted(*args): replace(*args); raise OSError('simulated process interruption')
        with patch.object(self.editor,'atomic_replace',side_effect=interrupted):
            with self.assertRaises(ConfigStorageError): self.put(view,values,key)
        result=self.put(view,values,key)
        self.assertEqual(result['revision'],self.loader.snapshot().revision)
        self.assertEqual(self.loader.snapshot()['thresholds']['release_hours'],7)

    def test_unresolved_external_edit_is_not_overwritten(self):
        view,values=self.proposed(); values['release_hours']=7
        with patch.object(self.editor,'atomic_replace',side_effect=OSError()):
            with self.assertRaises(ConfigStorageError): self.put(view,values)
        path=self.root/'costs.yaml'; path.write_bytes(path.read_bytes()+b'\n# external edit\n')
        with self.assertRaises(ConfigStorageError): self.editor.read('thresholds')
        self.assertEqual(self.loader.snapshot()['thresholds']['release_hours'],6)

    def test_model_changes_require_restart_but_other_rules_apply_on_snapshot(self):
        view,values=self.proposed(); values['baseline']['min_windows']=169
        self.assertTrue(self.put(view,values)['restart_required'])
        view,values=self.proposed('cause_rules'); values['rules']['R7']['behavior']['feeding_change_max']=-0.16
        self.assertEqual(self.put(view,values)['application'],'next_snapshot')

    def test_guard_cannot_be_weakened_and_official_text_requires_source_match(self):
        view,values=self.proposed('first_aid'); values['blocked_terms']['ingredients']=['other']
        with self.assertRaises(ConfigError): self.put(view,values)
        view,values=self.proposed('first_aid'); values['common_first_aid'][0]['text']='출처에 없는 임의 조치'
        with self.assertRaises(ConfigError): self.put(view,values)

    def test_default_and_legacy_readers_share_env_directory_and_see_new_bytes(self):
        with patch.dict(os.environ,{'ROCKFISH_CONFIG_DIR':str(self.root)}):
            self.assertEqual(ConfigLoader().directory,self.root.resolve())
            self.assertEqual(legacy.get('thresholds','release_hours',None),6)
            view,values=self.proposed(); values['release_hours']=7; self.put(view,values)
            self.assertEqual(legacy.get('thresholds','release_hours',None),7)

    def test_file_names_are_allowlisted(self):
        for name in ('../costs','costs.yaml','notify','demo'):
            with self.assertRaises(ValueError): self.editor.read(name)

    def test_interprocess_lock_rejects_a_second_writer(self):
        script = '''from common.config_editor import ConfigEditor,ConfigConflict
from common.config_loader import ConfigLoader
import sys
try:
    ConfigEditor(ConfigLoader(sys.argv[1])).read('thresholds')
except ConfigConflict:
    print('conflict')
'''
        with self.editor.locked():
            result=subprocess.run([sys.executable,'-c',script,str(self.root)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'conflict')


if __name__=='__main__': unittest.main()
