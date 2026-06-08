"""J-Quants integration for TSE_EventBase (Stage 2: financial fundamentals).

Fetches financial statement summaries from the J-Quants API (/fins/summary,
V2) into the ``jquants_statements`` table. The companion enrichment stage
``classifier_v2/stage2_financial.py`` derives a data-driven "beat vs. prior
forecast" signal onto the ``events`` table.
"""
