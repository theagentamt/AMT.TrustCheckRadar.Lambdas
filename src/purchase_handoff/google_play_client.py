import json
from urllib.parse import quote

import boto3

from config import GOOGLE_PLAY_SECRET_NAME

secrets_client = boto3.client("secretsmanager")

GOOGLE_ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
GOOGLE_PLAY_API_BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3"


class GooglePlayRetryableError(RuntimeError):
    pass


class GooglePlayRejectedError(RuntimeError):
    pass


def fetch_subscription_purchase(*, package_name: str, purchase_token: str) -> dict:
    session = _build_authorized_session()
    url = (
        f"{GOOGLE_PLAY_API_BASE}/applications/{quote(package_name, safe='')}/"
        f"purchases/subscriptionsv2/tokens/{quote(purchase_token, safe='')}"
    )

    try:
        response = session.get(url, timeout=10)
    except Exception as err:  # pragma: no cover - network failure branch
        raise GooglePlayRetryableError("Unable to reach Google Play verification service.") from err

    if response.status_code == 200:
        return response.json()
    if response.status_code in {400, 401, 403, 404, 410}:
        raise GooglePlayRejectedError("Google Play rejected the purchase token.")
    raise GooglePlayRetryableError(f"Google Play verification failed with status {response.status_code}.")


def _build_authorized_session():
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError as err:  # pragma: no cover - packaging responsibility
        raise GooglePlayRetryableError("Google Play verification dependencies are not available.") from err

    credentials_payload = _load_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        credentials_payload,
        scopes=[GOOGLE_ANDROID_PUBLISHER_SCOPE],
    )
    return AuthorizedSession(credentials)


def _load_service_account_info() -> dict:
    if not GOOGLE_PLAY_SECRET_NAME:
        raise GooglePlayRetryableError("Google Play service account secret is not configured.")
    secret_value = secrets_client.get_secret_value(SecretId=GOOGLE_PLAY_SECRET_NAME)
    secret_string = secret_value.get("SecretString")
    if not secret_string:
        raise GooglePlayRetryableError("The Google Play service account secret is empty.")
    try:
        return json.loads(secret_string)
    except json.JSONDecodeError as err:
        raise GooglePlayRetryableError("The Google Play service account secret is not valid JSON.") from err
