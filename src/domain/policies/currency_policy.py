"""CurrencyPolicy — turn what people say ("rupees", "$", "usd") into ISO codes.

Done in code, not by the model: a 4B model is unreliable at currency names, and
a wrong code silently converts the wrong pair. Unknown 3-letter alphabetic
tokens are passed through upper-cased (the provider rejects unsupported ones).
"""

import re

_ALIASES = {
    "rupee": "INR", "rupees": "INR", "rs": "INR", "inr": "INR", "₹": "INR",
    "indian rupee": "INR", "indian rupees": "INR",
    "dollar": "USD", "dollars": "USD", "usd": "USD", "$": "USD", "buck": "USD", "bucks": "USD",
    "us dollar": "USD", "us dollars": "USD", "american dollar": "USD", "american dollars": "USD",
    "euro": "EUR", "euros": "EUR", "eur": "EUR", "€": "EUR",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "£": "GBP", "sterling": "GBP",
    "british pound": "GBP", "british pounds": "GBP",
    "yen": "JPY", "jpy": "JPY", "¥": "JPY", "japanese yen": "JPY",
    "yuan": "CNY", "rmb": "CNY", "cny": "CNY", "renminbi": "CNY",
    "dirham": "AED", "dirhams": "AED", "aed": "AED",
    "franc": "CHF", "francs": "CHF", "chf": "CHF", "swiss franc": "CHF", "swiss francs": "CHF",
    "australian dollar": "AUD", "australian dollars": "AUD", "aud": "AUD",
    "canadian dollar": "CAD", "canadian dollars": "CAD", "cad": "CAD",
    "singapore dollar": "SGD", "singapore dollars": "SGD", "sgd": "SGD",
    "won": "KRW", "krw": "KRW", "baht": "THB", "thb": "THB", "ringgit": "MYR", "myr": "MYR",
    "rand": "ZAR", "zar": "ZAR", "krona": "SEK", "sek": "SEK", "zloty": "PLN", "pln": "PLN",
}

MAX_AMOUNT = 1e12


def normalize_currency(token: object) -> str | None:
    """ISO-4217-style code for a spoken/written currency, or None if unrecognisable."""
    text = re.sub(r"\s+", " ", str(token or "").strip().lower().rstrip("."))
    if not text:
        return None
    if text in _ALIASES:
        return _ALIASES[text]
    if re.fullmatch(r"[a-z]{3}", text):
        return text.upper()
    return None


def parse_amount(value: object) -> float | None:
    """A positive finite amount, or None. Accepts 1,000.50 style strings."""
    try:
        amount = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if amount != amount or amount <= 0 or amount > MAX_AMOUNT:  # NaN, non-positive, absurd
        return None
    return amount
