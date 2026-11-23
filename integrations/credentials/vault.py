"""OS-protected local credentials. Never fall back to plaintext token files."""
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time


class StorageError(RuntimeError):
    pass


def _key(scope):
    return hashlib.sha256(scope.encode('utf-8')).hexdigest()


class WindowsBackend:
    def __init__(self, root):
        from ctypes import wintypes
        self.root = Path(root)
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        self.Blob = Blob
        self.crypto = ctypes.WinDLL('crypt32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.crypto.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        self.crypto.CryptProtectData.restype = wintypes.BOOL
        self.crypto.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        self.crypto.CryptUnprotectData.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p

    def transform(self, data, scope, *, decrypt=False):
        buf = ctypes.create_string_buffer(data)
        entropy = ctypes.create_string_buffer(scope.encode('utf-8'))
        source = self.Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        extra = self.Blob(len(scope.encode('utf-8')), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
        result = self.Blob()
        method = self.crypto.CryptUnprotectData if decrypt else self.crypto.CryptProtectData
        # DPAPI binds the ciphertext to this Windows user; no machine-wide flag.
        if not method(ctypes.byref(source), None if decrypt else 'Remi Blender login', ctypes.byref(extra), None, None, 1, ctypes.byref(result)):
            raise StorageError('Windows could not unlock the saved Remi login. Sign in again.')
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            self.kernel.LocalFree(result.data)

    def load(self, scope):
        path = self.root / (_key(scope) + '.bin')
        if not path.exists(): return None
        if path.is_symlink() or path.stat().st_size > 131072:
            raise StorageError('The saved login is invalid. Sign out and reconnect.')
        return self.transform(path.read_bytes(), scope, decrypt=True)

    def save(self, scope, data):
        self.root.mkdir(parents=True, exist_ok=True)
        value = self.transform(data, scope)
        path = self.root / (_key(scope) + '.bin')
        temporary = path.with_suffix('.partial')
        temporary.write_bytes(value)
        temporary.replace(path)

    def clear(self, scope):
        (self.root / (_key(scope) + '.bin')).unlink(missing_ok=True)


class MacBackend:
    service = b'app.remi3d.blender.login.v1'

    def __init__(self, _root):
        self.api = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        p = ctypes.c_void_p
        u = ctypes.c_uint32
        self.api.SecKeychainFindGenericPassword.argtypes = [p, u, p, u, p, ctypes.POINTER(u), ctypes.POINTER(p), ctypes.POINTER(p)]
        self.api.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.api.SecKeychainAddGenericPassword.argtypes = [p, u, p, u, p, u, p, ctypes.POINTER(p)]
        self.api.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.api.SecKeychainItemModifyAttributesAndData.argtypes = [p, p, u, p]
        self.api.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.api.SecKeychainItemDelete.argtypes = [p]
        self.api.SecKeychainItemDelete.restype = ctypes.c_int32
        self.api.SecKeychainItemFreeContent.argtypes = [p, p]
        self.api.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.cf.CFRelease.argtypes = [p]
        self.cf.CFRelease.restype = None

    def _find(self, scope):
        name = _key(scope).encode('ascii')
        length = ctypes.c_uint32()
        value = ctypes.c_void_p()
        item = ctypes.c_void_p()
        code = self.api.SecKeychainFindGenericPassword(None, len(self.service), self.service, len(name), name, ctypes.byref(length), ctypes.byref(value), ctypes.byref(item))
        if code == -25300: return None, None
        if code != 0:
            raise StorageError('Unlock the macOS login Keychain to remember your Remi login.')
        try:
            if length.value > 131072: raise StorageError('The saved login is invalid. Sign out and reconnect.')
            data = ctypes.string_at(value, length.value)
        except Exception:
            if item: self.cf.CFRelease(item)
            raise
        finally:
            self.api.SecKeychainItemFreeContent(None, value)
        return data, item

    def load(self, scope):
        value, item = self._find(scope)
        if item: self.cf.CFRelease(item)
        return value

    def save(self, scope, data):
        _old, item = self._find(scope)
        try:
            if item:
                code = self.api.SecKeychainItemModifyAttributesAndData(item, None, len(data), data)
            else:
                item = ctypes.c_void_p()
                name = _key(scope).encode('ascii')
                code = self.api.SecKeychainAddGenericPassword(None, len(self.service), self.service, len(name), name, len(data), data, ctypes.byref(item))
            if code != 0:
                raise StorageError('macOS Keychain could not remember your login. Unlock it and reconnect.')
        finally:
            if item: self.cf.CFRelease(item)

    def clear(self, scope):
        _value, item = self._find(scope)
        if not item: return
        try:
            if self.api.SecKeychainItemDelete(item) != 0:
                raise StorageError('macOS Keychain could not remove the saved login. Unlock it and sign out again.')
        finally:
            self.cf.CFRelease(item)


class Vault:
    def __init__(self, root, *, backend=None):
        self.root = Path(root)
        self.backend = backend

    def _backend(self):
        if self.backend is None:
            if sys.platform == 'win32': self.backend = WindowsBackend(self.root)
            elif sys.platform == 'darwin': self.backend = MacBackend(self.root)
            else: raise StorageError('Secure login persistence is available on Windows and macOS. This connection lasts for the current session.')
        return self.backend

    def load(self, scope):
        try:
            raw = self._backend().load(scope)
            if raw is None: return None
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get('version') != 1 or value.get('scope') != scope:
                raise ValueError
            return value['value']
        except StorageError: raise
        except Exception:
            raise StorageError('The saved login could not be read. Sign out and reconnect.') from None

    def save(self, scope, value):
        raw = json.dumps({'version': 1, 'scope': scope, 'value': value}, allow_nan=False).encode('utf-8')
        if len(raw) > 65536: raise StorageError('The login is too large to save securely.')
        try: self._backend().save(scope, raw)
        except StorageError: raise
        except Exception:
            raise StorageError('The OS could not remember this login. This connection lasts for the current session.') from None

    def clear(self, scope):
        try: self._backend().clear(scope)
        except StorageError: raise
        except Exception:
            raise StorageError('The saved login could not be removed. Sign out again after unlocking secure storage.') from None

    @contextmanager
    def lock(self, scope, timeout=30):
        # Non-secret lock files serialize rotating refresh tokens across Blender
        # processes. The token remains in Keychain/DPAPI storage throughout.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / (_key(scope) + '.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            if not path.stat().st_size: stream.write(b'0'); stream.flush()
            end = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= end: raise StorageError('Another Blender session is updating this login. Try again shortly.') from None
                    time.sleep(.05)
            try: yield
            finally:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)
