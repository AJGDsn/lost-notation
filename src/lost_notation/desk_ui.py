"""Pages for the four-stage research desk: Search, Review, Leads and Collection."""
from __future__ import annotations

from html import escape
import json
from typing import Optional
from urllib.parse import quote, quote_plus

from .desk import FILTERS, KIND_NAMES, Item, commons_thumbnail
from .queries import CONCEPT_TIERS, FIRST_PERSON
from .search_plan import LANGUAGE_NAMES, SEARCH_SOURCES, brave_available, languages_covered
from .workbench_ui import REGIONAL_ENGINES

THEME = """
<style id="lnp-theme">
:root{--ink:#1d2730;--muted:#65717b;--nav:#12384d;--blue:#176b87;--teal:#248b85;--gold:#c58b32;--paper:#fff;--wash:#f3f6f5;--line:#dce4e5;--good:#e7f5ec;--warn:#fff3dc}
html{overflow-x:hidden}*{box-sizing:border-box}.lnp-nav{font:600 14px/1.2 system-ui,sans-serif;background:var(--nav);color:white;margin:-2rem calc(50% - 50vw) 2rem;padding:.8rem max(1rem,calc((100vw - 1180px)/2));display:flex;gap:.3rem;align-items:center;flex-wrap:wrap;position:relative;z-index:20}.lnp-nav a{color:white!important;text-decoration:none;padding:.55rem .7rem;border-radius:5px}.lnp-nav a:hover{background:#ffffff19}.lnp-nav .brand{font-family:Georgia,serif;font-size:18px;margin-right:auto}.lnp-nav .badge{background:#d7942c;color:#152b35;border-radius:999px;padding:.08rem .4rem;margin-left:.2rem;font-size:11px}
.lnp-flash{font:500 15px system-ui,sans-serif;background:var(--good);border-left:4px solid #55956b;padding:.75rem 1rem;border-radius:4px;margin:1rem 0}
</style>"""

DESK_CSS = """<style>
html,body{background:var(--wash)}body{margin:0;color:var(--ink);font:16px/1.55 system-ui,sans-serif}
.lnp-nav{margin:0}.lnp-nav a.active{background:#ffffff2b}.lnp-nav .step{display:inline-grid;place-items:center;width:1.25rem;height:1.25rem;border-radius:50%;background:#ffffff26;font-size:11px;margin-right:.35rem}
main{max-width:1100px;margin:auto;padding:1.5rem 1rem 5rem}
.eyebrow{font:750 .74rem system-ui;letter-spacing:.13em;text-transform:uppercase;color:#a96d16}
h1{font:700 clamp(1.7rem,4vw,2.4rem)/1.12 Georgia,serif;color:var(--nav);margin:.25rem 0 .4rem}h2{font:700 1.3rem Georgia,serif;color:var(--nav);margin:.2rem 0 .6rem}h3{margin:.1rem 0 .3rem;color:var(--nav);font-size:1.05rem}
.lede{color:var(--muted);max-width:760px;margin:.2rem 0 1.2rem}.muted{color:var(--muted)}small{color:var(--muted)}
.panel{background:white;border:1px solid var(--line);border-radius:12px;padding:1.2rem 1.3rem;margin:1rem 0;box-shadow:0 2px 8px #17394b08}
.button,button{display:inline-block;border:0;border-radius:7px;background:var(--blue);color:white!important;text-decoration:none;padding:.62rem .95rem;font:650 14px/1.2 system-ui;cursor:pointer}
.button.quiet,button.quiet{background:#e8eef0;color:var(--nav)!important}.button.gold,button.gold{background:#a96d16}.button.bad,button.bad{background:#a5524a}button:disabled{opacity:.45;cursor:not-allowed}
.actions{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-top:.8rem}
a{color:#0d5f86}code{background:#eef2f3;border-radius:4px;padding:.08rem .3rem;font-size:.88em;word-break:break-word}
input[type=text],input[type=url],select,textarea{font:inherit;padding:.55rem .65rem;border:1px solid #b9c6cb;border-radius:6px;background:white;color:var(--ink)}
textarea{width:100%;min-height:4.2rem}label.field{display:block;font-weight:650;margin:.6rem 0}label.field input,label.field select,label.field textarea{display:block;width:100%;margin-top:.25rem;font-weight:400}
.job{border-radius:9px;padding:.85rem 1rem;margin:1rem 0}.job.running{background:#e7f2f8;border:1px solid #9fc6d8}.job.done{background:var(--good);border:1px solid #a8d2b5}.job.error{background:#fdebea;border:1px solid #e3aaa5}.job strong{display:block}
.notice{background:var(--warn);border-left:4px solid var(--gold);padding:.75rem 1rem;border-radius:6px;margin:1rem 0}
.stages{display:grid;grid-template-columns:repeat(4,1fr);gap:.9rem;margin:1.2rem 0}.stage{background:white;border:1px solid var(--line);border-radius:12px;padding:1.1rem;display:flex;flex-direction:column;gap:.35rem;position:relative}
.stage .n{width:2rem;height:2rem;border-radius:50%;display:grid;place-items:center;background:var(--nav);color:white;font-weight:750}.stage b{font:700 2rem/1 Georgia,serif;color:var(--nav)}.stage p{color:var(--muted);margin:0;flex:1}
.stage:not(:last-child)::after{content:"→";position:absolute;right:-.85rem;top:1.2rem;color:#9fb1b8;font-weight:700}
.next{background:linear-gradient(135deg,#12384d,#176b87);color:white;border-radius:12px;padding:1.2rem 1.4rem;display:flex;justify-content:space-between;gap:1rem;align-items:center;flex-wrap:wrap}.next p{margin:.2rem 0 0;color:#dceaf0}.next .eyebrow{color:#ffd38b}
.chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.3rem 0 .6rem}.chip{display:inline-flex;align-items:center;gap:.35rem;background:#edf3f4;border:1px solid #d3e0e2;border-radius:999px;padding:.32rem .65rem;cursor:pointer;font-size:.93rem}
.chip:has(input:checked){background:#d5ede9;border-color:#6fb3ac}.chip.custom{border-style:dashed}.tierlabel{font:700 .7rem system-ui;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-top:.4rem}
.lang{border-top:1px solid #edf0f1;padding:.7rem 0}.lang:first-of-type{border-top:0}.langhead{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}.langhead h3{margin:0;min-width:7rem}.langhead button{padding:.3rem .55rem;font-size:12px}
.sources{display:grid;grid-template-columns:repeat(2,1fr);gap:.8rem}.source{border:1px solid var(--line);border-radius:10px;padding:1rem;background:#fbfcfc}.source h3{display:flex;gap:.5rem;align-items:center}.source p{margin:.3rem 0;color:var(--muted);font-size:.93rem}.source .status{font-size:.85rem;color:var(--teal)}
.bar{position:sticky;bottom:0;background:#ffffffee;border:1px solid var(--line);box-shadow:0 -6px 18px #17394b18;border-radius:10px;padding:.8rem 1rem;margin-top:1rem;display:flex;justify-content:space-between;align-items:center;gap:.8rem;flex-wrap:wrap;z-index:10}
.filters{display:flex;flex-wrap:wrap;gap:.4rem;margin:.6rem 0}.filters a{background:white;border:1px solid var(--line);border-radius:999px;padding:.35rem .75rem;text-decoration:none;color:var(--nav);font-size:.92rem}.filters a.on{background:var(--nav);color:white;border-color:var(--nav)}
.card{background:white;border:1px solid var(--line);border-radius:12px;padding:1.3rem 1.4rem;margin:1rem 0;box-shadow:0 4px 16px #17394b10}
.kind{display:inline-block;font:700 .72rem system-ui;letter-spacing:.08em;text-transform:uppercase;background:#e6f0f3;color:#285b6c;border-radius:5px;padding:.15rem .45rem;margin-right:.4rem}
blockquote{border-left:4px solid #7b8b95;margin:1rem 0;padding:.9rem 1.1rem;background:#f6f8f8;white-space:pre-wrap;font-family:Georgia,serif;line-height:1.55;max-height:28rem;overflow:auto}
.thumb{max-width:100%;max-height:380px;border:1px solid var(--line);border-radius:6px;background:#f6f8f8;display:block;margin:.6rem 0}
.decide{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.8rem}.decide button{font-size:15px;padding:.75rem 1.1rem}.decide kbd{font:600 11px system-ui;background:#ffffff33;border-radius:4px;padding:.05rem .3rem;margin-left:.4rem}
.pagelist{max-height:20rem;overflow:auto;columns:2;font-size:.9rem;padding-left:1.1rem}.pagelist li{break-inside:avoid}
table.list{width:100%;border-collapse:collapse;background:white}table.list th,table.list td{border-bottom:1px solid var(--line);padding:.55rem;text-align:left;vertical-align:top}table.list th{background:#f2f6f7;font-size:.85rem}
.lead h3 a{text-decoration:none}.lead .meta{font-size:.88rem;color:var(--muted)}.excerpt{background:#f6f8f8;border-radius:6px;padding:.6rem .8rem;font-size:.93rem;white-space:pre-wrap;max-height:9rem;overflow:auto}
.status-list{list-style:none;padding:0;margin:.5rem 0}.status-list li{padding:.2rem 0}.ok{color:#2d7a4b;font-weight:650}.todo{color:#a96d16;font-weight:650}
pre.text{white-space:pre-wrap;word-break:break-word;background:#f6f8f8;padding:1rem;border-radius:6px;max-height:36rem;overflow:auto}
details>summary{cursor:pointer;color:#0d5f86;font-weight:600}
@media(max-width:900px){.stages{grid-template-columns:1fr 1fr}.stage::after{display:none}.sources{grid-template-columns:1fr}}@media(max-width:560px){.stages{grid-template-columns:1fr}.pagelist{columns:1}}
</style>"""

