"""Admin edits: compare-and-swap, interprocess lock, atomic YAML replacement and durable receipts."""

from contextlib import contextmanager,closing
import hashlib
import io
import json
import os
import sqlite3
import stat
import tempfile
from threading import RLock

from ruamel.yaml import YAML

from .config_loader import CONFIG_NAMES,ConfigLoader,ConfigError,config_revision,validate_contents

_locks = {}


class ConfigConflict(Exception):
    pass


class ConfigStorageError(Exception):
    pass


def replace_values(old,new):
    """Keep comments/quoting attached to existing map keys and list positions."""
    if isinstance(old,dict) and isinstance(new,dict):
        for key in list(old):
            if key not in new:
                del old[key]
        for key,value in new.items():
            old[key] = replace_values(old[key],value) if key in old else value
        return old
    if isinstance(old,list) and isinstance(new,list):
        for i,value in enumerate(new):
            if i<len(old): old[i] = replace_values(old[i],value)
            else: old.append(value)
        del old[len(new):]
        return old
    return old if type(old)==type(new) and old==new else new


class ConfigEditor:
    def __init__(self,loader=None):
        self.loader = loader or ConfigLoader()
        self.root = self.loader.directory

    @contextmanager
    def locked(self):
        # Locking covers all five files and the journal, across editor instances and processes.
        with _locks.setdefault(str(self.root),RLock()):
            for name in [f'{n}.yaml' for n in CONFIG_NAMES]+['.admin-config.lock','.admin-config.sqlite3']:
                if (self.root/name).is_symlink():
                    raise ConfigStorageError('Symlinked config state is not supported')
            with (self.root/'.admin-config.lock').open('a+b') as lock:
                lock.seek(0,2)
                if lock.tell()==0:
                    lock.write(b'0'); lock.flush()
                lock.seek(0)
                try:
                    if os.name=='nt':
                        import msvcrt
                        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError:
                    raise ConfigConflict('Another config update is in progress') from None
                try:
                    yield
                finally:
                    lock.seek(0)
                    if os.name=='nt': msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                    else: fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

    @contextmanager
    def journal(self):
        with closing(sqlite3.connect(self.root/'.admin-config.sqlite3',timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            db.execute('''CREATE TABLE IF NOT EXISTS edits (
                actor TEXT NOT NULL, request_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                file TEXT NOT NULL, before_revision TEXT NOT NULL, after_revision TEXT NOT NULL,
                before_bytes BLOB NOT NULL, after_bytes BLOB NOT NULL, reason TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('prepared','committed','aborted')),
                receipt TEXT NOT NULL, PRIMARY KEY(actor,request_key))''')
            db.commit()
            self.recover(db)
            yield db

    def recover(self,db):
        for row in db.execute("SELECT * FROM edits WHERE state='prepared'").fetchall():
            revision = config_revision(self.loader._read_files())
            if revision==row['after_revision']:
                self.loader.reload()
                db.execute("UPDATE edits SET state='committed' WHERE actor=? AND request_key=?",(row['actor'],row['request_key']))
            elif revision==row['before_revision']:
                db.execute("UPDATE edits SET state='aborted' WHERE actor=? AND request_key=?",(row['actor'],row['request_key']))
            else:
                raise ConfigStorageError('Unresolved config journal; inspect before another edit')
            db.commit()

    def read(self,name):
        if name not in CONFIG_NAMES: raise ValueError('Unsupported config file')
        with self.locked(),self.journal():
            snapshot = self.loader.snapshot()
            return {'file':name,'revision':snapshot.revision,'values':snapshot.to_dict()[name],
                    'loaded_at':snapshot.loaded_at.isoformat()}

    def atomic_replace(self,name,content):
        target = self.root/f'{name}.yaml'
        fd,path = tempfile.mkstemp(prefix='.config-',suffix='.tmp',dir=self.root)
        try:
            with os.fdopen(fd,'wb') as handle:
                handle.write(content); handle.flush(); os.fsync(handle.fileno())
            os.chmod(path,stat.S_IMODE(target.stat().st_mode))
            os.replace(path,target)
            if os.name!='nt':
                directory_fd = os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
                try: os.fsync(directory_fd)
                finally: os.close(directory_fd)
        finally:
            if os.path.exists(path): os.unlink(path)

    def update(self,name,values,expected_revision,request_key,actor,reason):
        if name not in CONFIG_NAMES: raise ValueError('Unsupported config file')
        # Reject NaN, non-JSON input and overlarge structures before formatting/writing.
        payload = json.dumps({'file':name,'values':values,'expected_revision':expected_revision,'reason':reason},
                             sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(payload)>1024*1024: raise ConfigError('Config update is too large')
        digest = hashlib.sha256(payload).hexdigest()
        request_key,actor = str(request_key),str(actor)
        with self.locked(),self.journal() as db:
            previous = db.execute('SELECT * FROM edits WHERE actor=? AND request_key=?',(actor,request_key)).fetchone()
            if previous:
                if previous['payload_hash']!=digest:
                    raise ConfigConflict('Request key reused with another payload')
                if previous['state']=='committed':
                    return json.loads(previous['receipt'])
            raw = self.loader._read_files()
            current = validate_contents(raw)
            if current.revision!=expected_revision:
                raise ConfigConflict('Config revision changed')
            # Validate the full replacement as JSON first. Formatting must not alter its semantics.
            from .config_schema import validate_bundle
            proposed = current.to_dict(); proposed[name] = values
            try:
                validated = validate_bundle(proposed)
            except ValueError:
                raise ConfigError('Config bundle validation failed') from None
            if validated['first_aid']['blocked_terms']!=current.to_dict()['first_aid']['blocked_terms']:
                raise ConfigError('Clinical output guard changes require a reviewed code/config deployment')
            changed = validated[name]!=current.to_dict()[name]
            candidate = dict(raw)
            if changed:
                formatter = YAML(typ='rt',pure=True)
                formatter.version = (1,1)  # Match the existing PyYAML reader's scalar rules (e.g. YES/NO).
                formatter.preserve_quotes = True
                tree = formatter.load(raw[name].decode('utf-8-sig'))
                stream = io.StringIO()
                formatter.dump(replace_values(tree,values),stream)
                candidate[name] = stream.getvalue().encode('utf-8')
            after = validate_contents(candidate)
            if after.to_dict()!=validated:
                raise ConfigError('YAML formatting changed configuration semantics')
            # Clinical source status remains editable, but official responses must still match reviewed sources.
            from decision.first_aid import reviewed_guide,UnsafeFirstAid
            try:
                reviewed_guide('R5',after); reviewed_guide('R8',after)
            except (ConfigError,UnsafeFirstAid):
                raise ConfigError('Reviewed first-aid content is invalid') from None
            before_thresholds,after_thresholds = current['thresholds'],after['thresholds']
            restart = (before_thresholds['cycle_minutes']!=after_thresholds['cycle_minutes']
                       or before_thresholds['baseline']!=after_thresholds['baseline'])
            receipt = {'persisted':True,'changed':changed,'file':name,'request_key':request_key,
                'previous_revision':current.revision,'revision':after.revision,
                'validated_at':after.loaded_at.isoformat(),'reload_status':'validated',
                'application':'restart_required' if restart else 'next_snapshot','restart_required':restart}
            encoded = json.dumps(receipt,ensure_ascii=False)
            db.execute('''INSERT OR REPLACE INTO edits VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (actor,request_key,digest,name,current.revision,after.revision,raw[name],candidate[name],reason,'prepared',encoded))
            db.commit()  # Write-ahead receipt: recovery can distinguish applied vs not applied after a crash.
            if config_revision(self.loader._read_files())!=current.revision:
                db.execute("UPDATE edits SET state='aborted' WHERE actor=? AND request_key=?",(actor,request_key)); db.commit()
                raise ConfigConflict('Config changed before replacement')
            try:
                if changed: self.atomic_replace(name,candidate[name])
                loaded = self.loader.reload()
                if loaded.revision!=after.revision:
                    raise ConfigStorageError('Config changed after replacement; inspect journal')
            except (OSError,ConfigError):
                # A replace may already have succeeded: leave prepared for deterministic next-call recovery.
                raise ConfigStorageError('Config storage outcome requires recovery') from None
            db.execute("UPDATE edits SET state='committed' WHERE actor=? AND request_key=?",(actor,request_key)); db.commit()
            return receipt
