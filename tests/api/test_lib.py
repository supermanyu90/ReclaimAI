import json
import logging

from reclaim_api.lib.log import JsonFormatter, error_tag, message_tag
from reclaim_api.lib.mask import mask_aadhaar, mask_pan, mask_phone, mask_reference


def test_mask_reference_shows_last_four_only():
    assert mask_reference("000011112222") == "••••2222"
    assert mask_reference("123") == "••••"


def test_mask_identity_numbers():
    assert mask_aadhaar("0000 1111 2222") == "XXXX XXXX 2222"
    assert mask_aadhaar("12") == "XXXX XXXX ????"
    assert mask_pan("abcde0000z") == "ABCDE####Z"
    assert mask_pan("bad") == "##########"
    assert mask_phone("+91 98765 43210") == "+91 ••••• 43210"


def test_message_tag_never_contains_text():
    tag = message_tag("I opened an FD for 5 lakh")
    assert "lakh" not in tag and tag.startswith("len=25 ")


def test_error_tag_and_formatter_drop_error_messages():
    err = ValueError("request body: account 000011112222")
    assert error_tag(err) == "error=ValueError"
    try:
        raise err
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "upstream failed", None, __import__("sys").exc_info())
    line = JsonFormatter().format(record)
    assert "000011112222" not in line
    assert json.loads(line)["error"] == "error=ValueError"
