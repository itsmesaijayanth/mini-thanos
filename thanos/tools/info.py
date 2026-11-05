"""Quick info: weather, news, Wikipedia, dictionary, currency, calculator, unit conversion.

All free, no API keys: Open-Meteo, ip-api, Google News RSS, Wikipedia REST, dictionaryapi.dev,
Frankfurter (ECB rates). Math is done here, never by the LLM.
"""

from __future__ import annotations

import ast
import math
import operator
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import quote

import httpx

from thanos.config import get_settings
from thanos.tools.base import ToolResult
from thanos.tools.registry import tool

TIMEOUT = 10.0
HEADERS = {"User-Agent": "Thanos/0.1 (personal local voice assistant; https://github.com/) python-httpx"}


def http_get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    resp = httpx.get(url, params=params, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp


# --- location -----------------------------------------------------------------


@lru_cache(maxsize=32)
def geocode(city: str) -> dict[str, Any] | None:
    data = http_get(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1, "language": "en", "format": "json"},
    ).json()
    hits = data.get("results") or []
    if not hits:
        return None
    h = hits[0]
    return {"city": h["name"], "lat": h["latitude"], "lon": h["longitude"], "country": h.get("country_code", "")}


@lru_cache(maxsize=1)
def ip_location() -> dict[str, Any]:
    d = http_get("http://ip-api.com/json").json()
    return {"city": d.get("city", ""), "lat": d["lat"], "lon": d["lon"], "country": d.get("countryCode", "")}


def home_location() -> dict[str, Any]:
    home = get_settings().home_city
    if home and (loc := geocode(home)):
        return loc
    return ip_location()


# --- weather ------------------------------------------------------------------

WMO = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


@tool(
    category="info",
    direct=True,
    examples=["what's the weather", "will it rain tomorrow", "weather in Mumbai", "how hot is it outside",
              "do I need an umbrella today"],
)
def get_weather(city: str = "", when: Literal["now", "today", "tomorrow"] = "now") -> ToolResult:
    """Get the weather: current conditions, or today's/tomorrow's forecast (temperature, rain chance).

    Args:
        city: City name, e.g. "Mumbai". Leave empty for the user's own location.
        when: "now", "today" or "tomorrow". Default "now".
    """
    try:
        loc = geocode(city) if city.strip() else home_location()
    except httpx.HTTPError as e:
        return ToolResult.fail(f"Couldn't look up the location: {e}")
    if not loc:
        return ToolResult.fail(f"I couldn't find a place called {city}.")
    imperial = get_settings().units == "imperial"
    params = {
        "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
    }
    try:
        data = http_get("https://api.open-meteo.com/v1/forecast", params).json()
    except httpx.HTTPError as e:
        return ToolResult.fail(f"Weather service unavailable: {e}")
    return ToolResult.success(format_weather(data, loc["city"] or city, when, "F" if imperial else "C"))


def format_weather(data: dict[str, Any], place: str, when: str, unit: str) -> str:
    deg = f"°{unit}"
    if when == "now":
        c = data["current"]
        d = data["daily"]
        desc = WMO.get(c["weather_code"], "mixed weather")
        msg = f"In {place} it's {round(c['temperature_2m'])}{deg} and {desc}"
        feels = round(c["apparent_temperature"])
        if abs(feels - c["temperature_2m"]) >= 3:
            msg += f", feels like {feels}{deg}"
        rain = d["precipitation_probability_max"][0]
        msg += f". Today's high is {round(d['temperature_2m_max'][0])}{deg}"
        if rain is not None and rain >= 30:
            msg += f", with a {rain}% chance of rain"
        return msg + "."
    i = 0 if when == "today" else 1
    d = data["daily"]
    desc = WMO.get(d["weather_code"][i], "mixed weather")
    rain = d["precipitation_probability_max"][i]
    msg = (f"{when.capitalize()} in {place}: {desc}, high {round(d['temperature_2m_max'][i])}{deg}, "
           f"low {round(d['temperature_2m_min'][i])}{deg}")
    if rain is not None:
        msg += f", {rain}% chance of rain"
    return msg + "."


# --- news ---------------------------------------------------------------------


@tool(
    category="info",
    direct=True,
    examples=["what's the news", "latest headlines", "any news about cricket", "tech news today"],
)
def get_news(topic: str = "") -> ToolResult:
    """Get the latest news headlines, overall or about a topic.

    Args:
        topic: Optional topic, e.g. "cricket", "technology", "stock market". Empty = top headlines.
    """
    try:
        country = (home_location().get("country") or "US").upper()
    except httpx.HTTPError:
        country = "US"
    lang = f"hl=en-{country}&gl={country}&ceid={country}:en"
    url = (f"https://news.google.com/rss/search?q={quote(topic)}&{lang}" if topic.strip()
           else f"https://news.google.com/rss?{lang}")
    try:
        xml = http_get(url).text
    except httpx.HTTPError as e:
        return ToolResult.fail(f"News unavailable: {e}")
    headlines = parse_rss_titles(xml, 5)
    if not headlines:
        return ToolResult.fail(f"No news found{' about ' + topic if topic else ''}.")
    intro = f"Top news about {topic}: " if topic.strip() else "Top headlines: "
    return ToolResult.success(intro + " ".join(f"{h}." for h in headlines))