STAGES = [("search", "1", "Search", "/search"), ("review", "2", "Review", "/review"), ("leads", "3", "Leads", "/leads"), ("collection", "4", "Collection", "/collection")]


def _badge(value: int) -> str:
    return f"<span class='badge'>{value:,}</span>" if value else ""


def nav(counts: Optional[dict[str, int]] = None, active: str = "") -> str:
    counts = counts or {}
    badges = {"review": counts.get("waiting", 0), "leads": counts.get("holding", 0), "collection": counts.get("collection", 0)}
    stages = "".join(
        f"<a href='{href}'{' class=active' if key == active else ''}><span class='step'>{number}</span>{name}{_badge(badges.get(key, 0))}</a>"
        for key, number, name, href in STAGES
    )
    return (f"<nav class='lnp-nav'><a class='brand' href='/'>Lost Notation</a><a href='/'{' class=active' if active == 'home' else ''}>Home</a>{stages}"
            f"<a href='/tools'{' class=active' if active == 'tools' else ''}>Tools</a><a href='/handbook' target='_blank'>Handbook</a></nav>")


def job_banner(state: Optional[dict[str, object]]) -> str:
    if not state:
        return ""
    if state.get("running"):
        return f"<div class='job running'><strong>Working: {escape(str(state['label']))}</strong>Started at {escape(str(state['started_at']))}. You can keep reviewing meanwhile; this banner updates when you change page.</div>"
    if state.get("error"):
        return f"<div class='job error'><strong>The last job stopped</strong>{escape(str(state['error']))}</div>"
    if state.get("result"):
        return f"<div class='job done'><strong>Last job finished</strong>{escape(str(state['result']))}</div>"
    return ""


def layout(title: str, body: str, counts: Optional[dict[str, int]] = None, active: str = "", message: str = "",
           job: Optional[dict[str, object]] = None, refresh: bool = False, script: str = "") -> str:
    flash = f"<div class='lnp-flash'>{escape(message)}</div>" if message else ""
    meta_refresh = "<meta http-equiv='refresh' content='6'>" if refresh else ""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{meta_refresh}"
            f"<title>{escape(title)} · Lost Notation</title>{THEME}{DESK_CSS}</head><body>{nav(counts, active)}<main>{flash}{job_banner(job)}{body}</main>{script}</body></html>")


def link(url: str, text: str, cls: str = "") -> str:
    cls_attr = f" class='{cls}'" if cls else ""
    return f"<a{cls_attr} href='{escape(url, quote=True)}' target='_blank' rel='noreferrer'>{escape(text)}</a>"


def run_button(running: bool, label: str = "Run a batch now", only: str = "") -> str:
    hidden = f"<input type='hidden' name='only' value='{escape(only)}'>" if only else ""
    return f"<form method='post' action='/search/run'>{hidden}<button class='gold' {'disabled' if running else ''}>{escape(label)}</button></form>"


# Home -----------------------------------------------------------------------

