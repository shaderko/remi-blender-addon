"""Downloads must make independent, bounded progress, including Windows IPC locks."""
import json
import hashlib
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.remi_cloud.workspace_worker import atomic_json
from integrations.remi_cloud.downloads import Downloads
from integrations.remi_cloud.sdk.embedded import CloudError


class WindowsPublicationTests(unittest.TestCase):
    def test_reader_sharing_violation_is_retried_without_losing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workspace.json'
            path.write_text('{"revision":1}')
            replace = Path.replace
            attempts = []

            def locked(source, target):
                attempts.append(target)
                if len(attempts) < 3:
                    raise PermissionError(13, 'Windows reader temporarily denies delete sharing')
                return replace(source, target)

            with patch.object(Path, 'replace', locked):
                atomic_json(path, {'revision': 2})
            self.assertEqual(json.loads(path.read_text()), {'revision': 2})
            self.assertEqual(len(attempts), 3)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows file-sharing semantics')
    def test_actual_windows_reader_lock_allows_status_publication_after_close(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workspace.json'
            atomic_json(path, {'revision': 1})
            handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            release = threading.Timer(.1, lambda: kernel.CloseHandle(handle))
            release.start()
            try:
                atomic_json(path, {'revision': 2})
            finally:
                release.join()
            self.assertEqual(json.loads(path.read_text()), {'revision': 2})

    def test_persistent_snapshot_lock_does_not_kill_worker_or_discard_state(self):
        from unittest.mock import Mock
        from integrations.remi_cloud.workspace_worker import WorkspaceWorker
        with tempfile.TemporaryDirectory() as directory:
            session = Mock(base_url='http://127.0.0.1:9/api/v1', profile={'user_id': 'fixture'})
            worker = WorkspaceWorker(session, Path(directory)/'ipc', Path(directory)/'cache')
            worker.message = 'Downloaded files are ready'
            with patch('integrations.remi_cloud.workspace_worker.atomic_json', side_effect=PermissionError(13, 'Locked')):
                worker.publish()
            self.assertTrue(worker.publish_pending)
            worker.publish(heartbeat=True)
            self.assertFalse(worker.publish_pending)
            self.assertEqual(json.loads((worker.ipc/'workspace.json').read_text())['message'], worker.message)


class DownloadHelpers:
    def assets(self, count):
        return [{'id': f'card-{i}', 'file': {'id': f'file-{i}', 'sha256': hashlib.sha256(str(i).encode()).hexdigest(),
                 'bytes': len(str(i)), 'kind': 'mesh'}} for i in range(count)]

    def wait(self, queue, assets, predicate, project='default'):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            queue.tick(project, assets)
            if predicate():
                return
            time.sleep(.005)
        self.fail(f'Queue stopped progressing: {queue.records}')

    def result(self, root, file):
        path = root/file['id']
        path.write_bytes(file['id'].split('-')[-1].encode())
        return {'path': str(path), 'file': file}

class DownloadQueueTests(DownloadHelpers, unittest.TestCase):
    def test_three_downloads_and_slow_first_card_does_not_block_the_fourth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gates = [threading.Event() for _ in range(4)]
            started = [threading.Event() for _ in range(4)]
            lock = threading.Lock()
            running = [0, 0]

            def download(project, file, progress, cancelled):
                index = int(file['id'].split('-')[-1])
                with lock:
                    running[0] += 1
                    running[1] = max(running)
                started[index].set()
                try:
                    progress({'state': 'downloading', 'receivedBytes': 1, 'totalBytes': 2})
                    gates[index].wait(3)
                    return self.result(root, file)
                finally:
                    with lock:
                        running[0] -= 1

            queue = Downloads(download)
            assets = self.assets(4)
            try:
                queue.tick('default', assets)
                self.assertTrue(all(event.wait(1) for event in started[:3]))
                self.assertFalse(started[3].is_set())
                self.assertEqual(running[1], 3)
                gates[1].set()
                self.wait(queue, assets, lambda: started[3].is_set())
                self.assertNotIn('path', assets[0])
                self.assertIn('path', assets[1])
                self.assertLessEqual(running[1], 3)
                for gate in gates:
                    gate.set()
                self.wait(queue, assets, lambda: all(a.get('path') for a in assets))
            finally:
                queue.close()
                for gate in gates:
                    gate.set()

    def test_retries_stop_after_three_attempts_and_explicit_retry_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            clock = [0]

            def download(project, file, progress, cancelled):
                calls.append(file['id'])
                if len(calls) <= 3:
                    raise CloudError('Fixture connection lost', retryable=True)
                return self.result(root, file)

            queue = Downloads(download, clock=lambda: clock[0])
            assets = self.assets(1)
            for state in ('retrying', 'retrying', 'failed'):
                self.wait(queue, assets, lambda: assets[0].get('download', {}).get('state') == state and not queue.active)
                clock[0] += 30
            for _ in range(5):
                queue.tick('default', assets)
            self.assertEqual(len(calls), 3)
            queue.retry('default', assets[0]['file'])
            self.wait(queue, assets, lambda: bool(assets[0].get('path')))
            self.assertEqual(len(calls), 4)
            queue.close()


class HTTPDownloadTests(DownloadHelpers, unittest.TestCase):
    def test_real_compressed_http_queue_and_restart_reuse_verified_cache(self):
        from integrations.remi_cloud.sdk.embedded import CloudSession
        from integrations.remi_cloud.workspace_worker import WorkspaceWorker
        body = (b'ply\nformat ascii 1.0\nelement vertex 20000\nproperty float x\n'
                b'property float y\nproperty float z\nend_header\n' + b'0 0 0\n'*20000)
        packed = gzip.compress(body)
        checksum = hashlib.sha256(body).hexdigest()
        received = []
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                assert self.headers['Authorization'] == 'Bearer synthetic-download-session'
                payload = json.dumps({'filename': 'mesh.ply', 'contentType': 'application/ply',
                                      'bytes': len(body), 'sha256': checksum}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_GET(self):
                assert self.headers['X-Remi-Accept-Compression'] == 'remi-v1'
                received.append(self.path)
                release.wait(3)
                self.send_response(200)
                self.send_header('Content-Length', str(len(packed)))
                self.send_header('X-Remi-Compression', 'gzip')
                self.end_headers()
                self.wfile.write(packed)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        worker = restarted = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                session = CloudSession(f'http://127.0.0.1:{server.server_port}/api/v1', realtime=False)
                session._token = 'synthetic-download-session'
                session.profile = {'user_id': 'fixture'}
                assets = [{'id': f'card-{i}', 'file': None, 'fileRef': {'id': str(uuid4()), 'kind': 'mesh',
                           'createdAt': '2026-11-24T00:00:00Z'}} for i in range(4)]
                worker = WorkspaceWorker(session, root/'ipc', root/'cache')
                worker.ready = True
                worker.snapshot = {'assets': assets}
                end = time.monotonic() + 5
                while len(received) < 3 and time.monotonic() < end:
                    worker.download_next()
                    time.sleep(.005)
                self.assertEqual(len(received), 3)
                release.set()
                end = time.monotonic() + 5
                while not all(a.get('path') for a in assets) and time.monotonic() < end:
                    worker.download_next()
                    time.sleep(.005)
                self.assertTrue(all(a.get('path') for a in assets))
                self.assertTrue(all(Path(a['path']).read_bytes() == body for a in assets))
                self.assertEqual(len(received), 3, 'Fourth identical file should reuse the verified cache')
                worker.close()
                restarted = WorkspaceWorker(session, root/'ipc2', root/'cache')
                recovered = [{'id': a['id'], 'file': a['file']} for a in assets]
                restarted.ready = True
                restarted.snapshot = {'assets': recovered}
                end = time.monotonic() + 5
                while not all(a.get('path') for a in recovered) and time.monotonic() < end:
                    restarted.download_next()
                    time.sleep(.005)
                self.assertTrue(all(a.get('path') for a in recovered))
                self.assertEqual(len(received), 3, 'Restart re-downloaded complete cache files')
        finally:
            release.set()
            if worker:worker.close()
            if restarted:restarted.close()
            server.shutdown();server.server_close();thread.join()

    def test_identical_file_ids_are_downloaded_once_for_multiple_cards(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            assets = self.assets(1)
            assets.append({**assets[0], 'id': 'duplicate'})

            def download(project, file, *_args):
                calls.append(file['id'])
                return self.result(Path(directory), file)

            queue = Downloads(download)
            self.wait(queue, assets, lambda: all(a.get('path') for a in assets))
            self.assertEqual(len(calls), 1)
            self.assertEqual(assets[0]['path'], assets[1]['path'])
            queue.close()

    def test_project_switch_cancels_old_download_without_publishing_into_new_board(self):
        with tempfile.TemporaryDirectory() as directory:
            started = threading.Event()
            cancelled_old = threading.Event()
            root = Path(directory)

            def download(project, file, progress, cancelled):
                if project == 'old':
                    started.set()
                    end = time.monotonic() + 3
                    while not cancelled() and time.monotonic() < end:
                        time.sleep(.005)
                    cancelled_old.set()
                return self.result(root, file)

            queue = Downloads(download)
            old = self.assets(1)
            new = self.assets(2)[1:]
            queue.tick('old', old)
            self.assertTrue(started.wait(1))
            self.wait(queue, new, lambda: bool(new[0].get('path')) and cancelled_old.is_set() and not queue.active, 'new')
            self.assertNotIn('path', old[0])
            self.assertTrue(all(key[0] == 'new' for key in queue.records))
            queue.close()


if __name__ == '__main__':
    unittest.main()
