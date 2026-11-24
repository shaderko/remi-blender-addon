"""A project-bound queue. Network threads never publish IPC or touch Blender."""
from concurrent.futures import Future
from pathlib import Path
from queue import Empty, SimpleQueue
import threading
import time

if __package__:
    from .sdk.embedded import CloudError
else:
    from sdk.embedded import CloudError


class Downloads:
    LIMIT = 3
    RETRY_DELAYS = (2, 10)

    def __init__(self, download, *, clock=time.monotonic):
        self.download = download
        self.clock = clock
        self.records = {}
        self.active = {}
        self.events = SimpleQueue()
        self.project = None
        self.wanted = set()
        self.closed = False

    @staticmethod
    def key(project, file):
        # File IDs are immutable, and available before ticket resolution.
        return project, file['id']

    def retry(self, project, file=None):
        for key, record in self.records.items():
            if key[0] == project and (file is None or key == self.key(project, file)):
                if key not in self.active and record['state'] in ('failed', 'retrying'):
                    record.update(state='queued', attempt=0, receivedBytes=0, retry_at=0)
                    record.pop('error', None)

    def start(self, key, record):
        future = Future()
        cancel = threading.Event()
        self.active[key] = {'future': future, 'cancel': cancel}
        record.update(state='downloading', attempt=record['attempt'] + 1, receivedBytes=0,
                      totalBytes=record['file'].get('bytes', 0))
        record.pop('error', None)

        def run():
            last = 0
            phase = None

            def progress(value):
                nonlocal last, phase
                now = self.clock()
                if value.get('state') != phase or now - last >= .25:
                    self.events.put((key, value))
                    last, phase = now, value.get('state')

            try:
                future.set_result(self.download(key[0], record['file'], progress, cancel.is_set))
            except Exception as error:
                future.set_exception(error)

        threading.Thread(target=run, daemon=True, name='remi-file-download').start()

    def tick(self, project, assets):
        if self.closed:
            return False
        changed = False
        self.project = project
        self.wanted = {self.key(project, a.get('file') or a['fileRef']) for a in assets if a.get('file') or a.get('fileRef')}
        for key, task in self.active.items():
            if key not in self.wanted:
                task['cancel'].set()
        for key in list(self.records):
            if key not in self.wanted and key not in self.active:
                self.records.pop(key)
        for asset in assets:
            file = asset.get('file') or asset.get('fileRef')
            if not file:
                continue
            key = self.key(project, file)
            record = self.records.get(key)
            if record and record['state'] == 'ready' and record.get('check_at',0)<=self.clock():
                try:
                    stamp = Path(record['path']).stat()
                    valid = (stamp.st_mtime_ns, stamp.st_size) == record['stamp']
                except OSError:
                    valid = False
                if not valid:
                    self.records.pop(key)
                    record = None
                else:
                    record['check_at']=self.clock()+5
            if record is None:
                self.records[key] = {'file': dict(file), 'state': 'queued', 'attempt': 0,
                                     'receivedBytes': 0, 'totalBytes': file.get('bytes', 0), 'retry_at': 0}
                changed = True
        while True:
            try:
                key, progress = self.events.get_nowait()
            except Empty:
                break
            if key in self.active and key in self.records:
                self.records[key].update(progress)
                changed = True
        for key, task in list(self.active.items()):
            if not task['future'].done():
                continue
            self.active.pop(key)
            record = self.records[key]
            try:
                result = task['future'].result()
                path = result['path']
                if task['cancel'].is_set():
                    raise InterruptedError('Download cancelled')
                stamp = Path(path).stat()
                record.update(state='ready', path=path, file=result['file'], stamp=(stamp.st_mtime_ns, stamp.st_size),check_at=self.clock()+5)
                record.pop('error', None)
            except InterruptedError:
                record.update(state='queued', attempt=0, retry_at=0)
            except Exception as error:
                retryable = not isinstance(error, CloudError) or error.retryable
                record['error'] = str(error) if isinstance(error, CloudError) else 'The download was interrupted.'
                if retryable and record['attempt'] <= len(self.RETRY_DELAYS):
                    record.update(state='retrying', retry_at=self.clock() + self.RETRY_DELAYS[record['attempt'] - 1])
                else:
                    record['state'] = 'failed'
            changed = True
            if key not in self.wanted:
                self.records.pop(key)
        # New files get a turn before retries; a broken first card cannot starve the board.
        for key, record in sorted(self.records.items(), key=lambda item: item[1]['attempt']):
            if len(self.active) >= self.LIMIT:
                break
            if key in self.wanted and key not in self.active and record['state'] in ('queued', 'retrying'):
                if record['retry_at'] <= self.clock():
                    self.start(key, record)
                    changed = True
        self.apply(project, assets)
        return changed

    def apply(self, project, assets):
        for asset in assets:
            file = asset.get('file') or asset.get('fileRef')
            if not file:
                continue
            record = self.records.get(self.key(project, file))
            if not record:
                continue
            asset['download'] = {k: record[k] for k in
                                 ('state', 'attempt', 'receivedBytes', 'totalBytes', 'error') if k in record}
            asset.pop('downloadError', None)
            if record['state'] == 'ready':
                asset['path'] = record['path']
                asset['file'] = record['file']
                asset['pathStamp'] = list(record['stamp'])
            else:
                asset.pop('path', None)
                asset.pop('pathStamp', None)
                if record.get('error'):
                    asset['downloadError'] = record['error']

    def close(self):
        self.closed = True
        for task in self.active.values():
            task['cancel'].set()
