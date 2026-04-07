from __future__ import annotations

import re

import pandas as pd
import pytz


_OFFSET_SUFFIX_RE = re.compile(r"(?:Z|[+-]\d{2}(?::?\d{2})?)$", re.IGNORECASE)


def _localize_naive_values_to_utc(
    values: pd.Series,
    tz: pytz.BaseTzInfo,
) -> pd.Series:
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    parsed = pd.to_datetime(values, errors="coerce")
    valid = parsed.notna()
    if not valid.any():
        return result

    localized_source = parsed.loc[valid].sort_index()
    if isinstance(localized_source.dtype, pd.DatetimeTZDtype):
        result.loc[localized_source.index] = localized_source.dt.tz_convert("UTC")
        return result

    try:
        localized = localized_source.dt.tz_localize(
            tz,
            ambiguous="infer",
            nonexistent="shift_forward",
        )
    except (TypeError, ValueError):
        localized = localized_source.dt.tz_localize(
            tz,
            ambiguous=False,
            nonexistent="shift_forward",
        )

    result.loc[localized.index] = localized.dt.tz_convert("UTC")
    return result


def parse_timestamps_to_utc(
    values: pd.Series,
    tz: pytz.BaseTzInfo,
) -> pd.Series:
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        return pd.to_datetime(values, errors="coerce", utc=True)

    if pd.api.types.is_datetime64_dtype(values.dtype):
        return _localize_naive_values_to_utc(values, tz)

    text = values.astype("string")
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    aware_mask = text.str.contains(_OFFSET_SUFFIX_RE, na=False)

    if aware_mask.any():
        result.loc[aware_mask] = pd.to_datetime(text.loc[aware_mask], errors="coerce", utc=True)

    naive_mask = ~aware_mask
    if naive_mask.any():
        result.loc[naive_mask] = _localize_naive_values_to_utc(text.loc[naive_mask], tz)

    return result
