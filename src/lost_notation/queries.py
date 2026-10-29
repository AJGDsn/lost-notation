"""Auditable, priority-tiered multilingual search-query generation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
import json

GENERATOR_VERSION = "0.4"
TIER_NAMES = {
    "focused": "Focused specialist terms",
    "expanded": "Expanded conceptual terms",
    "broad": "Broad discovery terms",
}

# Each language uses the hobby's own vocabulary where one exists (neography,
# néographie, 創作文字 …), not only literal translations of the English terms.
# Chinese lists simplified and traditional forms where they differ.
CONCEPT_TIERS = {
    "focused": {
        "en": ["shorthand", "speedwriting", "stenography", "pasigraphy", "pasigraphic writing", "ideography", "ideographic writing", "neography", "conscript"],
        "fr": ["sténographie", "pasigraphie", "écriture idéographique", "néographie"],
        "de": ["Kurzschrift", "Stenografie", "Pasigrafie", "ideografische Schrift"],
        "es": ["taquigrafía", "pasigrafía", "escritura ideográfica", "neografía"],
        "pt": ["taquigrafia", "estenografia", "pasigrafia", "escrita ideográfica", "neografia"],
        "it": ["stenografia", "tachigrafia", "pasigrafia", "scrittura ideografica", "neografia"],
        "ru": ["стенография", "пасиграфия", "идеографическое письмо", "искусственная письменность"],
        "ja": ["速記", "創作文字", "人工文字", "自作文字"],
        "zh": ["速记", "人造文字", "自创文字", "自創文字"],
    },
    "expanded": {
        "en": ["symbolic writing", "semantic writing", "ideographic language", "visual language", "semantic language", "concept language", "universal writing", "philosophical language", "glyph system"],
        "fr": ["écriture symbolique", "langage idéographique", "langage visuel", "langage sémantique"],
        "de": ["symbolische Schrift", "ideografische Sprache", "visuelle Sprache", "semantische Schrift"],
        "es": ["escritura simbólica", "lenguaje ideográfico", "lenguaje visual", "escritura semántica"],
        "pt": ["escrita simbólica", "linguagem ideográfica", "linguagem visual", "escrita semântica"],
        "it": ["scrittura simbolica", "linguaggio ideografico", "linguaggio visivo", "scrittura semantica"],
        "ru": ["символическое письмо", "семантическое письмо", "визуальный язык", "неография"],
        "ja": ["架空文字", "人工言語", "視覚言語"],
        "zh": ["人造语言", "符号语言", "视觉语言"],
    },
    "broad": {
        "en": ["invented alphabet", "constructed script", "personal notation", "symbolic notation", "knowledge representation", "diagram language"],
        "fr": ["alphabet inventé", "système d'écriture inventé", "notation personnelle", "idéolangue"],
        "de": ["erfundenes Alphabet", "eigenes Schriftsystem", "persönliche Notation", "Kunstschrift", "Kunstsprache"],
        "es": ["alfabeto inventado", "sistema de escritura inventado", "notación personal", "lengua construida"],
        "pt": ["alfabeto inventado", "sistema de escrita inventado", "notação pessoal", "língua construída"],
        "it": ["alfabeto inventato", "sistema di scrittura inventato", "notazione personale", "lingua artificiale"],
        "ru": ["придуманный алфавит", "придуманная письменность", "искусственный язык", "конланг"],
        "ja": ["オリジナル文字", "記号言語"],
        "zh": ["发明文字", "造字"],
    },
}

# Phrases are matched as exact quoted strings, so they must be fragments that
# occur verbatim in natural text.  German, for example, separates the verb
# ("ich habe ein Alphabet entworfen"), so it uses "selbst entworfen" instead.
FIRST_PERSON = {
    "en": ["I designed", "I invented", "I created", "I developed", "I've been working on", "I have been working on", "my writing system", "my notation", "my symbols", "my script", "my alphabet", "my glyphs"],
    "fr": ["j'ai conçu", "j'ai inventé", "j'ai créé", "mon système d'écriture", "mon propre alphabet", "ma notation", "mes symboles"],
    "de": ["selbst entworfen", "selbst erfunden", "selbst entwickelt", "habe ich entworfen", "habe ich erfunden", "mein eigenes Alphabet", "meine eigene Schrift", "mein Schriftsystem", "meine Notation"],
    "es": ["diseñé", "inventé", "creé", "desarrollé", "mi sistema de escritura", "mi propio alfabeto", "mi notación", "mis símbolos"],
    "pt": ["eu criei", "eu inventei", "eu desenvolvi", "meu sistema de escrita", "meu próprio alfabeto", "minha notação", "meus símbolos"],
    "it": ["ho inventato", "ho creato", "ho ideato", "ho progettato", "il mio sistema di scrittura", "il mio alfabeto", "la mia notazione", "i miei simboli"],
    "ru": ["я придумал", "я придумала", "я создал", "я создала", "я разработал", "моя письменность", "мой алфавит", "мои символы"],
    "ja": ["作ってみた", "自分で作った", "考えた文字", "作った文字", "私の文字"],
    "zh": ["我发明了", "我设计了", "我创造了", "我自创的", "我的文字"],
}


def concepts_for_tiers(tiers: list[str]) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {language: [] for language in FIRST_PERSON}
    for tier in tiers:
        for language, terms in CONCEPT_TIERS[tier].items():
            selected[language].extend(term for term in terms if term not in selected[language])
    return selected


CONCEPTS = concepts_for_tiers(list(TIER_NAMES))


def concept_tier(language: str, concept: str) -> str:
    """The built-in tier of a concept; terms the researcher added are "custom"."""
    for tier, languages in CONCEPT_TIERS.items():
        if concept in languages.get(language, []):
            return tier
    return "custom"


@dataclass(frozen=True)
class Query:
    text: str
    language: str
    concept: str
    phrase: str | None
    tier: str
    generator_version: str = GENERATOR_VERSION


def generate_queries(
    languages: list[str] | None = None,
    concepts: dict[str, list[str]] | None = None,
    phrases: dict[str, list[str]] | None = None,
    include_standalone: bool = True,
) -> list[Query]:
    """Generate queries from an explicit selection; defaults to the focused tier."""
    selected_languages = languages or list(FIRST_PERSON)
    selected_concepts = concepts or concepts_for_tiers(["focused"])
    selected_phrases = phrases or FIRST_PERSON
    output: dict[str, Query] = {}
    for language in selected_languages:
        language_concepts = selected_concepts.get(language, [])
        language_phrases = selected_phrases.get(language, [])
        for concept, phrase in product(language_concepts, language_phrases):
            query = Query(f'"{phrase}" "{concept}"', language, concept, phrase, concept_tier(language, concept))
            output[query.text] = query
        if include_standalone:
            for concept in language_concepts:
                query = Query(f'"{concept}"', language, concept, None, concept_tier(language, concept))
                output[query.text] = query
    return list(output.values())


def write_queries(path: Path, queries: list[Query]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for query in queries:
            handle.write(json.dumps(asdict(query), ensure_ascii=False) + "\n")