def home_page(counts: dict[str, int], plan: dict[str, object], job: dict[str, object], message: str = "") -> str:
    running = bool(job.get("running"))
    used, missing = plan["languages"], plan["missing"]
    if counts["waiting"]:
        step = ("Review the new finds", f"{counts['waiting']:,} new finds are in the Review inbox.", "/review", "Start reviewing")
    elif counts["holding"]:
        step = ("Look at your leads", f"{counts['holding']:,} leads are waiting for a closer look.", "/leads", "Open leads")
    elif not plan["sources"]:
        step = ("Choose where to search", "No automatic sources are ticked yet.", "/search", "Choose terms and sources")
    else:
        step = ("Find more material", "The inbox is empty. Run another batch from your ticked sources.", "/search", "Go to Search")
    missing_note = f" Not searching: {', '.join(LANGUAGE_NAMES[code] for code in missing)}." if missing else " All nine languages included."
    later_note = f"; {counts.get('later', 0):,} set aside for later" if counts.get("later") else ""
    done_note = f"; {counts.get('done', 0):,} finished" if counts.get("done") else ""
    stages = [
        ("1", "Search", f"{len(used)} of 9", f"languages, {len(plan['sources'])} automatic sources ticked.{missing_note}", "/search", "Choose terms and sources"),
        ("2", "Review", f"{counts['waiting']:,}", f"new finds waiting{later_note}. Keep, discard or postpone each one.", "/review", "Start reviewing"),
        ("3", "Leads", f"{counts['holding']:,}", "promising finds in the holding area, with your notes.", "/leads", "Open leads"),
        ("4", "Collection", f"{counts['collection']:,}", f"being processed (copies and vault notes){done_note}.", "/collection", "Open collection"),
    ]
    cards = "".join(
        f"<article class='stage'><span class='n'>{number}</span><h3>{name}</h3><b>{escape(big)}</b><p>{escape(text)}</p><a class='button' href='{href}'>{escape(button)}</a></article>"
        for number, name, big, text, href, button in stages
    )
    body = f"""<div class='eyebrow'>Digital archaeology field station</div><h1>Lost Notation</h1>
    <p class='lede'>Find, sift and preserve independently invented writing systems. Searches feed one Review inbox; what you keep becomes a lead; your best leads are copied and written up in your Obsidian vault.</p>
    <section class='next'><div><div class='eyebrow'>Suggested next step</div><h2 style='color:white;margin:.2rem 0'>{escape(step[0])}</h2><p>{escape(step[1])}</p></div>
    <div class='actions' style='margin:0'><a class='button gold' href='{step[2]}'>{escape(step[3])}</a>{run_button(running) if step[2] == '/search' else ''}</div></section>
    <section class='stages'>{cards}</section>
    <section class='panel'><h2>Run a batch</h2><p class='muted'>Runs each ticked automatic source once, in the background: {escape(', '.join(plan['source_names']) or 'none ticked')}. New finds appear in Review.</p>{run_button(running)}</section>"""
    return layout("Home", body, counts, "home", message, job, refresh=running)


# 1 · Search -----------------------------------------------------------------

def _chip(name: str, language: str, term: str, checked: bool, cls: str = "", tier: str = "") -> str:
    return (f"<label class='chip {cls}'><input type='checkbox' class='{name}' name='{name}' data-lang='{language}' data-tier='{tier}' "
            f"value='{escape(language + '|' + term, quote=True)}' {'checked' if checked else ''}> {escape(term)}</label>")


