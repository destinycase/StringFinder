"""Search-query validation shared by the UI, workers and Python entry points."""
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


def validate_search_query(query: str) -> None:
    """Reject oversized input without trimming or changing the requested query.

    Count Unicode code points, not UTF-8 bytes or Qt UTF-16 code units.
    Empty-query handling remains the responsibility of the caller.
    """
    if not isinstance(query, str):
        raise ValueError(AppStrings.ERROR_SEARCH_QUERY_TYPE)
    if len(query) > Constants.MAX_SEARCH_QUERY_LENGTH:
        raise ValueError(AppStrings.ERROR_SEARCH_QUERY_LENGTH.format(
            Constants.MAX_SEARCH_QUERY_LENGTH, len(query)))
