"""winget's name and publisher normalization

A port of ``NormalizationInitial`` in winget-cli's ``src/AppInstallerCommonCore/NameNormalization.cpp`` (MIT).
"""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from functools import cache

ARCHITECTURES = ("X86", "X64", "Arm", "Arm64")
_SPACE = " \t\r\n\f\v"

LOCALES = frozenset(locale.casefold() for locale in """
    AF-ZA AM-ET AR-AE AR-BH AR-DZ AR-EG AR-IQ AR-JO AR-KW AR-LB AR-LY AR-MA ARN-CL AR-OM AR-QA AR-SA AR-SY AR-TN
    AR-YE AS-IN BA-RU BE-BY BG-BG BN-BD BN-IN BO-CN BR-FR CA-ES CA-ES-VALENCIA CO-FR CS-CZ CY-GB DA-DK DE-AT DE-CH
    DE-DE DE-LI DE-LU DSB-DE DV-MV EL-GR EN-AU EN-BZ EN-CA EN-GB EN-IE EN-IN EN-JM EN-MY EN-NZ EN-PH EN-SG EN-TT
    EN-US EN-ZA EN-ZW ES-AR ES-BO ES-CL ES-CO ES-CR ES-DO ES-EC ES-ES ES-GT ES-HN ES-MX ES-NI ES-PA ES-PE ES-PR
    ES-PY ES-SV ES-US ES-UY ES-VE ET-EE EU-ES FA-IR FI-FI FIL-PH FO-FO FR-BE FR-CA FR-CH FR-FR FR-LU FR-MC FY-NL
    GA-IE GD-DB GL-ES GSW-FR GU-IN HE-IL HI-IN HR-BA HR-HR HSB-DE HU-HU HY-AM ID-ID IG-NG II-CN IS-IS IT-CH IT-IT
    JA-JP KA-GE KK-KZ KL-GL KM-KH KN-IN KOK-IN KO-KR KY-KG LB-LU LO-LA LT-LT LV-LV MI-NZ MK-MK ML-IN MN-MN MOH-CA
    MR-IN MS-BN MS-MY MT-MT NB-NO NE-NP NL-BE NL-NL NN-NO NSO-ZA OC-FR OR-IN PA-IN PL-PL PRS-AF PS-AF PT-BR PT-PT
    QUT-GT QUZ-BO QUZ-EC QUZ-PE RM-CH RO-RO RU-RU RW-RW SAH-RU SA-IN SE-FI SE-NO SE-SE SI-LK SK-SK SL-SI SMA-NO
    SMA-SE SMJ-NO SMJ-SE SMN-FI SMS-FI SQ-AL SV-FI SV-SE SW-KE SYR-SY TA-IN TE-IN TH-TH TK-TM TN-ZA TR-TR TT-RU
    UG-CN UK-UA UR-PK VI-VN WO-SN XH-ZA YO-NG ZH-CN ZH-HK ZH-MO ZH-SG ZH-TW ZU-ZA AZ-CYRL-AZ AZ-LATN-AZ BS-CYRL-BA
    BS-LATN-BA HA-LATN-NG IU-CANS-CA IU-LATN-CA MN-MONG-CN SR-CYRL-BA SR-CYRL-CS SR-CYRL-ME SR-CYRL-RS SR-LATN-BA
    SR-LATN-CS SR-LATN-ME SR-LATN-RS TG-CYRL-TJ TZM-LATN-DZ UZ-CYRL-UZ UZ-LATN-UZ
""".split())

LEGAL_SUFFIXES = frozenset(suffix.casefold() for suffix in """
    AB AD AG APS AS ASA BV CO CV DOO eV GES GESMBH GMBH INC KG KS PS LLC LP LTD LTDA MBH NV PLC SL PTY PVT SA SARL
    SC SCA SP SPA SRL SRO COMPANY CORP CORPORATION HOLDING HOLDINGS INCORPORATED LIMITED SUBSIDIARY
""".split())


@dataclass(frozen=True)
class Normalized:
    name: str
    publisher: str
    architecture: str | None = None
    locale: str = ""

    def name_with_architecture(self) -> str:
        """The name as winget indexes it with its architecture: ``python(X64)``"""
        return f"{self.name}({self.architecture})" if self.architecture else self.name


def normalize(name: str, publisher: str) -> Normalized:
    """``name`` and ``publisher`` as winget normalizes them, case kept. Fold them first to match winget's index"""
    patterns = _patterns()
    normalized, architecture, locale = _name(name, patterns)
    return Normalized(normalized, _publisher(publisher, patterns), architecture, locale)


def for_index(name: str, publisher: str) -> Normalized:
    """``normalize`` as winget's index search does it: case folded first"""
    return normalize(name.casefold(), publisher.casefold())


def _prepare(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip(_SPACE)
    at = value.find("@@", 3)
    value = value[:at] if at != -1 else value
    while len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "(" and value[-1] == ")")):
        value = value[1:-1]
    return value


