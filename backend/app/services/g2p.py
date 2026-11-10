"""
English text → phonemes, for the Kokoro voice. Offline, no GPL code.

Kokoro reads phonemes, not letters. The usual converters reach for eSpeak (GPL)
or spaCy (which downloads a model); this one uses misaki's American English
pronunciation dictionaries (Apache-2.0, ``g2p_data/``) and misaki's own suffix
rules (-s, -ed, -ing), with:

* a small built-in list for words Cerebro says often that the dictionary lacks;
* numbers, years, times, percentages, money and case numbers said as words;
* acronyms spelled out (``CAS`` → "C A S");
* letter-to-sound rules for anything else, so an unknown name still comes out
  as a sensible attempt rather than silence.

The output uses only symbols in Kokoro's vocabulary.
"""

import gzip
import json
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DATA = Path(__file__).with_name("g2p_data")

STRESS_1, STRESS_2 = "ˈ", "ˌ"
VOWELS = frozenset("AIOQWYaiuæɑɒɔəɛɜɪʊʌᵻ")
US_TAUS = frozenset("AIOWYiuæɑəɛɪɹʊʌ")
PUNCT = ";:,.!?—…\"()“”"

#: Kokoro's phoneme vocabulary (everything else is dropped before synthesis).
VOCAB = frozenset(json.loads((DATA / "kokoro_vocab.json").read_text(encoding="utf-8")))

#: Words Cerebro says that the dictionary doesn't know, or knows wrongly.
EXTRA: Dict[str, str] = {
    "cerebro": "səɹˈɛbɹO", "hehe": "hˈihi", "haha": "hɑhˈɑ", "ehehe": "ɛhˈɛhɛ", "teehee": "tihˈi",
    "uwu": "ˈuwu", "yay": "jˈA", "ooh": "ˈu", "aww": "ˈɔ", "hmm": "hˈʌm", "mhm": "ʌmhˈʌm",
    "okay": "ˌOkˈA", "ok": "ˌOkˈA", "teams": "tˈimz", "outlook": "ˈWtlʊk", "sharepoint": "ʃˈɛɹpYnt",
    "onedrive": "wˈʌndɹIv", "dynamics": "dInˈæmɪks", "rightanswers": "ɹˈItˈænsəɹz",
    "beyondtrust": "biˈɑndtɹʌst", "genesys": "ʤˈɛnəsɪs", "polly": "pˈɑli", "bedrock": "bˈɛdɹɑk",
    "kokoro": "kˈOkəɹO", "wifi": "wˈIfI", "login": "lˈɔɡɪn", "logout": "lˈɔɡWt", "email": "ˈimAl",
    "emails": "ˈimAlz", "url": "jˌuˌɑɹˈɛl", "pdf": "pˌidˌiˈɛf", "vpn": "vˌipˌiˈɛn", "api": "ˌApˌiˈI",
}

_LETTERS = {
    "a": "ˈA", "b": "bˈi", "c": "sˈi", "d": "dˈi", "e": "ˈi", "f": "ˈɛf", "g": "ʤˈi", "h": "ˈAʧ",
    "i": "ˈI", "j": "ʤˈA", "k": "kˈA", "l": "ˈɛl", "m": "ˈɛm", "n": "ˈɛn", "o": "ˈO", "p": "pˈi",
    "q": "kjˈu", "r": "ˈɑɹ", "s": "ˈɛs", "t": "tˈi", "u": "jˈu", "v": "vˈi", "w": "dˈʌbəlju",
    "x": "ˈɛks", "y": "wˈI", "z": "zˈi",
}

_lex: Dict[str, object] = {}
_lock = threading.Lock()


def _lexicon() -> Dict[str, object]:
    """misaki's gold dictionary over its silver one, plus capitalised variants."""
    with _lock:
        if not _lex:
            merged: Dict[str, object] = {}
            for name in ("us_silver.json.gz", "us_gold.json.gz"):     # gold wins
                with gzip.open(DATA / name, "rt", encoding="utf-8") as handle:
                    merged.update(json.load(handle))
            grown = {}
            for key, value in merged.items():
                if len(key) < 2:
                    continue
                if key == key.lower() and key != key.capitalize():
                    grown.setdefault(key.capitalize(), value)
                elif key == key.lower().capitalize():
                    grown.setdefault(key.lower(), value)
            _lex.update({**grown, **merged})
        return _lex


