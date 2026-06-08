#!/usr/bin/env python3
"""
J-Quants API client wrapper for TSE_EventBase.

Thin layer over ``jquantsapi.ClientV2`` (J-Quants V2 API, which authenticates
with a dashboard-issued API key — NOT email/password at call time). Reads
``JQUANTS_API_KEY`` from config/.env unless an explicit key is passed.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import logging
from typing import Optional

import pandas as pd

from config import JQUANTS_API_KEY

logger = logging.getLogger(__name__)


class JQuantsClient:
    """Wraps jquantsapi.ClientV2 with project config and a lazy import.

    The ``jquants-api-client`` dependency is imported lazily so that the rest of
    the project (TDnet/EDINET scraping, classification, prices) does not require
    it to be installed.
    """

    def __init__(self, api_key: Optional[str] = None):
        api_key = api_key or JQUANTS_API_KEY
        if not api_key:
            raise RuntimeError(
                "JQUANTS_API_KEY is not set. Add it to your .env "
                "(JQUANTS_API_KEY=...) or pass --api-key. Issue a key from the "
                "J-Quants dashboard at https://jpx-jquants.com/."
            )
        try:
            import jquantsapi  # lazy import
        except ImportError as e:
            raise RuntimeError(
                "jquants-api-client is not installed. Run: "
                "pip install jquants-api-client"
            ) from e

        self._cli = jquantsapi.ClientV2(api_key=api_key)
        logger.debug("JQuantsClient initialized (jquantsapi %s)",
                     getattr(jquantsapi, "__version__", "?"))

    def fin_summary_range(self, start_date: str, end_date: str,
                          cache_dir: str = "") -> pd.DataFrame:
        """Financial summaries (/fins/summary) for a date range.

        Fans out one request per calendar day (handled by the library) and
        caches each day under ``{cache_dir}/{yyyy}/v2_fin_summary_*.csv.gz`` when
        ``cache_dir`` is set, making re-runs cheap. Returns the V2
        (abbreviated-column) DataFrame.
        """
        return self._cli.get_fin_summary_range(
            start_dt=start_date, end_dt=end_date, cache_dir=cache_dir
        )

    def fin_summary(self, code: str = "", date_yyyymmdd: str = "") -> pd.DataFrame:
        """Financial summaries for a single stock code or a single disclosure date."""
        return self._cli.get_fin_summary(code=code, date_yyyymmdd=date_yyyymmdd)