def search_page(selection: dict[str, object], custom: dict[str, list[str]], statuses: dict[str, str], checklist: tuple[int, int],
                counts: dict[str, int], job: dict[str, object], message: str = "") -> str:
    running = bool(job.get("running"))
    used, missing = languages_covered(selection)
    chosen = {language: set(selection["concepts"].get(language, [])) for language in FIRST_PERSON}
    phrases = {language: set(selection["phrases"].get(language, [])) for language in FIRST_PERSON}
    if missing:
        coverage = (f"<div class='notice'><strong>Searching in {len(used)} of 9 languages:</strong> {escape(', '.join(LANGUAGE_NAMES[c] for c in used) or 'none')}. "
                    f"<br><strong>Left out:</strong> {escape(', '.join(LANGUAGE_NAMES[c] for c in missing))}. "
                    "<button type='button' class='quiet' data-preset='focused-add'>Add the specialist terms for every language</button></div>")
    else:
        coverage = "<div class='lnp-flash'>Searching in all nine languages.</div>"
    blocks = []
    tier_labels = {"focused": "Specialist", "expanded": "Related ideas", "broad": "Broad (more noise)"}
    for language in FIRST_PERSON:
        rows = []
        for tier, label in tier_labels.items():
            terms = CONCEPT_TIERS[tier].get(language, [])
            if terms:
                rows.append(f"<div class='tierlabel'>{label}</div><div class='chips'>{''.join(_chip('concept', language, term, term in chosen[language], tier=tier) for term in terms)}</div>")
        if custom.get(language):
            rows.append(f"<div class='tierlabel'>Your terms</div><div class='chips'>{''.join(_chip('concept', language, term, term in chosen[language], 'custom', 'custom') for term in custom[language])}</div>")
        blocks.append(f"<div class='lang'><div class='langhead'><h3>{LANGUAGE_NAMES[language]}</h3><small class='count' data-lang='{language}'></small>"
                      f"<button type='button' class='quiet' data-all='{language}'>All</button><button type='button' class='quiet' data-none='{language}'>None</button></div>{''.join(rows)}</div>")
    phrase_blocks = "".join(
        f"<div class='lang'><div class='langhead'><h3>{LANGUAGE_NAMES[language]}</h3></div><div class='chips'>{''.join(_chip('phrase', language, phrase, phrase in phrases[language]) for phrase in FIRST_PERSON[language])}</div></div>"
        for language in FIRST_PERSON
    )
    language_options = "".join(f"<option value='{code}'>{name}</option>" for code, name in LANGUAGE_NAMES.items())
    source_cards = []
    for source in SEARCH_SOURCES:
        key = source["key"]; status = statuses.get(key, "")
        if source["automatic"]:
            unavailable = source.get("needs_key") and not brave_available()
            control = (f"<label><input type='checkbox' name='source' value='{key}' {'checked' if key in selection['sources'] and not unavailable else ''} {'disabled' if unavailable else ''}> Include in batches</label>"
                       f"<button class='quiet' formaction='/search/run' name='only' value='{key}' {'disabled' if running or unavailable else ''}>Run just this now</button>")
        elif key == "web":
            control = "<a class='button quiet' href='/search/web'>Open the search checklist</a>"
        else:
            control = "<a class='button quiet' href='/leads#add'>Record a catalogue find</a>"
        source_cards.append(f"<article class='source'><h3>{escape(source['name'])}</h3><p>{escape(source['how'])}</p>"
                            f"{f'<div class=status>{escape(status)}</div>' if status else ''}<div class='actions'>{control}</div></article>")
    done, total = checklist
    body = f"""<div class='eyebrow'>Stage 1 of 4</div><h1>Search: what to look for, and where</h1>
    <p class='lede'>Tick the terms and the places to search. Automatic sources run in batches and send what they find to Review; the hand-search checklist ({done:,} of {total:,} searches done) lets you search Google, Yandex, Baidu and Yahoo! Japan yourself.</p>
    <form method='post' action='/search/save' id='plan'><input type='hidden' name='plan' value='1'>
    {coverage}
    <section class='panel'><h2>Terms</h2><p class='muted'>Specialist terms find the most relevant material; related and broad terms reach further but bring more noise. Forums and the mailing list are read with these terms in mind, Commons is searched for each one, and the checklist is built from them.</p>
    <div class='actions' style='margin:.2rem 0 .6rem'><button type='button' class='quiet' data-preset='focused'>Specialist terms, every language</button><button type='button' class='quiet' data-preset='expanded'>Specialist and related, every language</button><button type='button' class='quiet' data-preset='all'>Everything</button><button type='button' class='quiet' data-preset='clear'>Clear all</button></div>
    {''.join(blocks)}
    <div class='lang'><div class='langhead'><h3>Add a term</h3></div><div class='actions' style='margin-top:.3rem'><select name='new_language' aria-label='Language of the new term'>{language_options}</select><input type='text' name='new_term' placeholder='e.g. tachygraphy, скоропись, 符号文字' aria-label='New term'><button class='quiet'>Add and save</button></div></div></section>
    <section class='panel'><details {'open' if any(phrases.values()) else ''}><summary>Phrases that suggest a personal invention ({sum(len(v) for v in phrases.values())} ticked)</summary>
    <p class='muted'>Phrases like "I invented" or "selbst entworfen" rank passages higher, and each one is combined with each term (same language) on the hand-search checklist.</p>{phrase_blocks}</details>
    <label style='display:block;margin-top:.8rem'><input type='checkbox' id='standalone' name='include_standalone' {'checked' if selection['include_standalone'] else ''}> Also search each term on its own</label></section>
    <section class='panel'><h2>Where to look</h2><div class='sources'>{''.join(source_cards)}</div></section>
    <div class='bar'><div><strong id='query-count'>0</strong> searches on the checklist · <span id='term-count'>0</span> terms ticked</div>
    <div class='actions' style='margin:0'><button class='quiet'>Save choices</button><button class='gold' formaction='/search/run' {'disabled' if running else ''}>Save and run a batch</button></div></div></form>"""
    script = """<script>
const concepts=[...document.querySelectorAll('input.concept')],phrases=[...document.querySelectorAll('input.phrase')],standalone=document.getElementById('standalone');
function update(){let total=0;for(const c of concepts.filter(x=>x.checked)){total+=phrases.filter(p=>p.checked&&p.dataset.lang===c.dataset.lang).length;if(standalone.checked)total+=1}
document.getElementById('query-count').textContent=total.toLocaleString();document.getElementById('term-count').textContent=concepts.filter(x=>x.checked).length;
for(const el of document.querySelectorAll('small.count')){el.textContent=concepts.filter(x=>x.checked&&x.dataset.lang===el.dataset.lang).length+' ticked'}}
for(const b of document.querySelectorAll('[data-preset]'))b.addEventListener('click',()=>{const p=b.dataset.preset;for(const c of concepts){if(p==='focused-add'){if(c.dataset.tier==='focused')c.checked=true}else if(c.dataset.tier!=='custom'||p==='clear'){c.checked=p==='all'||(p==='expanded'&&c.dataset.tier!=='broad')||c.dataset.tier===p}}update()});
for(const b of document.querySelectorAll('[data-all]'))b.addEventListener('click',()=>{for(const c of concepts)if(c.dataset.lang===b.dataset.all)c.checked=true;update()});
for(const b of document.querySelectorAll('[data-none]'))b.addEventListener('click',()=>{for(const c of concepts)if(c.dataset.lang===b.dataset.none)c.checked=false;update()});
for(const x of [...concepts,...phrases,standalone])x.addEventListener('change',update);update();
</script>"""
    return layout("Search", body, counts, "search", message, job, script=script)


def web_search_page(queries: list[dict[str, str]], done: int, total: int, counts: dict[str, int], message: str = "") -> str:
    rows = []
    for query in queries:
        text, language = query["text"], query.get("language", "")
        regional = REGIONAL_ENGINES.get(language)
        engines = [link(f"https://www.google.com/search?q={quote_plus(text)}", "Google", "button"), link(f"https://search.brave.com/search?q={quote_plus(text)}", "Brave", "button quiet")]
        if regional:
            engines.append(link(regional[1] + quote_plus(text), regional[0].replace("Try ", ""), "button quiet"))
        rows.append(f"""<article class='card'><span class='kind'>{escape(LANGUAGE_NAMES.get(language, language or 'any'))}</span><h3 style='display:inline'>{escape(text)}</h3>
        <div class='actions'>{''.join(engines)}</div>
        <form method='post' action='/search/web/lead' class='actions'><input type='hidden' name='query' value='{escape(text, quote=True)}'>
        <input type='url' name='url' required placeholder='Paste a good find: https://…' style='flex:1;min-width:14rem'><input type='text' name='title' placeholder='Title or note (optional)' style='flex:1;min-width:10rem'><button>Add to Leads</button></form>
        <form method='post' action='/search/web/done' class='actions'><input type='hidden' name='query' value='{escape(text, quote=True)}'><button class='quiet'>Done with this search</button></form></article>""")
    body = f"""<div class='eyebrow'>Stage 1 · by hand</div><h1>Search engines, by hand</h1>
    <p class='lede'>{done:,} of {total:,} searches done. Each opens in your normal browser; nothing is scraped. Paste anything worth keeping straight into Leads; it skips Review because you have already judged it. The searches come from the terms ticked on <a href='/search'>Search</a>.</p>
    {''.join(rows) or "<section class='panel'><h2>Checklist finished</h2><p>Every search has been done. Tick more terms or languages on <a href='/search'>Search</a> to add more.</p></section>"}"""
    return layout("Search by hand", body, counts, "search", message)


