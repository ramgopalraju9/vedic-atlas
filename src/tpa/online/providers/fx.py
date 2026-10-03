"""FxProvider — implements FactProviderPort for currency conversion.

Primary: Frankfurter (ECB reference rates; free, no key; ~30 major currencies).
Fallback: open.er-api.com (ExchangeRate-API open access; free, no key; ~166
currencies, updated daily). The fallback is used when Frankfurter doesn't
carry a currency (HTTP 404/422 — e.g. AED) or is down. Rates from
ExchangeRate-API: https://www.exchangerate-api.com (attribution required by
their terms — the `provider_id` and `sources` always say which one answered).

Frankfurter moved from api.frankfurter.app to api.frankfurter.dev/v1; the old
host answers 301, which the allow-listed client deliberately does not follow.
The converted figure is computed by the provider/this adapter from the rate,
never by the model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from core.logging_config import logger
from domain.entities.fact_answer import FactAnswer
from domain.entities.fact_query import FactQuery
from exceptions.exception import ToolUnavailableError
from tpa.online.http_client import AllowListedHttpClient

_PRIMARY = "api.frankfurter.dev"
_FALLBACK = "open.er-api.com"


class FxProvider:
    """Converts an amount between two currency codes at the latest reference rate."""

    category = "fx"
    allowed_hosts = (_PRIMARY, _FALLBACK)

    def __init__(self, http_client: AllowListedHttpClient):
        self._http = http_client

    async def fetch(self, query: FactQuery) -> FactAnswer:
        base = str(query.params.get("from") or "").upper()
        target = str(query.params.get("to") or "").upper()
        if not base or not target:
            raise ValueError("fx queries require 'from' and 'to' currency-code params")
        amount = float(query.params.get("amount") or 1)

        try:
            return await self._frankfurter(base, target, amount)
        except ToolUnavailableError as primary:
            logger.info(f"[fx] frankfurter could not answer {base}/{target} ({primary}); trying {_FALLBACK}")
            try:
                return await self._er_api(base, target, amount)
            except ToolUnavailableError as fallback:
                # Report "unsupported currency" if either source said so, otherwise the outage.
                raise fallback if fallback.status in (404, 422) else primary

    # ---- sources -------------------------------------------------------------

    async def _frankfurter(self, base: str, target: str, amount: float) -> FactAnswer:
        payload = await self._http.get(
            f"https://{_PRIMARY}/v1/latest",
            category=self.category,
            params={"base": base, "symbols": target, "amount": amount},
        )
        converted = (payload.get("rates") or {}).get(target)
        if converted is None:
            raise ToolUnavailableError("FxProvider", self.category, f"no rate returned for {base}/{target}", status=404)
        return self._answer("frankfurter", _PRIMARY, base, target, amount, converted, str(payload.get("date") or ""))

    async def _er_api(self, base: str, target: str, amount: float) -> FactAnswer:
        payload = await self._http.get(f"https://{_FALLBACK}/v6/latest/{base}", category=self.category)
        rate = (payload.get("rates") or {}).get(target)
        if payload.get("result") != "success" or rate is None:
            raise ToolUnavailableError("FxProvider", self.category, f"{base} or {target} is not supported", status=404)
        try:
            date = parsedate_to_datetime(payload.get("time_last_update_utc", "")).date().isoformat()
        except (TypeError, ValueError):
            date = ""
        return self._answer("exchangerate-api (open)", _FALLBACK, base, target, amount, amount * rate, date)

    def _answer(self, provider_id: str, host: str, base: str, target: str, amount: float, converted: float, date: str) -> FactAnswer:
        rate = converted / amount if amount else 0.0
        data = {"amount": amount, "base": base, "target": target, "converted": converted, "rate": rate, "date": date}
        text = f"{amount:g} {base} = {converted:,.2f} {target} (1 {base} = {rate:g} {target}; reference rate of {date})"
        return FactAnswer(
            provider_id=provider_id, category=self.category, text=text, sources=(host,),
            fetched_at=datetime.now(timezone.utc), data=data,
        )
