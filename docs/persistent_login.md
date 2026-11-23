# Remembered sign-in

Remi Cloud and the local ChatGPT image connection are remembered across normal
Blender restarts, add-on disable/re-enable, and loading another .blend file.
Credentials are separate from project and scene data.

- macOS stores protected records in the user's login Keychain.
- Windows stores DPAPI ciphertext under Blender's user configuration directory;
  it is encrypted for the current Windows user, not the whole machine.
- There is no plaintext token-file fallback. If protected storage is unavailable,
  an explicitly requested sign-in can remain usable for that session and reports
  that it could not be remembered. Linux persistence is not implemented.

A saved Remi account is verified with the original server before its profile or
shared board is restored. Ordinary worker shutdown only closes the connection;
**Sign Out** deletes the remembered login and attempts server-side revocation.
Expired/revoked sessions are cleared. Temporary transport failures retain the
credential so reconnecting does not require another browser approval.

The ChatGPT worker stores token and expiry updates together and locks updates
across Blender processes to serialize rotating refresh tokens. It reads the latest
rotation before using a saved session. Explicit **Disconnect ChatGPT** deletes
that saved local connection. A provider-side expiry/disconnection requires new
browser sign-in; temporary refresh failures do not erase the saved tokens.

Automatic restoration runs only in interactive Blender with Online Access
allowed. Headless jobs do not silently read desktop accounts. No token is placed
in RNA, .blend files, IPC status files, logs or release ZIPs.

After updating from a session-only beta, sign in once so the new worker can save
the account. Subsequent starts restore it. macOS may request Keychain access.

Validation uses synthetic accounts, actual owned-worker restarts, native OS
protected-store round trips, logout/revocation, transport interruptions, refresh
rotation and concurrent update locks. Automated tests never read a user's saved
account or perform real generation. Native Windows DPAPI verification must run
on Windows; the development Mac validates Keychain directly.