# 2 · Review -----------------------------------------------------------------

def _filters(items: list[Item], active: str, base: str) -> str:
    fresh = [item for item in items if not item.later]
    chips = [f"<a href='{base}' class='{'on' if not active else ''}'>All waiting ({len(fresh):,})</a>"]
    for key, label in FILTERS.items():
        amount = sum(item.kind == key for item in fresh)
        if amount:
            chips.append(f"<a href='{base}?filter={key}' class='{'on' if active == key else ''}'>{label} ({amount:,})</a>")
    later = len(items) - len(fresh)
    if later:
        chips.append(f"<a href='{base}?filter=later' class='{'on' if active == 'later' else ''}'>Set aside for later ({later:,})</a>")
    return f"<nav class='filters'>{''.join(chips)}</nav>"


def filtered(items: list[Item], active: str) -> list[Item]:
    if active == "later":
        return [item for item in items if item.later]
    return [item for item in items if not item.later and (not active or item.kind == active)]


def evidence_html(item: Item) -> str:
    details = item.details
    if item.kind == "passage":
        meta = " · ".join(part for part in (item.source, details.get("author"), details.get("published")) if part)
        group = ""
        if details.get("documents", 1) > 1:
            group = f"<p class='muted'>Strongest of {details['matches']} matches across {details['documents']} pages of this thread.</p>"
        signals = "".join(f"<code>{escape(signal)}</code> " for signal in details.get("signals", []))
        source = f" · {link(item.url, 'open the original')}" if item.url else ""
        return f"<p class='muted'>{escape(meta)}{source}</p>{group}<p>{signals}</p><blockquote>{escape(item.evidence)}</blockquote>"
    if item.kind == "site":
        pages = "".join(f"<li>{link(url, name)}</li>" for name, url in details.get("page_list", []))
        return (f"<p><strong>{details.get('pages', 0):,} archived pages</strong> · captured {escape(str(details.get('years', '')))} · found by <code>{escape(str(details.get('found_by', '')))}</code></p>"
                f"<div class='actions'>{link(item.url, 'Open the front page', 'button')}{link(str(details.get('listing', '')), 'See everything archived', 'button quiet')}</div>"
                f"<p class='muted'>Pages include: {escape(item.evidence)}</p><details><summary>All pages</summary><ul class='pagelist'>{pages}</ul></details>")
    if item.kind == "file":
        return (f"<img class='thumb' loading='lazy' src='{escape(commons_thumbnail(item.title), quote=True)}' alt='Preview of {escape(item.title, quote=True)}'>"
                f"<div class='actions'>{link(item.url, 'Open on Wikimedia Commons', 'button quiet')}</div><p class='muted'>Found by searching <code>{escape(str(details.get('found_by', '')))}</code></p>")
    return f"<div class='actions'>{link(item.url, 'Open the page', 'button quiet')}</div><p class='muted'>{escape(item.url)}</p>"


def review_page(items: list[Item], current: Optional[Item], active: str, counts: dict[str, int], settled: dict[str, int], message: str = "") -> str:
    shown = filtered(items, active)
    position = shown.index(current) + 1 if current in shown else 1
    progress = (f"{settled.get('holding', 0) + settled.get('collection', 0) + settled.get('done', 0):,} kept as leads · "
                f"{settled.get('trace', 0):,} too little survives · {settled.get('rejected', 0):,} set aside as not relevant")
    if current is None:
        card = ("<section class='card'><h2>Nothing waiting here</h2><p>Everything in this view has been decided. Run another batch from "
                "<a href='/search'>Search</a>, or look at your <a href='/leads'>Leads</a>.</p></section>")
    else:
        later_note = "<p class='notice'>You set this aside earlier.</p>" if current.later else ""
        card = f"""<article class='card'><div class='muted'>{position:,} of {len(shown):,} in this view</div>
        <span class='kind'>{escape(KIND_NAMES.get(current.kind, current.kind))}</span><small>{escape(current.source)}</small>
        <h2 style='margin-top:.4rem;word-break:break-word'>{escape(current.title)}</h2>{later_note}{evidence_html(current)}
        <form method='post' action='/review/decide'><input type='hidden' name='key' value='{escape(current.key, quote=True)}'><input type='hidden' name='filter' value='{escape(active)}'>
        <label class='field'>Note (optional; kept with the lead)<textarea name='notes' placeholder='Why it matters, what to check next…'></textarea></label>
        <div class='decide'><button name='decision' value='lead'>Keep as a lead<kbd>K</kbd></button><button class='gold' name='decision' value='trace' title='Relevant, but too little is archived to use: kept in its own list with your note'>Too little survives<kbd>T</kbd></button><button class='bad' name='decision' value='rejected'>Not relevant<kbd>X</kbd></button><button class='quiet' name='decision' value='later'>Decide later<kbd>L</kbd></button></div></form></article>"""
    body = f"""<div class='eyebrow'>Stage 2 of 4</div><h1>Review</h1>
    <p class='lede'>Everything the searches find waits here, one at a time: keep it as a lead; file it under <em>Too little survives</em> when it is relevant but too little is archived to use; set it aside as not relevant; or leave it for later. {escape(progress)}. Prefer a list? <a href='/review/list{f'?filter={active}' if active else ''}'>Switch to list view</a>.</p>
    {_filters(items, active, '/review')}{card}"""
    script = """<script>document.addEventListener('keydown',e=>{if(e.target.closest('textarea,input,select')||e.ctrlKey||e.metaKey||e.altKey)return;
const v={k:'lead',t:'trace',x:'rejected',l:'later'}[e.key.toLowerCase()];const b=v&&document.querySelector(`button[name=decision][value=${v}]`);if(b){e.preventDefault();b.click()}});</script>"""
    return layout("Review", body, counts, "review", message, script=script)