def parse_rss_titles(xml: str, limit: int) -> list[str]:
    root = ET.fromstring(xml)
    titles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        title = re.sub(r"\s+-\s+[^-]+$", "", title)  # drop " - Source Name"
        if title:
            titles.append(title.rstrip("."))
        if len(titles) >= limit:
            break
    return titles


# --- wikipedia / dictionary ---------------------------------------------------


@tool(
    category="info",
    direct=True,
    examples=["who is APJ Abdul Kalam", "what is quantum computing", "tell me about the Eiffel Tower"],
)
def wikipedia_summary(topic: str) -> ToolResult:
    """Get a short encyclopedia summary of a person, place, thing or idea from Wikipedia.

    Args:
        topic: What to look up, e.g. "Eiffel Tower".
    """
    try:
        hits = http_get(
            "https://en.wikipedia.org/w/api.php",
            {"action": "opensearch", "search": topic, "limit": 1, "namespace": 0, "format": "json"},
        ).json()
        if not hits[1]:
            return ToolResult.fail(f"Wikipedia has nothing on {topic}.")
        title = hits[1][0]
        page = http_get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}").json()
    except httpx.HTTPError as e:
        return ToolResult.fail(f"Wikipedia unavailable: {e}")
    extract = (page.get("extract") or "").strip()
    if not extract:
        return ToolResult.fail(f"No summary for {title}.")
    sentences = re.split(r"(?<=[.!?])\s+", extract)
    return ToolResult.success(" ".join(sentences[:3]), title=title)


@tool(
    category="info",
    direct=True,
    examples=["what does serendipity mean", "define ephemeral", "meaning of the word ubiquitous"],
)
def define_word(word: str) -> ToolResult:
    """Get the dictionary meaning of an English word.

    Args:
        word: The word, e.g. "serendipity".
    """
    w = word.strip().lower()
    try:
        data = http_get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(w)}").json()
    except httpx.HTTPStatusError:
        return ToolResult.fail(f"I couldn't find the word {word}.")
    except httpx.HTTPError as e:
        return ToolResult.fail(f"Dictionary unavailable: {e}")
    meaning = data[0]["meanings"][0]
    d = meaning["definitions"][0]
    msg = f"{w.capitalize()}, {meaning['partOfSpeech']}: {d['definition']}"
    if d.get("example"):
        msg += f" For example: {d['example']}"
    return ToolResult.success(msg)


# --- currency -----------------------------------------------------------------

CURRENCY_NAMES = {
    "dollar": "USD", "dollars": "USD", "usd": "USD", "us dollar": "USD", "bucks": "USD",
    "rupee": "INR", "rupees": "INR", "inr": "INR", "indian rupee": "INR",
    "euro": "EUR", "euros": "EUR", "pound": "GBP", "pounds": "GBP", "sterling": "GBP",
    "yen": "JPY", "yuan": "CNY", "renminbi": "CNY", "dirham": "AED", "dirhams": "AED",
    "canadian dollar": "CAD", "australian dollar": "AUD", "singapore dollar": "SGD",
    "franc": "CHF", "swiss franc": "CHF", "won": "KRW", "ruble": "RUB", "rouble": "RUB",
}


def currency_code(name: str) -> str:
    n = name.strip().lower()
    if n in CURRENCY_NAMES:
        return CURRENCY_NAMES[n]
    if re.fullmatch(r"[a-z]{3}", n):
        return n.upper()
    raise ValueError(f"unknown currency '{name}'")


@tool(
    category="info",
    direct=True,
    examples=["convert 100 dollars to rupees", "how much is 50 euros in INR", "usd to inr rate"],
)
def convert_currency(amount: float, from_currency: str, to_currency: str) -> ToolResult:
    """Convert money between currencies at today's exchange rate.

    Args:
        amount: How much, e.g. 100. Use 1 to get the rate.
        from_currency: Currency code or name, e.g. "USD" or "dollars".
        to_currency: Currency code or name, e.g. "INR" or "rupees".
    """
    try:
        src, dst = currency_code(from_currency), currency_code(to_currency)
    except ValueError as e:
        return ToolResult.fail(f"{e}; use a 3-letter code like USD.")
    if src == dst:
        return ToolResult.success(f"{amount:g} {src} is {amount:g} {dst}.")
    try:
        data = http_get("https://api.frankfurter.app/latest", {"amount": amount, "from": src, "to": dst}).json()
    except httpx.HTTPError as e:
        return ToolResult.fail(f"Exchange rates unavailable (maybe unsupported currency): {e}")
    value = data["rates"][dst]
    return ToolResult.success(f"{amount:,.2f} {src} is {value:,.2f} {dst}.".replace(".00 ", " "))


