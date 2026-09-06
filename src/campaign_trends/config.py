import os

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
INTELLIGENCE_TABLE_NAME = os.environ.get("INTELLIGENCE_TABLE_NAME", "")
PUBLICATION_INDEX_NAME = os.environ.get("PUBLICATION_INDEX_NAME", "PublicationIndex")
MIN_CONTRIBUTOR_COUNT = int(os.environ.get("MIN_CONTRIBUTOR_COUNT", "10"))
MAXIMUM_PAGE_SIZE = int(os.environ.get("MAXIMUM_PAGE_SIZE", "50"))
PAGINATION_TOKEN_TTL_SECS = int(os.environ.get("PAGINATION_TOKEN_TTL_SECS", "900"))


def validate_config():
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"} or CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Invalid campaign environment or schema")
    if not INTELLIGENCE_TABLE_NAME or not 1 <= MAXIMUM_PAGE_SIZE <= 100:
        raise RuntimeError("Invalid campaign trends configuration")
    if MIN_CONTRIBUTOR_COUNT != 10 or not 60 <= PAGINATION_TOKEN_TTL_SECS <= 3600:
        raise RuntimeError("Invalid campaign privacy or pagination bounds")