def review_list_page(items: list[Item], active: str, counts: dict[str, int], message: str = "", limit: int = 400) -> str:
    shown = filtered(items, active)[:limit]
    rows = []
    for item in shown:
        if item.kind == "site":
            summary = f"{item.details.get('pages', 0):,} pages · {item.evidence}"
            opener = link(item.url, "front page") + " · " + link(str(item.details.get("listing", "")), "everything archived")
        elif item.kind == "passage":
            summary = item.evidence[:280] + ("…" if len(item.evidence) > 280 else "")
            opener = link(item.url, "original") if item.url else ""
        else:
            summary = item.url
            opener = link(item.url, "open")
        thumb = f"<img loading='lazy' src='{escape(commons_thumbnail(item.title, 120), quote=True)}' alt='' style='max-width:90px;max-height:70px;float:right;margin-left:.5rem'>" if item.kind == "file" else ""
        rows.append(f"<tr><td><input type='checkbox' name='key' value='{escape(item.key, quote=True)}' aria-label='Select'></td><td><span class='kind'>{escape(KIND_NAMES.get(item.kind, item.kind))}</span></td>"
                    f"<td>{thumb}<strong style='word-break:break-word'>{escape(item.title)}</strong><br><small style='word-break:break-word'>{escape(summary)}</small><br><small>{opener}</small></td></tr>")
    more = f"<p class='muted'>Showing the first {limit:,}; decide these and the rest will follow.</p>" if len(filtered(items, active)) > limit else ""
    body = f"""<div class='eyebrow'>Stage 2 of 4 · list view</div><h1>Review in bulk</h1>
    <p class='lede'>Tick several and decide them together. <a href='/review{f'?filter={active}' if active else ''}'>Back to one at a time</a>.</p>{_filters(items, active, '/review/list')}
    <form method='post' action='/review/bulk'><input type='hidden' name='filter' value='{escape(active)}'>
    <div class='bar' style='top:0;bottom:auto;position:sticky'><label><input type='checkbox' id='all'> Select all shown</label><div class='actions' style='margin:0'>
    <button name='decision' value='lead'>Keep ticked as leads</button><button class='gold' name='decision' value='trace'>Too little survives</button><button class='bad' name='decision' value='rejected'>Ticked are not relevant</button><button class='quiet' name='decision' value='later'>Decide later</button></div></div>
    <table class='list'><thead><tr><th></th><th>Kind</th><th>Find</th></tr></thead><tbody>{''.join(rows) or "<tr><td colspan=3>Nothing waiting in this view.</td></tr>"}</tbody></table></form>{more}"""
    script = "<script>document.getElementById('all').addEventListener('change',e=>{for(const b of document.querySelectorAll('input[name=key]'))b.checked=e.target.checked});</script>"
    return layout("Review list", body, counts, "review", message, script=script)


# 3 · Leads ------------------------------------------------------------------

def _lead_summary(row, limit: int = 900) -> str:
    details = json.loads(row["details_json"] or "{}")
    if row["kind"] == "passage":
        earlier = details.get("earlier_decision")
        note = f"<small>Marked “{'needs more research' if earlier == 'needs-research' else earlier}” on the old Passages screen.</small>" if earlier and earlier != "irrelevant" else ""
        return f"<div class='excerpt'>{escape(row['evidence'][:limit])}{'…' if len(row['evidence']) > limit else ''}</div>{note}"
    if row["kind"] == "site":
        return (f"<p class='meta'>{details.get('pages', 0):,} archived pages · captured {escape(str(details.get('years', '')))} · "
                f"{link(row['url'], 'front page')} · {link(str(details.get('listing', '')), 'everything archived')}</p><div class='excerpt'>{escape(row['evidence'])}</div>")
    if row["kind"] == "file":
        return f"<img class='thumb' loading='lazy' style='max-height:180px' src='{escape(commons_thumbnail(row['title'], 300), quote=True)}' alt=''>"
    if row["kind"] == "catalogue":
        return "<p class='meta'>Catalogue or library record: needs a visit or request; never downloaded automatically.</p>"
    return ""


LEAD_LISTS = [
    ("holding", "/leads", "Leads"),
    ("trace", "/leads/traces", "Too little survives"),
    ("rejected", "/leads/set-aside", "Set aside as not relevant"),
]


def _lead_tabs(counts: dict[str, int], active: str) -> str:
    return "<nav class='filters'>" + "".join(
        f"<a href='{url}' class='{'on' if key == active else ''}'>{name} ({counts.get(key, 0):,})</a>" for key, url, name in LEAD_LISTS
    ) + "</nav>"


def other_copies(row) -> list[tuple[str, str]]:
    """Places that may hold more of a find than the archive copy reviewed."""
    if row["kind"] != "site":
        return [(f"https://www.google.com/search?q={quote_plus(row['url'])}", "search the web for this address")] if row["url"] else []
    site = row["item_key"].split(":", 1)[1]; host, _, path = site.partition("/")
    places = []
    if host == "geocities.com" and path:
        places += [(f"https://www.oocities.org/{path}/", "OoCities mirror"), (f"https://web.archive.org/web/*/oocities.org/{path}/*", "OoCities mirror in the archive")]
    if host == "oocities.org" and path:
        places.append((f"https://web.archive.org/web/*/geocities.com/{path}/*", "the GeoCities original in the archive"))
    return places + [(f"https://www.google.com/search?q={quote_plus(chr(34) + site + chr(34))}", "search the web for this address")]


def settled_page(rows, decision: str, counts: dict[str, int], message: str = "") -> str:
    """The traces list, or everything set aside as not relevant, each with its notes and a way to move it."""
    url = next(url for key, url, _ in LEAD_LISTS if key == decision)
    title = next(name for key, _, name in LEAD_LISTS if key == decision)
    moves = {
        "trace": "<button name='action' value='lead'>Keep as a lead after all</button><button class='quiet' name='action' value='save'>Save notes</button><button class='bad' name='action' value='drop'>Not relevant</button>",
        "rejected": "<button name='action' value='lead'>Keep as a lead</button><button class='gold' name='action' value='trace'>Too little survives</button><button class='quiet' name='action' value='save'>Save notes</button>",
    }[decision]
    cards = []
    for row in rows:
        places = " · ".join(link(place, name) for place, name in other_copies(row)) if decision == "trace" else ""
        cards.append(f"""<article class='card lead'><span class='kind'>{escape(KIND_NAMES.get(row['kind'], row['kind']))}</span><small>{escape(row['source'])} · decided {escape(row['updated_at'][:10])}</small>
        <h3 style='margin-top:.35rem;word-break:break-word'>{escape(row['title'])}</h3>{f"<p class='meta' style='word-break:break-all'>{link(row['url'], row['url'])}</p>" if row['url'] else ''}{_lead_summary(row, 400)}
        {f"<p class='meta'>Look for more: {places}</p>" if places else ''}
        <form method='post' action='/leads/update'><input type='hidden' name='key' value='{escape(row['item_key'], quote=True)}'><input type='hidden' name='back' value='{url}'>
        <label class='field'>Notes<textarea name='notes' placeholder='What survives, what is missing, where else to look'>{escape(row['notes'])}</textarea></label>
        <div class='actions'>{moves}</div></form></article>""")
    lede = {
        "trace": "Finds that are relevant, but where too little is archived to use. They stay here with your notes rather than being thrown away: "
                 "a mirror or another capture may turn up more, and a trace is still evidence that a notation existed.",
        "rejected": "Everything set aside as not relevant, most recently decided first, with your notes. None of it comes back to Review; "
                    "move anything set aside by mistake, or anything that deserves another look.",
    }[decision]
    empty = {"trace": "Nothing filed here yet. In Review, choose “Too little survives” (or press T) for a find that is relevant but too thin to use.",
             "rejected": "Nothing set aside yet."}[decision]
    body = f"""<div class='eyebrow'>Stage 3 of 4</div><h1>{escape(title)}</h1>{_lead_tabs(counts, decision)}
    <p class='lede'>{lede}</p>{''.join(cards) or f"<section class='card'><p>{empty}</p></section>"}"""
    return layout(title, body, counts, "leads", message)