# --- calculator ---------------------------------------------------------------

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {name: getattr(math, name) for name in
          ("sqrt", "sin", "cos", "tan", "log", "log10", "log2", "exp", "floor", "ceil", "factorial")}
_FUNCS |= {"abs": abs, "round": round}
_CONSTS = {"pi": math.pi, "e": math.e}


def safe_eval(expr: str) -> float:
    expr = expr.lower().replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", expr)  # 18% of 2450
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expr)
    expr = re.sub(r"\b(x|times)\b", "*", expr)
    expr = re.sub(r"\bplus\b", "+", expr)
    expr = re.sub(r"\bminus\b", "-", expr)
    expr = re.sub(r"\b(divided by|over)\b", "/", expr)
    expr = re.sub(r"\bsquare root of\b", "sqrt", expr)
    tree = ast.parse(expr.strip(), mode="eval")

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 1000:
                raise ValueError("exponent too large")
            return _BIN[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name) and node.id in _CONSTS:
            return _CONSTS[node.id]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
                and not node.keywords):
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError("unsupported expression")

    return ev(tree)


def fmt_number(x: float) -> str:
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        x = int(x)
    if isinstance(x, int):
        return f"{x:,}"
    return f"{x:,.6g}" if abs(x) < 1e6 else f"{x:,.2f}"


@tool(
    category="info",
    direct=True,
    examples=["what is 18% of 2450", "calculate 345 times 12", "square root of 144", "15 plus 27 divided by 3"],
)
def calculate(expression: str) -> ToolResult:
    """Do exact math. ALWAYS use this for any arithmetic instead of computing yourself.

    Args:
        expression: The math, e.g. "18% of 2450", "345 * 12", "sqrt(144)", "(15 + 27) / 3".
    """
    try:
        value = safe_eval(expression)
    except ZeroDivisionError:
        return ToolResult.fail("Can't divide by zero.")
    except (ValueError, SyntaxError, TypeError, OverflowError) as e:
        return ToolResult.fail(f"Couldn't calculate '{expression}': {e}. Rewrite it with + - * / and numbers.")
    return ToolResult.success(f"That's {fmt_number(value)}.", value=value)


# --- units --------------------------------------------------------------------

UNIT_ALIASES = {
    "celsius": "degC", "c": "degC", "°c": "degC", "centigrade": "degC",
    "fahrenheit": "degF", "f": "degF", "°f": "degF", "kelvin": "kelvin",
    "kmph": "km/hour", "kph": "km/hour", "km/h": "km/hour", "mph": "mile/hour",
    "lbs": "pound", "lb": "pound", "kgs": "kilogram", "feet": "foot", "ft": "foot",
    "inches": "inch", "in": "inch", "cm": "centimeter", "mm": "millimeter", "km": "kilometer",
    "miles": "mile", "litres": "liter", "liters": "liter", "l": "liter", "ml": "milliliter",
    "gallons": "gallon", "oz": "ounce", "ounces": "ounce", "kg": "kilogram", "g": "gram",
    "grams": "gram", "meters": "meter", "metres": "meter", "m": "meter", "yards": "yard",
}


@lru_cache(maxsize=1)
def _ureg():
    import pint

    return pint.UnitRegistry()


def _unit(name: str) -> str:
    n = name.strip().lower()
    return UNIT_ALIASES.get(n, n)


@tool(
    category="info",
    direct=True,
    examples=["convert 5 miles to km", "how many pounds is 70 kg", "30 celsius in fahrenheit",
              "how many cups in a liter"],
)
def convert_units(value: float, from_unit: str, to_unit: str) -> ToolResult:
    """Convert a measurement between units (length, weight, temperature, volume, speed, ...).

    Args:
        value: The number, e.g. 5.
        from_unit: Unit to convert from, e.g. "miles", "kg", "celsius".
        to_unit: Unit to convert to, e.g. "km", "pounds", "fahrenheit".
    """
    ureg = _ureg()
    try:
        result = ureg.Quantity(value, _unit(from_unit)).to(_unit(to_unit))
    except Exception as e:  # pint raises several error types
        return ToolResult.fail(f"Can't convert {from_unit} to {to_unit}: {e}")
    return ToolResult.success(
        f"{fmt_number(value)} {from_unit} is {fmt_number(round(result.magnitude, 4))} {to_unit}."
    )
