"""CurrencyLookup — convert an amount between two currencies at the latest ECB rate.

Spoken names ("rupees", "$") are normalised to ISO codes in code; the converted
figure comes from the provider, never from the model.
"""

from __future__ import annotations

from core.enums import ErrorMessage, ExceptionCode
from domain.entities.fact_query import FactQuery
from domain.entities.tool_observation import ToolObservation
from domain.policies.currency_policy import normalize_currency, parse_amount
from exceptions.exception import AppException, ToolUnavailableError
from service.lookup.lookup_service import LookupService


def _bad(detail: str) -> AppException:
    return AppException(
        class_name="CurrencyLookup", code=ExceptionCode.VALIDATION_ERROR, error_message=ErrorMessage.GENERIC,
        detail=detail,
    )


class CurrencyLookup:
    def __init__(self, lookup: LookupService):
        self._lookup = lookup

    async def convert(self, amount, base, target) -> ToolObservation:
        value = parse_amount(1 if amount in (None, "") else amount)
        if value is None:
            raise _bad(f"'{amount}' isn't an amount I can convert")
        src, dst = normalize_currency(base), normalize_currency(target)
        if src is None or dst is None:
            raise _bad(f"I don't recognise the currency '{base if src is None else target}'")
        if src == dst:
            text = f"{value:g} {src} = {value:g} {dst} (same currency)"
            return ToolObservation(text=text, spoken=f"That's the same currency: {value:g} {src}.", source="local")

        try:
            answer = await self._lookup.fetch(
                FactQuery(category="fx", params={"from": src, "to": dst, "amount": value})
            )
        except ToolUnavailableError as e:
            if e.status in (404, 422):  # the provider doesn't carry one of these currencies
                raise _bad(f"{src} or {dst} isn't a currency I can get a rate for") from e
            raise
        d = answer.data
        spoken = (
            f"{d['amount']:g} {d['base']} is about {d['converted']:,.2f} {d['target']}, "
            f"at {d['rate']:g} {d['target']} per {d['base']}, using the reference rate from {d['date']}."
        )
        return ToolObservation(
            text=f"CURRENCY (source {answer.provider_id}{', cached' if answer.cached else ''}): {answer.text}",
            spoken=spoken, source=answer.provider_id, as_of=d["date"], cached=answer.cached, data=d,
        )
