"""The reply-filler policy drops stock "anything else?" offers and nothing else."""

import pytest

from domain.policies.reply_filler_policy import FillerStreamFilter, is_filler, strip_filler


@pytest.mark.parametrize("sentence", [
    "Let me know if there's anything else I can help with.",
    "Let me know if you need help with that or anything else!",
    "Feel free to ask me anything.",
    "Is there anything else I can help you with?",
    "I'm here to help.",
    "Don't hesitate to reach out if you have questions.",
    "Please let me know if you have any questions.",
])
def test_stock_offers_are_filler(sentence):
    assert is_filler(sentence)


@pytest.mark.parametrize("sentence", [
    "16 multiplied by 3 is 48.",
    "In Pune it's 24 degrees and clear sky.",
    "Let me know how it goes!",
    "Let me know if you'd like to try again.",
    "I'm here with you.",
    "What's on your mind?",
    "Photosynthesis is how plants turn light into energy.",
])
def test_real_content_is_not_filler(sentence):
    assert not is_filler(sentence)


def test_trailing_offer_is_removed():
    assert strip_filler("16 multiplied by 3 is 48. Let me know if there's anything else I can help with.") == \
        "16 multiplied by 3 is 48."


def test_emoji_after_a_dropped_offer_goes_with_it():
    assert strip_filler("Sure thing. Let me know if you need anything else! 😊") == "Sure thing."


def test_a_reply_that_is_only_filler_is_kept():
    text = "Let me know if there's anything else I can help with."
    assert strip_filler(text) == text


def test_text_without_filler_is_untouched():
    assert strip_filler("Hello! How can I assist you today? 😊") == "Hello! How can I assist you today? 😊"


def _stream(text: str, size: int = 4) -> str:
    f = FillerStreamFilter()
    out = "".join(f.feed(text[i:i + size]) for i in range(0, len(text), size))
    return out + f.finish()


def test_stream_drops_the_trailing_offer():
    assert _stream("16 multiplied by 3 is 48. Let me know if there's anything else I can help with.") == \
        "16 multiplied by 3 is 48."


def test_stream_keeps_multiple_sentences_in_order():
    assert _stream("It is sunny. Humidity is 40 percent! Feel free to ask me anything.") == \
        "It is sunny. Humidity is 40 percent!"


def test_stream_with_only_filler_still_says_it():
    text = "Let me know if there's anything else I can help with."
    assert _stream(text) == text


def test_stream_matches_the_batch_version():
    text = "A car engine burns fuel to make heat. That heat pushes pistons. Let me know if you need more help! 😊"
    assert _stream(text, size=3) == strip_filler(text)