def _pick(entry, tag: str = "DEFAULT") -> Optional[str]:
    if isinstance(entry, dict):
        return entry.get(tag) or entry.get("DEFAULT")
    return entry


# ------------------------------------------------------------- numbers
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
        "nine": "ninth", "twelve": "twelfth"}


def number_words(n: int) -> str:
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if not rest else " and " + number_words(rest))
    for size, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            head = number_words(n // size) + " " + name
            if not rest:
                return head
            return head + (" and " if rest < 100 else " ") + number_words(rest)
    return str(n)


def ordinal_words(n: int) -> str:
    words = number_words(n).split(" ")
    last = words[-1]
    if last in _ORD:
        words[-1] = _ORD[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] = last + "th"
    return " ".join(words)


def _year(n: int) -> str:
    if 2000 <= n <= 2009:
        return number_words(n)
    hi, lo = divmod(n, 100)
    if lo == 0:
        return number_words(hi) + " hundred"
    return number_words(hi) + (" oh " + _ONES[lo] if lo < 10 else " " + number_words(lo))


def _digits(text: str) -> str:
    return " ".join(_ONES[int(c)] for c in text if c.isdigit())


def normalise(text: str) -> str:
    """Numbers, symbols and codes as words, ready to be looked up."""
    text = text.replace("’", "'").replace("‘", "'")
    # Case and ticket numbers (CAS-01234, INC0012345): letters spelled, digits one by one.
    text = re.sub(r"\b([A-Z]{2,5})[-_ ]?(\d{3,})\b",
                  lambda m: m.group(1) + " " + _digits(m.group(2)), text)
    text = re.sub(r"\$(\d+)(?:\.(\d\d))?\b", lambda m: number_words(int(m.group(1))) + " dollars"
                  + (f" and {number_words(int(m.group(2)))} cents" if m.group(2) and int(m.group(2)) else ""),
                  text)
    text = re.sub(r"\b(\d{1,2}):(\d\d)\b", lambda m: number_words(int(m.group(1))) + (
        "" if m.group(2) == "00" else (" oh " + _ONES[int(m.group(2))] if int(m.group(2)) < 10
                                       else " " + number_words(int(m.group(2))))), text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: ordinal_words(int(m.group(1))), text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)%", lambda m: m.group(1) + " percent", text)
    text = re.sub(r"\b(1[1-9]\d\d|20\d\d)\b", lambda m: _year(int(m.group(1))), text)
    text = re.sub(r"\b0\d+\b|\b\d{6,}\b", lambda m: _digits(m.group(0)), text)
    text = re.sub(r"\b(\d+)\.(\d+)\b", lambda m: number_words(int(m.group(1))) + " point " + _digits(m.group(2)),
                  text)
    text = re.sub(r"\b\d{1,3}(?:,\d{3})+\b", lambda m: number_words(int(m.group(0).replace(",", ""))), text)
    text = re.sub(r"\b\d+\b", lambda m: number_words(int(m.group(0))), text)
    for symbol, word in (("&", " and "), ("+", " plus "), ("@", " at "), ("%", " percent "), ("#", " number "),
                         ("~", " "), ("*", " "), ("_", " ")):
        text = text.replace(symbol, word)
    text = re.sub(r"\s*[-–]\s+|\s+[-–]\s*", ", ", text)     # a spaced dash is a pause
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------- dictionary
def _known(word: str) -> bool:
    lex = _lexicon()
    return word in lex or word.lower() in EXTRA or (word.isalpha() and len(word) == 1)


def _lookup(word: str) -> Optional[str]:
    lex = _lexicon()
    lower = word.lower()
    if lower in EXTRA:
        return EXTRA[lower]
    for key in (word, lower, word.capitalize()):
        if key in lex:
            return _pick(lex[key])
    if len(word) == 1 and word.isalpha():
        return _LETTERS[lower]
    return None


def _s(stem: str) -> str:
    if stem[-1] in "ptkfθ":
        return stem + "s"
    if stem[-1] in "szʃʒʧʤ":
        return stem + "ᵻz"
    return stem + "z"


def _ed(stem: str) -> str:
    if stem[-1] in "pkfθʃsʧ":
        return stem + "t"
    if stem[-1] == "d":
        return stem + "ᵻd"
    if stem[-1] != "t":
        return stem + "d"
    if len(stem) >= 2 and stem[-2] in US_TAUS:
        return stem[:-1] + "ɾᵻd"
    return stem + "ᵻd"


def _ing(stem: str) -> str:
    if len(stem) > 1 and stem[-1] == "t" and stem[-2] in US_TAUS:
        return stem[:-1] + "ɾɪŋ"
    return stem + "ɪŋ"


def _stemmed(word: str) -> Optional[str]:
    w = word.lower()
    candidates: List[Tuple[str, callable]] = []
    if w.endswith("'s") and len(w) > 3:
        candidates.append((w[:-2], _s))
    if w.endswith("s") and len(w) > 3:
        if w.endswith("ies") and len(w) > 4:
            candidates.append((w[:-3] + "y", _s))
        if w.endswith("es") and len(w) > 4:
            candidates.append((w[:-2], _s))
        if not w.endswith("ss"):
            candidates.append((w[:-1], _s))
    if w.endswith("ed") and len(w) > 4 and not w.endswith("eed"):
        candidates += [(w[:-2], _ed), (w[:-1], _ed)]
        if len(w) > 5 and w[-3] == w[-4]:
            candidates.append((w[:-3], _ed))
    if w.endswith("ing") and len(w) > 5:
        candidates += [(w[:-3], _ing), (w[:-3] + "e", _ing)]
        if len(w) > 6 and w[-4] == w[-5]:
            candidates.append((w[:-4], _ing))
    for stem, fn in candidates:
        if len(stem) > 1 and _known(stem):
            base = _lookup(stem)
            if base:
                return fn(base)
    return None


def _compound(word: str) -> Optional[str]:
    """A long unknown word made of two known ones (e.g. 'helpdesk')."""
    w = word.lower()
    for cut in range(len(w) - 3, 2, -1):
        head, tail = w[:cut], w[cut:]
        if _known(head) and _known(tail):
            first, second = _lookup(head), _lookup(tail)
            if first and second:
                return first + second.replace(STRESS_1, STRESS_2)
    return None


# ------------------------------------------------- letter-to-sound rules
_RULES = [
    ("tion", "ʃən"), ("sion", "ʒən"), ("ture", "ʧəɹ"), ("ough", "ɔ"), ("augh", "ɔ"), ("eigh", "A"),
    ("igh", "I"), ("tch", "ʧ"), ("sch", "sk"), ("dge", "ʤ"), ("ck", "k"), ("ch", "ʧ"), ("sh", "ʃ"),
    ("th", "θ"), ("ph", "f"), ("wh", "w"), ("ng", "ŋ"), ("qu", "kw"), ("gh", "ɡ"), ("kn", "n"),
    ("wr", "ɹ"), ("ee", "i"), ("ea", "i"), ("ie", "i"), ("oo", "u"), ("ou", "W"), ("ow", "O"),
    ("ai", "A"), ("ay", "A"), ("ei", "A"), ("ey", "A"), ("oi", "Y"), ("oy", "Y"), ("au", "ɔ"),
    ("aw", "ɔ"), ("oa", "O"), ("ue", "u"), ("ew", "u"), ("er", "əɹ"), ("ar", "ɑɹ"), ("or", "ɔɹ"),
    ("ir", "ɜɹ"), ("ur", "ɜɹ"),
]
_SINGLE = {"a": "æ", "e": "ɛ", "i": "ɪ", "o": "ɑ", "u": "ʌ", "b": "b", "c": "k", "d": "d", "f": "f",
           "g": "ɡ", "h": "h", "j": "ʤ", "k": "k", "l": "l", "m": "m", "n": "n", "p": "p", "q": "k",
           "r": "ɹ", "s": "s", "t": "t", "v": "v", "w": "w", "x": "ks", "z": "z"}
_LONG = {"a": "A", "e": "i", "i": "I", "o": "O", "u": "ju"}


def letters_to_sounds(word: str) -> str:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    out: List[str] = []
    i = 0
    while i < len(w):
        rest = w[i:]
        # Magic e: a-consonant-e at the end makes the vowel long ("cake", "Mike").
        if rest[0] in _LONG and len(rest) == 3 and rest[1] not in "aeiouy" and rest[2] == "e":
            out += [_LONG[rest[0]], _SINGLE.get(rest[1], ""), ""]
            break
        for graph, sound in _RULES:
            if rest.startswith(graph):
                out.append(sound)
                i += len(graph)
                break
        else:
            ch = rest[0]
            nxt = rest[1:2]
            if ch == "c" and nxt in ("e", "i", "y"):
                out.append("s")
            elif ch == "g" and nxt in ("e", "i", "y") and i > 0:
                out.append("ʤ")
            elif ch == "y":
                out.append("j" if i == 0 or nxt in ("a", "e", "o", "u") else
                           ("i" if i == len(w) - 1 else "ɪ"))
            elif ch == "a" and i == len(w) - 1 and i > 0:
                out.append("ə")                         # a final a ("Priya", "Anna")
            elif ch == "l" and rest == "le" and i > 0:
                out.append("əl")                        # "-ble", "-tle"
                break
            elif ch == "e" and i == len(w) - 1 and i > 1:
                out.append("")                          # a silent final e
            elif ch == "o" and i == len(w) - 1:
                out.append("O")
            else:
                out.append(_SINGLE.get(ch, ""))
            i += 1
    sounds = "".join(out)
    # Primary stress on the first vowel.
    for index, ch in enumerate(sounds):
        if ch in VOWELS:
            return sounds[:index] + STRESS_1 + sounds[index:]
    return sounds


# ----------------------------------------------------------------- words
def word_phonemes(word: str, next_word: str = "") -> str:
    lower = word.lower()
    vowel_next = bool(next_word) and next_word[0].lower() in "aeiou"
    # Function words that change with what follows.
    if lower == "the":
        return "ði" if vowel_next else "ðə"
    if lower == "a":
        return "ɐ"
    if lower == "an":
        return "ɐn"
    if lower == "to":
        return "tʊ" if vowel_next else "tə"
    if word == "I":
        return "ˌI"
    # Acronyms: all capitals and not a word the dictionary knows as one.
    if word.isupper() and 2 <= len(word) <= 5 and word not in _lexicon():
        return "".join(_LETTERS[c.lower()] for c in word if c.isalpha()).replace(STRESS_1, STRESS_2, len(word) - 1)
    found = _lookup(word)
    if found is None:
        found = _stemmed(word)
    if found is None and len(word) > 6:
        found = _compound(word)
    if found is None:
        found = _guessed(word)
    return found or ""


def _guessed(word: str) -> str:
    """Letter-to-sound rules, keeping English suffix sounds (-s, -ed, -ing)."""
    w = word.lower()
    if w.endswith("ing") and len(w) > 5:
        return _ing(letters_to_sounds(w[:-3]) or "ɪ")
    if w.endswith("ed") and len(w) > 4:
        stem = letters_to_sounds(w[:-1] if w[-3] == "l" else w[:-2])
        return _ed(stem) if stem else letters_to_sounds(w)
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        stem = letters_to_sounds(w[:-1])
        return _s(stem) if stem else letters_to_sounds(w)
    return letters_to_sounds(w)


_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*|[" + re.escape(PUNCT) + r"]")


def phonemize(text: str) -> str:
    """Text → Kokoro phonemes."""
    tokens = _TOKEN.findall(normalise(text))
    out: List[str] = []
    for index, token in enumerate(tokens):
        if token in PUNCT:
            if out and out[-1] == " ":
                out.pop()
            out.append(token)
            out.append(" ")
            continue
        following = next((t for t in tokens[index + 1:] if t not in PUNCT), "")
        out.append(word_phonemes(token, following))
        out.append(" ")
    phonemes = "".join(out).strip()
    return "".join(c for c in phonemes if c in VOCAB)
