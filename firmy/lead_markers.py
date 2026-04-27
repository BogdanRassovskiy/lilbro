"""Marker lists for lead-trait classification."""

# Rejecting markers (regex over raw lowercased text in _derive_response_type).
REJECTING_REGEXES = (
    r"\bне\s*интерес(но|ует)?\b",
    r"\bне\s*актуальн[оа]\b",
    r"\bне\s*нужн[оа]\b",
    r"\bне\s*пишите\b",
    r"\bбольше\s*не\s*пишите\b",
    r"\bудалите\s*меня\b",
    r"\bотпишит(е|есь)\b",
    r"\bno\s*interest(ed)?\b",
    r"\bnot\s*interested\b",
    r"\bstop\s*(email|mail|messag|writing)\b",
    r"\bdo\s*not\s*contact\b",
    r"\bunsubscribe\b",
    r"\bnezájem\b",
    r"\bnezajem\b",
    r"\bnemám\s*zájem\b",
    r"\bnemam\s*zajem\b",
    r"\bnemáme\s*zájem\b",
    r"\bnemame\s*zajem\b",
    r"\bnemáme\s+zájem\b",
    r"\bnemame\s+zajem\b",
    r"\bnepsat\b",
    r"\bnepište\b",
    r"\bpros[ií]m\s+nepi[sš]te\b",
    r"\bu[zž]\s+n[aá]s\s+nekontaktujte\b",
    r"\bnekontaktujte\s+n[aá]s\b",
    # Not bare \bnechceme\b — false positives e.g. "Nechceme narušit provoz".
    r"\bnechceme\s+spolupracovat\b",
    r"\bnechci\s+spolupracovat\b",
    r"\bnem[aá]me\s+z[aá]jem\b",
    r"\bnebudeme\s+m[ií]t\s+z[aá]jem\b",
    r"\bodm[ií]t[aá]me\b",
    r"\bodm[ií]t[aá]m\b",
    r"\bodm[ií]tnuto\b",
    r"\bzam[ií]t[aá]me\b",
    r"\bodmí(t|tnut|tám|tam)\b",
    r"\bodmit(nut|am|at)?\b",
)

# Rejecting markers (simple substring checks over raw lowercased text).
REJECTING_SUBSTRINGS = (
    # direct rejection
    "не интересно",
    "not interested",
    "nezájem",
    "odmit",
    "odmít",
    "nezajem",
    "nemáme zájem",
    "don't write",
    "nemame zajem",
    "nemáme zájem",

    # do not write / do not contact
    "prosím nepište",
    "prosim nepište",
    "prosim nepiste",
    "už nás nekontaktujte",
    "uz nas nekontaktujte",
    "nekontaktujte nás",
    "nekontaktujte nas",
    "не пишите",
    "не надо писать",
    "не беспокойте",
    "не связывайтесь",

    # already resolved / not актуально
    "už máme",
    "uz mame",
    "už jsme našli",
    "uz jsme nasli",
    "už vyřešeno",
    "uz vyreseno",
    "already found",
    "already rented",
    "not needed anymore",
    "už není aktuální",
    "uz neni aktualni",
    "не актуально",
    "уже нашли",
    "уже сняли",
    "уже решили",
    "v soucasne dobe to nepotrebujeme",
    "v současné době to nepotřebujeme",
    "to nepotrebujeme",
    "to nepotřebujeme",
    "nepotrebujeme",
    "nepotřebujeme",

    # short explicit rejection
    "díky ne",
    "diky ne",
    "no thanks",
    "thanks no",
    "не спасибо",
    "нет спасибо",

    # soft decline
    "maybe later",
    "možná později",
    "mozna pozdeji",
    "позже",
    "давайте потом",
    "я подумаю",
    "ozvu se",
    "dám vědět",
    "дам знать",

    # busy / not now
    "busy now",
    "not now",
    "teď ne",
    "ted ne",
    "сейчас не",
    "занят",
    "занята",

    # aggressive rejection
    "stop texting",
    "leave me alone",
    "не пиши больше",
    "отстань",
    "иди нах",
)

# Czech rejecting markers (regex over normalized text without diacritics).
CZECH_REJECTING_NORMALIZED_REGEXES = (
    r"\bnezajem\b",
    r"\bnezajima\b",
    r"\bnazajima\b",
    r"\bto nas nezajima\b",
    r"\bto nas nazajima\b",
    r"\bnezajima nas\b",
    r"\bnazajima nas\b",
    r"\bnemam(?:e)? zajem\b",
    # Not bare nechci/nechceme — matches "nechceme narusit beh provozu" etc.
    r"\bodmit(?:am|ame|nout)\b",
    r"\bzamit(?:am|ame|nuto)\b",
    r"\bpros(?:im)? nepiste\b",
    r"\bnepiste\b",
    r"\bnekontaktujte nas\b",
    r"\buz nas nekontaktujte\b",
    r"\bnebudeme mit zajem\b",
    r"\bdekujeme\s+ale\b",
    r"\bdekujeme\s+ale\s+.*\bnezajima\b",
    r"\bdekujeme\s+ale\s+.*\bnazajima\b",
    r"\bnashledanou\b",
    r"\bna shledanou\b",
    r"\bbez\s+zajmu\b",
    r"\bnemame\s+o\s+to\s+zajem\b",
    r"\bnemam\s+o\s+to\s+zajem\b",
    r"\bnechceme\s+spolupracovat\b",
    r"\bnechci\s+spolupracovat\b",
    r"\b(?:v\s+soucasne\s+dobe\s+)?to\s+nepotrebujeme\b",
    r"\bnepotrebujeme\b",
)

