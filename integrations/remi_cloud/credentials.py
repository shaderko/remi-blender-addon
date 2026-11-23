"""Remember only verified Remi sessions in the host OS credential store."""
import re

if __package__:
    from .protocol import CloudError, CloudSession
    from ..credentials.vault import StorageError
else:
    from protocol import CloudError, CloudSession
    from integrations.credentials.vault import StorageError


def scope(base_url):
    return 'remi-cloud:' + base_url


def save(vault, session):
    if not session.profile or not session._token or session._expiry is None:
        raise CloudError('A verified account session is required before remembering sign-in.')
    vault.save(scope(session.base_url), {'token': session._token, 'user_id': session.profile['user_id'],
                                       'expires_at': session._expiry.isoformat()})


def restore(vault, base_url):
    value = vault.load(scope(base_url))
    if value is None: return None
    if (not isinstance(value, dict) or not isinstance(value.get('user_id'), str)
            or not isinstance(value.get('token'), str) or not re.fullmatch(r'[A-Za-z0-9._~-]{16,4096}', value['token'])):
        vault.clear(scope(base_url))
        raise CloudError('The saved Remi login is invalid. Sign in again.')
    session = CloudSession(base_url)
    session._token = value['token']
    try:
        profile = session.status()  # Online verification, never trust stored identity.
        if profile['user_id'] != value['user_id']:
            session.clear()
            vault.clear(scope(base_url))
            raise CloudError('The saved login belongs to a different account. Sign in again.')
        save(vault, session)
        return session
    except CloudError:
        if not session._token:
            vault.clear(scope(base_url))  # Confirmed expired/revoked, not transport failure.
            return None
        session.clear()
        raise
