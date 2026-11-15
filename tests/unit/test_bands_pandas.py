"""band_start/band_end/band_group accept pandas Timestamps and drop nanoseconds."""

from datetime import UTC, datetime

import pandas as pd

from emtra_common.bands import band_end, band_group, band_start


def test_band_start_pandas_timestamp_drops_nanoseconds():
    ts = pd.Timestamp("2026-11-16 06:17:42.123456789", tz="UTC")  # 07:17 Madrid
    got = band_start(ts)
    assert got == datetime(2026, 11, 16, 6, 15, tzinfo=UTC)
    assert got.microsecond == 0 and getattr(got, "nanosecond", 0) == 0
    assert band_end(ts) == datetime(2026, 11, 16, 6, 30, tzinfo=UTC)
    assert band_group(ts) == "am_peak"


def test_band_start_same_for_datetime_and_timestamp():
    dt = datetime(2026, 11, 16, 8, 44, 59, tzinfo=UTC)
    assert band_start(dt) == band_start(pd.Timestamp(dt))