def _remove(pattern: re.Pattern[str], value: str) -> tuple[str, bool]:
    result = pattern.sub("", value)
    return result, result != value


def _remove_all(patterns: list[re.Pattern[str]], value: str) -> tuple[str, bool]:
    changed = False
    for pattern in patterns:
        value, removed = _remove(pattern, value)
        changed = removed or changed
    return value, changed


def _split(pattern: re.Pattern[str], value: str, stop_on_exclusion: bool = False) -> list[str]:
    """``value`` cut at each match, matches kept as pieces of their own, as ICU's ``ForEach`` hands them over"""
    tokens: list[str] = []
    position = 0
    pieces = []
    for match in pattern.finditer(value):
        if match.start() > position:
            pieces.append(value[position:match.start()])
        pieces.append(match.group(0))
        position = match.end()
    if position < len(value):
        pieces.append(value[position:])
    for piece in pieces:
        if not piece.strip(_SPACE):
            continue
        # The first word is kept even if it's a legal suffix
        if tokens and piece.casefold() in LEGAL_SUFFIXES:
            if stop_on_exclusion:
                break
            continue
        tokens.append(piece)
    return tokens


def _name(value: str, p: _Patterns) -> tuple[str, str | None, str]:
    value = _prepare(value)
    if p.sap_package.fullmatch(value):
        return value, None, ""

    architecture = None
    value, found = _remove(p.arch_32_or_64_bit, value)
    if not found:
        for patterns, arch in (
            ((p.arch_x64, p.arch_64_bit), "X64"), ((p.arch_x32, p.arch_32_bit), "X86"),
            ((p.arch_arm64,), "Arm64"), ((p.arch_arm,), "Arm"),
        ):
            for pattern in patterns:
                value, found = _remove(pattern, value)
                if found:
                    architecture = arch
                    break
            if found:
                break

    value, locale = _remove_locale(value, p)
    value = p.kb_numbers.sub(r"\1", value)
    changed = True
    while changed:
        value, changed = _remove_all(p.program_name, value)
    value = "".join(_split(p.program_name_split, value))
    return p.non_letters_and_digits.sub("", value), architecture, locale


def _remove_locale(value: str, p: _Patterns) -> tuple[str, str]:
    found = False
    common = ""
    kept = []
    position = 0
    for match in p.locale.finditer(value):
        kept.append(value[position:match.start()])
        position = match.end()
        text = match.group(0).casefold()
        if text not in LOCALES:
            kept.append(match.group(0))
        elif not found:
            common, found = text, True
        elif common and common != text:
            # Several: keep only the language they share, if they do
            if common.split("-", 1)[0] != text.split("-", 1)[0]:
                common = ""
            else:
                common = common.split("-", 1)[0]
    kept.append(value[position:])
    return "".join(kept), common


def _publisher(value: str, p: _Patterns) -> str:
    value = _prepare(value)
    changed = True
    while changed:
        value, changed = _remove_all(p.publisher_name, value)
    value = "".join(_split(p.publisher_name_split, value, stop_on_exclusion=True))
    return p.non_letters_and_digits.sub("", value)


def _category(*categories: str) -> str:
    """A character class body of every code point in the Unicode ``categories``, as ranges"""
    table = _categories()
    return "".join(table.get(category, "") for category in categories)


@cache
def _categories() -> dict[str, str]:
    """The categories winget's patterns use, each as character class ranges"""
    wanted = {"Pd", "Pc", "Po", "Ps", "Pe", "Zs", "Zl", "Zp"}
    ranges: dict[str, list[list[int]]] = {category: [] for category in wanted}
    for code in range(sys.maxunicode + 1):
        category = unicodedata.category(chr(code))
        if category in wanted:
            spans = ranges[category]
            if spans and spans[-1][1] == code - 1:
                spans[-1][1] = code
            else:
                spans.append([code, code])
    return {
        category: "".join(
            re.escape(chr(start)) + ("" if start == end else "-" + re.escape(chr(end))) for start, end in spans
        )
        for category, spans in ranges.items()
    }


