"""Curated trade-name → HSN aliases for the "auto-fetch HSN by item name" lookup.

The official HSN descriptions use legalese ("Telephone for cellular networks",
"Automatic data processing machines") that a shopkeeper never types. This map
bridges the everyday trade name a user *does* type (``mobile``, ``laptop``,
``fridge``) to the right heading, so those searches surface the correct code
first — full-text search then covers everything else.

Every code here is verified to exist in ``data/seeds/hsn_codes.json``. Rates shown
in comments are the dataset's standard slab for that heading. Aliases are
surfaced as *suggestions* the user confirms on the Item form — never applied
silently — so a loose match is safe.

Coverage note: this HSN dataset omits textiles/apparel (ch 50-63), footwear (64),
jewellery (71) and misc manufactures (96-98), so no aliases exist for shirts,
shoes, etc. — those chapters simply aren't in the source schedule yet.
"""

import re

# HSN heading (8-digit) → trade names / synonyms users actually type.
_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    # --- Electronics & home appliances ---
    "84180000": ("refrigerator", "fridge", "freezer", "deep freezer"),
    "84150000": ("air conditioner", "ac", "split ac", "window ac", "a/c"),
    "84500000": ("washing machine", "washer"),
    "85171200": ("mobile", "mobile phone", "smartphone", "smart phone",
                 "cellphone", "cell phone", "android phone", "iphone"),
    "85171100": ("telephone", "landline"),
    "84710000": ("laptop", "computer", "desktop", "pc", "notebook computer", "cpu"),
    "84433100": ("printer", "inkjet printer", "laser printer"),
    "85280000": ("television", "tv", "led tv", "lcd tv", "smart tv", "monitor"),
    "84145100": ("fan", "ceiling fan", "table fan", "exhaust fan", "pedestal fan", "wall fan"),
    "85094000": ("mixer", "grinder", "mixer grinder", "juicer", "food processor", "blender"),
    "85165000": ("microwave", "microwave oven"),
    "85166000": ("induction cooktop", "electric cooker", "electric oven", "otg"),
    "85164000": ("iron", "electric iron", "steam iron"),
    "85161000": ("water heater", "geyser", "immersion rod"),
    "85070000": ("battery", "inverter battery", "accumulator", "lead acid battery"),
    "85040000": ("charger", "mobile charger", "adapter", "power adapter", "ups"),
    "85182100": ("speaker", "loudspeaker", "bluetooth speaker"),
    "85390000": ("bulb", "led bulb", "light bulb", "lamp", "tube light", "cfl"),
    "85444900": ("wire", "cable", "electric wire", "electric cable"),
    # --- Food & staples ---
    "10060000": ("rice", "basmati rice"),
    "10010000": ("wheat",),
    "11010000": ("atta", "flour", "wheat flour", "maida"),
    "17019900": ("sugar",),
    "25010010": ("salt", "common salt", "table salt"),
    "09020000": ("tea", "tea leaves"),
    "09010000": ("coffee",),
    "04012000": ("milk", "fresh milk"),
    # --- FMCG / personal care ---
    "34011900": ("soap", "bathing soap", "bath soap"),
    "33051010": ("shampoo",),
    "33061020": ("toothpaste", "tooth paste"),
    "34022000": ("detergent", "washing powder", "detergent powder"),
    # --- Hardware / construction ---
    "25230000": ("cement", "portland cement"),
    "72142000": ("tmt bar", "steel bar", "reinforcement bar", "rebar", "iron rod", "steel rod", "tmt"),
    "73181500": ("screw", "bolt", "nut", "fastener"),
    "32090000": ("paint", "wall paint", "enamel paint", "emulsion paint"),
    # --- Furniture / home ---
    "94033000": ("furniture", "office furniture", "wooden furniture"),
    "94010000": ("chair", "seat", "sofa", "office chair"),
    "94042900": ("mattress", "foam mattress"),
    # --- Stationery ---
    "48201000": ("notebook", "exercise book", "register", "diary"),
    # --- Auto / misc ---
    "40111000": ("tyre", "tire", "tyres"),
    "65061000": ("helmet",),
    "49010000": ("book", "books", "textbook"),
    "91011100": ("watch", "wrist watch", "wristwatch"),
    "95030000": ("toy", "toys"),
    "66011000": ("umbrella",),
    "42022200": ("handbag", "hand bag", "purse", "ladies bag"),
    # --- Apparel / footwear / textiles (supplement chapters) ---
    "61091000": ("t-shirt", "tshirt", "t shirt", "tee shirt"),
    "62052000": ("shirt", "mens shirt", "formal shirt"),
    "61051000": ("casual shirt",),
    "62034200": ("trouser", "trousers", "pant", "pants", "jeans", "denim"),
    "62114200": ("saree", "sari", "saree cotton"),
    "62114900": ("kurta", "kurti", "kurta pyjama"),
    "62044200": ("dress", "frock", "ladies dress"),
    "61102000": ("sweater", "pullover", "cardigan"),
    "62011900": ("jacket", "coat", "overcoat"),
    "61159500": ("socks", "sock"),
    "62063000": ("blouse", "top", "ladies top"),
    "63023100": ("bedsheet", "bed sheet", "bed linen"),
    "63026000": ("towel", "hand towel", "bath towel"),
    "63019000": ("blanket", "rug"),
    "63039900": ("curtain", "curtains"),
    "52081000": ("fabric", "cotton fabric", "cloth", "cotton cloth"),
    "50072000": ("silk", "silk fabric"),
    "64039900": ("shoe", "shoes", "leather shoes"),
    "64029900": ("sandal", "slipper", "chappal", "flip flop", "footwear"),
    "64041900": ("sneaker", "sports shoe", "canvas shoe"),
    # --- Jewellery ---
    "71131900": ("gold", "gold jewellery", "gold chain", "gold ring"),
    "71131100": ("silver", "silver jewellery", "silver ornament"),
    "71171900": ("imitation jewellery", "artificial jewellery", "jewellery", "jewelry", "bangles"),
    # --- Stationery / misc (supplement) ---
    "96081000": ("pen", "ball pen", "ballpoint pen", "ball point pen"),
    "96091000": ("pencil", "pencils"),
    "96032100": ("toothbrush", "tooth brush"),
    "96031000": ("broom", "broomstick", "jhadu"),
    "96151100": ("comb",),
    "96190010": ("sanitary napkin", "sanitary pad"),
    # --- Fertilizers ---
    "31021000": ("urea",),
    "31052000": ("fertilizer", "fertiliser", "npk"),
    "31010000": ("manure", "organic manure", "compost"),
}

# phrase → codes (a phrase can, in principle, map to more than one heading).
_PHRASE_TO_CODES: dict[str, list[str]] = {}
for _code, _phrases in _ALIAS_MAP.items():
    for _p in _phrases:
        _PHRASE_TO_CODES.setdefault(_p, []).append(_code)

_WORD = re.compile(r"[a-z0-9]+")


def lookup_alias_codes(query: str) -> list[str]:
    """HSN codes whose trade-name alias matches ``query`` (empty if none).

    Multi-word aliases ("air conditioner") match as a phrase substring;
    single-word aliases ("fridge", "ac") match only as a whole word, so "ac"
    does not fire on "back" or "jacket"."""
    q = " ".join(_WORD.findall(query.lower()))
    if not q:
        return []
    words = set(q.split())
    out: list[str] = []
    seen: set[str] = set()
    for phrase, codes in _PHRASE_TO_CODES.items():
        hit = phrase in q if " " in phrase else phrase in words
        if hit:
            for code in codes:
                if code not in seen:
                    seen.add(code)
                    out.append(code)
    return out