# Generic rejecting markers used in quick checks over raw text.
LOOKS_REJECTING_REGEXES = (
    r"\bне интерес\w*",
    r"\bнам не (подходит|актуально|нужно)\b",
    r"\bне нужно\b",
    r"\bне надо\b",
    r"\bне пишите\b",
    r"\bбольше не (пишите|нужно)\b",
    r"\bоткаж\w*",
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\bnot interested\b",
    r"\bplease stop\b",
    r"\bnez[aá]jem\b",
    r"\bnem[aá]m[e]?\s+z[aá]jem\b",
    r"\bpros[ií]m\s+nepi[sš]te\b",
    r"\bu[zž]\s+n[aá]s\s+nekontaktujte\b",
    r"\bnekontaktujte\s+n[aá]s\b",
    r"\bnechceme\s+spolupracovat\b",
    r"\bnechci\s+spolupracovat\b",
    r"\bnebudeme\s+m[ií]t\s+z[aá]jem\b",
    r"\bodm[ií]t(a|á)m(e)?\b",
    r"\bodm[ií]tnuto\b",
    r"\bzam[ií]t(a|á)m(e)?\b",
)

# Markers by response_type classification.
PRICE_SENSITIVE_SUBSTRINGS = (
    "сколько стоит",
    "цена",
    "стоимость",
    "почем",
    "price",
    "cost",
    "how much",
    "kolik stoji",
    "kolik stojí",
    "cena",
    "drahe",
    "drahé",
    "levne",
    "levné",
)

ASKING_INFO_SUBSTRINGS = (
    "?",
    "как",
    "какой",
    "какая",
    "какие",
    "когда",
    "где",
    "почему",
    "зачем",
    "сколько",
    "what",
    "how",
    "why",
    "when",
    "where",
    "which",
    "kolik",
    "proc",
    "proč",
    "jak",
    "kde",
    "kdy",
)

INTERESTED_SUBSTRINGS = (
    "интерес",
    "interested",
    "давайте",
    "хочу",
    "готов",
    "беру",
    "let's",
    "ok beru",
    "mam zajem",
    "mám zájem",
    "ano",
    "yes",
    "ok",
    "okey",
)

HESITATING_SUBSTRINGS = (
    "подумаю",
    "не уверен",
    "сомнева",
    "может быть",
    "maybe",
    "not sure",
    "uvidime",
    "uvidíme",
    "rozmyslim",
    "rozmyslím",
)

BUSY_LATER_SUBSTRINGS = (
    "позже",
    "потом",
    "давай позже",
    "напиши позже",
    "busy",
    "later",
    "not now",
    "сейчас не",
    "занят",
    "занята",
    "nyni ne",
    "teď ne",
    "ted ne",
)
# Positive interest markers in normalized Czech text.
CZECH_POSITIVE_INTEREST_NORMALIZED_REGEXES = (
    r"\bmam(?:e)? zajem\b",
    r"\bzajima me to\b",
    r"\bto me zajima\b",
    r"\bto nas zajima\b",
)

# Polite-thanks neutral guard (normalized text).
POLITE_THANKS_NORMALIZED_REGEXES = (
    r"\bdekuji\b",
    r"\bdekuju\b",
    r"\bdik(?:y|y moc)?\b",
    r"\bthank(?:s| you)\b",
    r"\bspasibo\b",
    r"\bdekujeme\b",
)
POLITE_THANKS_QUESTION_OR_PRICE_NORMALIZED_REGEXES = (
    r"\?",
    r"\bkolik\b",
    r"\bcena\b",
    r"\bprice\b",
    r"\bjak\b",
    r"\bproc\b",
    r"\bco\b",
    r"\bwhat\b",
    r"\bhow\b",
    r"\bwhy\b",
)
POLITE_THANKS_EXPLICIT_INTEREST_NORMALIZED_REGEXES = (
    r"\bmam(?:e)? zajem\b",
    r"\bzajima me\b",
    r"\bto me zajima\b",
    r"\bto nas zajima\b",
    r"\binterested\b",
    r"\bzajem\b",
)

# Communication-style markers (raw lowercased incoming text).
STYLE_FORMAL_SUBSTRINGS = ("добрый день", "уважа", "s pozdravem", "dear", "good day")
STYLE_FRIENDLY_SUBSTRINGS = ("super", "👍", "😊")
STYLE_DIRECT_SUBSTRINGS = ("короче", "по делу", "just", "directly")

# Outgoing bot-signoff marker: if bot sends this + leaves contacts, classify as lost.
LOST_OUTBOUND_SIGNOFF_SUBSTRINGS = (
    "uz nebudu rusit",
    "nebudu vas rusit",
    "nebudu více rušit",
    "nebudu vic rusit",
    "pokud se situace zmeni",
    "kdykoli se muzete ozvat",
    "dekuji za odpoved",
    "dekuji za zpetnou vazbu",
    "dekuji za informaci",
    "rozumim",
    "pro pripad budouci potreby",
    "zustavam k dispozici",
    "k dispozici",
    "preji hezky den",
    "hezky den",
    "already no longer relevant",
    "i won't bother you anymore",
)
LOST_OUTBOUND_CONTACT_SUBSTRINGS = (
    "s pozdravem",
    "kontakt",
    "email",
    "emailu",
    "telefon",
    "telefonu",
    "na telefonu",
    "tel:",
    "@",
)
LOST_OUTBOUND_CONTACT_REGEXES = (
    # Generic e-mail
    r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}",
    # Phone-like number (at least 8 digits with separators)
    r"(?:\+?\d[\d\s().\-]{6,}\d)",
)
