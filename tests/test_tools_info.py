from __future__ import annotations

import httpx
import pytest

from thanos.tools import info


class Resp:
    def __init__(self, data=None, text=""):
        self._data, self.text = data, text

    def json(self):
        return self._data


def test_calculator() -> None:
    assert info.calculate("18% of 2450").message == "That's 441."
    assert info.calculate("345 x 12").message == "That's 4,140."
    assert info.calculate("sqrt(144) + 2^3").message == "That's 20."
    assert info.calculate("10 divided by 4").message == "That's 2.5."
    assert not info.calculate("1/0").ok
    for bad in ['__import__("os")', "().__class__", "2**99999", "open('x')"]:
        assert not info.calculate(bad).ok


def test_units() -> None:
    assert info.convert_units(30, "celsius", "fahrenheit").message == "30 celsius is 86 fahrenheit."
    assert info.convert_units(1, "km", "m").message == "1 km is 1,000 m."
    assert not info.convert_units(1, "kg", "km").ok


def test_currency(monkeypatch) -> None:
    seen = {}

    def fake(url, params=None):
        seen.update(params)
        return Resp({"rates": {"INR": 9448.0}})

    monkeypatch.setattr(info, "http_get", fake)
    assert info.convert_currency(100, "dollars", "rupees").message == "100 USD is 9,448 INR."
    assert seen == {"amount": 100, "from": "USD", "to": "INR"}
    assert not info.convert_currency(1, "zorkmids", "INR").ok


WEATHER = {
    "current": {"temperature_2m": 21.4, "apparent_temperature": 24.6, "weather_code": 3, "wind_speed_10m": 5},
    "daily": {"temperature_2m_max": [28.2, 26], "temperature_2m_min": [19, 18.6],
              "precipitation_probability_max": [80, 10], "weather_code": [61, 1]},
}


def test_weather_format() -> None:
    assert info.format_weather(WEATHER, "Bengaluru", "now", "C") == (
        "In Bengaluru it's 21°C and overcast, feels like 25°C. Today's high is 28°C, with a 80% chance of rain."
    )
    assert info.format_weather(WEATHER, "Bengaluru", "tomorrow", "C") == (
        "Tomorrow in Bengaluru: mostly clear, high 26°C, low 19°C, 10% chance of rain."
    )


def test_weather_unknown_city(monkeypatch) -> None:
    monkeypatch.setattr(info, "geocode", lambda c: None)
    assert not info.get_weather("Atlantis").ok


def test_rss_titles() -> None:
    xml = """<rss><channel><item><title>Big thing happens - The Paper</title></item>
    <item><title>Other news - Site A</title></item></channel></rss>"""
    assert info.parse_rss_titles(xml, 5) == ["Big thing happens", "Other news"]


def test_define_not_found(monkeypatch) -> None:
    def fake(url, params=None):
        raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))

    monkeypatch.setattr(info, "http_get", fake)
    assert not info.define_word("qwxz").ok