class _Patterns:
    def __init__(self) -> None:
        L = "[^\\W\\d_]"
        notL = "[\\W\\d_]"
        Nd, notNd = r"\d", r"\D"
        LNd = "[^\\W_]"
        notLNd = "[\\W_]"
        Pd, Pc, Po = _category("Pd"), _category("Pc"), _category("Po")
        Ps, Pe, Z = _category("Ps"), _category("Pe"), _category("Zs", "Zl", "Zp")
        Lu = L
        start = f"(?:^|(?<={notLNd}))"
        flags = re.IGNORECASE

        def c(pattern: str) -> re.Pattern[str]:
            return re.compile(pattern, flags)

        self.arch_x32 = c(rf"{start}(X32|X86)(?={notNd}|$)(?:\sEDITION)?")
        self.arch_x64 = c(rf"{start}(X64|AMD64|X86([{Pd}{Pc}]64))(?={notNd}|$)(?:\sEDITION)?")
        self.arch_arm64 = c(rf"{start}(ARM64|AARCH64)(?={notNd}|$)(?:\sEDITION)?")
        self.arch_arm = c(rf"{start}(ARM32)(?={notNd}|$)(?:\sEDITION)?")
        self.arch_32_bit = c(rf"{start}(32[{Pd}{Pc}{Z}]?BIT)S?(?:\sEDITION)?")
        self.arch_64_bit = c(rf"{start}(64[{Pd}{Pc}{Z}]?BIT)S?(?:\sEDITION)?")
        self.arch_32_or_64_bit = c(rf"{start}((64[\\/]32|32[\\/]64)[{Pd}{Pc}{Z}]?BIT)S?(?:\sEDITION)?")

        self.locale = c(rf"(?<![A-Z])((?:{Lu}{{2,3}}(-(CANS|CYRL|LATN|MONG))?-{Lu}{{2}})(?![A-Z])(?:-VALENCIA)?)")
        self.sap_package = c(
            rf"(?:[^\W_]+[\._])+[^\W_]+(?:-(?:{Nd}+\.)+{Nd}+)(?:-(?:{Lu}{{2}}(?:_{Lu}{{2}})?|CORE))(?:-(?:{Lu}{{2}}|{Nd}{{2}}))"
        )
        self.kb_numbers = c(r"\((KB\d+)\)")
        self.non_letters_and_digits = c(notLNd)

        uri_protocol = c(rf"(?<!{L})(?:http[s]?|ftp)://")
        words = "V|VER|VERSI(?:O|Ó)N|VERSÃO|VERSIE|WERSJA|BUILD|RELEASE|RC|SP"
        version_delimited = c(
            rf"((?<!{L})(?:{words}){notL}?)?{Nd}+([{Po}{Pd}{Pc}]{Nd}?(RC|B|A|R|SP|K)?{Nd}+)+([{Po}{Pd}{Pc}]?{LNd}+)*"
        )
        version = c(
            rf"(FOR\s)?(?<!{L})(?:P|R|{words})(?:{notL}|{notL}{L})?({Nd}|\.{Nd})+(?:RC|B|A|R|V|SP)?{Nd}?"
        )
        version_letter = c(rf"(?<!{L})(?:(?:{words}){notL})?{Lu}{Nd}+(?:[{Po}{Pd}{Pc}]{Nd}+)+")
        non_nested_bracket = c(r"\([^\(\)]*\)|\[[^\[\]]*\]")
        bracket_enclosed = c(rf'(?:[{Ps}].*[{Pe}]|".*")')
        leading_symbols = c(rf"^{notLNd}+")
        trailing_non_letters = c(rf"{notL}+$")
        prefix_parens = c(r"^\(.*?\)")
        empty_parens = c(r'(\(\s*\)|\[\s*\]|"\s*")')
        en = c(r"\sEN\s*$")
        trailing_symbols = c(rf"{notLNd}+$")
        file_path = c(r"((INSTALLED\sAT|IN)\s)?[CDEF]:\\(?:.+\\)?[^\s]*\\?")
        file_path_ghs = c(r"\(CHANGE #\d{1,2} TO [CDEF]:\\(?:.+\\)?[^\s]*\\?\)")
        file_path_parens = c(r"\([CDEF]:\\(?:.+\\)?[^\s]*\\?\)")
        file_path_quotes = c(r'"[CDEF]:\\(?:.+\\)?[^\s]*\\?"')
        roblox = _Keep(c(r"^(ROBLOX\s(?:PLAYER|STUDIO))\sFOR\s.*"))
        bomgar = _Keep(c(r"^(BOMGAR\s(?:JUMP CLIENT|(?:ACCESS|REPRESENTATIVE) CONSOLE|BUTTON)|EMBEDDED CALLBACK)\s.*"))
        acronym_separators = c(rf"(?:(?<=^{L})|(?<={notL}{L}))(\.|/)(?={L}(?:{notL}|$))")
        non_letters = c(r"(?:^|(?<=\s))(?:[\W\d_])+(?=\s|$)")

        self.program_name = [
            roblox, bomgar, prefix_parens, empty_parens, file_path_ghs, file_path_parens, file_path_quotes, file_path,
            version_letter, version_delimited, version, en, non_nested_bracket, bracket_enclosed, uri_protocol,
            leading_symbols, trailing_symbols,
        ]
        self.publisher_name = [
            version_delimited, version, non_nested_bracket, bracket_enclosed, uri_protocol, non_letters,
            trailing_non_letters, acronym_separators,
        ]
        self.program_name_split = c(r"[^\w+&]|_")
        self.publisher_name_split = c(notLNd)


class _Keep:
    """A pattern whose match is replaced by its first group, standing in for winget's lookbehind"""

    def __init__(self, pattern: re.Pattern[str]) -> None:
        self.pattern = pattern

    def sub(self, _: str, value: str) -> str:
        return self.pattern.sub(r"\1", value)


@cache
def _patterns() -> _Patterns:
    return _Patterns()