def leads_page(rows, counts: dict[str, int], message: str = "") -> str:
    cards = []
    for row in rows:
        address = link(row["url"], row["url"]) if row["url"] else ""
        cards.append(f"""<article class='card lead'><span class='kind'>{escape(KIND_NAMES.get(row['kind'], row['kind']))}</span><small>{escape(row['source'])} · kept {escape(row['decided_at'][:10])}</small>
        <h3 style='margin-top:.35rem;word-break:break-word'>{escape(row['title'])}</h3><p class='meta' style='word-break:break-all'>{address}</p>{_lead_summary(row)}
        <form method='post' action='/leads/update'><input type='hidden' name='key' value='{escape(row['item_key'], quote=True)}'>
        <label class='field'>Notes<textarea name='notes' placeholder='What makes it promising, what to check…'>{escape(row['notes'])}</textarea></label>
        <div class='actions'><button class='gold' name='action' value='collect'>Ready for the Collection →</button><button class='quiet' name='action' value='save'>Save notes</button><button class='bad' name='action' value='drop'>Not a lead after all</button></div></form></article>""")
    body = f"""<div class='eyebrow'>Stage 3 of 4</div><h1>Leads</h1>{_lead_tabs(counts, 'holding')}
    <p class='lede'>The holding area for promising finds. Add notes as you investigate; when a lead is worth keeping for good, send it to the Collection, where copies are saved and an Obsidian note is written.</p>
    <details class='panel' id='add' {'open' if not rows else ''}><summary>Add a lead you found yourself</summary>
    <form method='post' action='/leads/add'><label class='field'>Address<input type='url' name='url' required placeholder='https://…'></label>
    <label class='field'>Title<input type='text' name='title' placeholder='What it is'></label>
    <label class='field'>What is it?<select name='where'><option value='web'>A web page (can be saved automatically)</option><option value='catalogue'>A library, archive or catalogue record (needs a visit or request)</option></select></label>
    <label class='field'>Library, archive or collection (for catalogue records)<input type='text' name='source' placeholder='e.g. British Library, call number …'></label>
    <label class='field'>Notes<textarea name='notes'></textarea></label><button>Add to Leads</button></form></details>
    <p class='muted'>{len(rows):,} leads waiting.</p>{''.join(cards) or "<section class='card'><h2>No leads yet</h2><p>Keep promising finds in <a href='/review'>Review</a> and they will appear here.</p></section>"}"""
    return layout("Leads", body, counts, "leads", message)


# 4 · Collection -------------------------------------------------------------

def _copy_line(row, status: dict[str, int]) -> str:
    if row["kind"] == "passage":
        return "<li><span class='ok'>✓ Copy saved</span> when the page was collected</li>"
    if row["kind"] == "catalogue":
        return "<li><span class='todo'>Needs a library visit or request</span>; nothing to download</li>"
    if not status["total"]:
        return "<li><span class='todo'>No copies yet</span></li>"
    waiting = f", {status['pending']:,} waiting" if status["pending"] else ""
    failed = f", <span class='todo'>{status['failed']:,} could not be saved</span>" if status["failed"] else ""
    mark = "<span class='ok'>✓</span>" if not status["pending"] else "<span class='todo'>…</span>"
    return f"<li>{mark} Copies: {status['fetched']:,} of {status['total']:,} saved{waiting}{failed}; {status['texts']:,} with readable text</li>"


def _site_copy_line(copy: dict) -> str:
    progress = copy.get("progress")
    if not progress:
        return ("<li><span class='todo'>No copy yet.</span> “Save a complete copy” saves every page, picture and file the archive holds "
                "for this site into a folder on this computer that you can browse offline.</li>")
    extra = []
    if progress["waiting"]:
        extra.append(f"{progress['waiting']:,} still to save")
    if progress["failed"]:
        extra.append(f"<span class='todo'>{progress['failed']:,} could not be saved</span>")
    if progress.get("found_elsewhere"):
        extra.append(f"{progress['found_elsewhere']:,} found outside the site's own captures")
    if progress.get("missing"):
        extra.append(f"{progress['missing']:,} pictures not found anywhere (listed in the copy)")
    if progress["left_out"]:
        extra.append(f"{progress['left_out']:,} more left out (a very large site)")
    mark = "<span class='ok'>✓</span>" if not progress["waiting"] and not progress["failed"] else "<span class='todo'>…</span>"
    browse = copy["browse"] + ("index.html" if progress["front_page"] else "_contents.html")
    return (f"<li>{mark} Complete copy: {progress['saved']:,} of {progress['total']:,} files ({progress['pages']:,} pages, {progress['pictures']:,} pictures)"
            f"{'; ' + ', '.join(extra) if extra else ''}</li><li><a href='{escape(browse, quote=True)}' target='_blank' rel='noopener'>Browse your copy</a> · "
            f"<a href='{escape(copy['browse'], quote=True)}_contents.html' target='_blank' rel='noopener'>List every file</a> · <code>{escape(copy['folder'])}</code></li>")


def _site_copy_buttons(copy: dict, disabled: str) -> str:
    progress = copy.get("progress")
    folder = "<button class='quiet' name='action' value='open'>Open the folder</button>" if progress else ""
    if progress and not progress["waiting"] and not progress["failed"]:
        return folder
    if not progress:
        label = "Save a complete copy"
    elif progress["waiting"]:
        label = f"Finish the copy ({progress['waiting'] + progress['failed']:,} files left)"
    else:
        label = f"Try the {progress['failed']:,} failed files again"
    return f"<button name='action' value='sitecopy' {disabled}>{label}</button>{folder}"


def collection_page(rows, statuses: dict[str, dict[str, int]], finished, settings: dict[str, str], counts: dict[str, int],
                    job: dict[str, object], message: str = "", site_copies: Optional[dict[str, dict]] = None) -> str:
    running = bool(job.get("running")); disabled = "disabled" if running else ""
    site_copies = site_copies or {}
    cards = []
    for row in rows:
        key = row["item_key"]; status = statuses[key]
        vault = f"<li><span class='ok'>✓ Vault note</span> <code>{escape(row['vault_note'])}</code></li>" if row["vault_note"] else "<li><span class='todo'>No vault note yet</span></li>"
        can_copy = row["kind"] in {"file", "web"}
        copy = site_copies.get(key, {}) if row["kind"] == "site" else None
        copy_line = _site_copy_line(copy) if copy is not None else _copy_line(row, status)
        text_link = f"<a href='/collection/text?key={quote(key, safe='')}'>Read the saved text</a>" if status["texts"] or row["kind"] == "passage" else ""
        cards.append(f"""<article class='card lead'><span class='kind'>{escape(KIND_NAMES.get(row['kind'], row['kind']))}</span><small>{escape(row['source'])}</small>
        <h3 style='margin-top:.35rem;word-break:break-word'>{escape(row['title'])}</h3>{f"<p class='meta' style='word-break:break-all'>{link(row['url'], row['url'])}</p>" if row['url'] else ''}
        <ul class='status-list'>{copy_line}{vault}</ul>{text_link}
        <form method='post' action='/collection/update' class='actions'><input type='hidden' name='key' value='{escape(key, quote=True)}'>
        {f"<button name='action' value='copies' {disabled}>Save copies</button>" if can_copy else ''}{_site_copy_buttons(copy, disabled) if copy is not None else ''}{f"<button class='quiet' name='action' value='retry' {disabled}>Try failed copies again</button>" if status['failed'] else ''}
        <button class='quiet' name='action' value='vault'>{'Refresh' if row['vault_note'] else 'Write'} vault note</button><button class='gold' name='action' value='done'>Mark finished</button><button class='quiet' name='action' value='back'>Back to Leads</button></form></article>""")
    finished_rows = "".join(
        f"<li>{escape(row['title'])} <small>{escape(KIND_NAMES.get(row['kind'], row['kind']))}{' · note: ' + escape(row['vault_note']) if row['vault_note'] else ''}</small>"
        f"<form method='post' action='/collection/update' style='display:inline;margin-left:.5rem'><input type='hidden' name='key' value='{escape(row['item_key'], quote=True)}'><button class='quiet' name='action' value='reopen' style='padding:.2rem .5rem;font-size:12px'>Reopen</button></form></li>"
        for row in finished
    )
    body = f"""<div class='eyebrow'>Stage 4 of 4</div><h1>Collection</h1>
    <p class='lede'>Good leads are processed here. Each website gets a complete copy on this computer: every page, picture and file the archive holds, in a folder you can browse offline. Commons images and web pages are saved too. The text of every saved page is extracted, and each lead gets a note in your Obsidian vault with a folder of its saved pages.</p>
    <section class='panel'><form method='post' action='/collection/settings' class='actions' style='margin:0'><label for='vault'><strong>Obsidian vault folder</strong></label>
    <input id='vault' type='text' name='vault' value='{escape(settings['vault'], quote=True)}' style='flex:1;min-width:16rem'><button class='quiet'>Save folder</button></form>
    <p class='muted' style='margin:.5rem 0 0'>Notes go in a “Lost Notation” folder inside it. Anything you write under “My notes” in Obsidian is kept when a note is refreshed.</p></section>
    <form method='post' action='/collection/all' class='actions'><button name='action' value='copies' {disabled}>Save copies of every lead here</button><button class='quiet' name='action' value='vault'>Write every vault note</button></form>
    {''.join(cards) or "<section class='card'><h2>Nothing being processed</h2><p>Send leads here from <a href='/leads'>Leads</a> with “Ready for the Collection”.</p></section>"}
    {f"<details class='panel'><summary>Finished ({len(finished):,})</summary><ul>{finished_rows}</ul></details>" if finished else ''}"""
    return layout("Collection", body, counts, "collection", message, job, refresh=running)


def texts_page(row, texts: list[dict[str, str]], counts: dict[str, int]) -> str:
    sections = "".join(
        f"<details class='panel' {'open' if index == 0 else ''}><summary>{escape(text['title'])}</summary><p class='muted' style='word-break:break-all'>{link(text['original_url'], text['original_url'])}</p><pre class='text'>{escape(text['text'])}</pre></details>"
        for index, text in enumerate(texts)
    )
    body = f"""<div class='eyebrow'>Collection · saved text</div><h1 style='word-break:break-word'>{escape(row['title'])}</h1>
    <p class='lede'>{len(texts):,} saved page(s) with readable text. <a href='/collection'>Back to the Collection</a>.</p>{sections or "<p>No readable text has been saved yet.</p>"}"""
    return layout("Saved text", body, counts, "collection")


def tools_page(counts: dict[str, int], job: dict[str, object], message: str = "") -> str:
    disabled = "disabled" if job.get("running") else ""
    body = f"""<div class='eyebrow'>Extras</div><h1>Tools</h1>
    <section class='panel'><h2>Import files you already have</h2><p class='muted'>HTML, text, Markdown, JSON or JSONL from a file or folder on this computer. Passages that look like someone describing their own system go to Review.</p>
    <form method='post' action='/tools/import'><label class='field'>File or folder<input type='text' name='path' required placeholder='C:\\Research\\saved-forum'></label><label class='field'>A short name for it<input type='text' name='name' required></label><button {disabled}>Import</button></form></section>
    <section class='panel'><h2>Sites hidden from Review</h2><p class='muted'>Sites whose address only looks like a notation word (Neopets “neographics”, a battery shop called <em>sokki</em>, pinhole photography) are listed in <code>examples/review-hidden-sites.txt</code>. Add a line to hide another site; nothing is deleted.</p></section>
    <section class='panel'><h2>Longer archive runs</h2><p class='muted'>For a one- to two-hour run of the old-websites survey, double-click <code>Run First Archive Survey.bat</code> in the project folder. Its finds appear in Review like any other.</p></section>"""
    return layout("Tools", body, counts, "tools", message, job)
