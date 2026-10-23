#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["anthropic>=2.1", "openai>=3.37"]
# ///
"""Kể Chuyện Ngày Xưa: turn the recorder's exports into a written life story.

Two steps, run in order (or both at once with `all`):

  transcribe  Listen to every recording and write down what was said, word for
              word, in the language it was spoken (Vietnamese, English, or both
              mixed). Claude then cleans up each transcript, translates it, and
              pulls out the places, dates and people mentioned.
  weave       Read every transcript and write one life story from them, in
              English and Vietnamese, with a timeline and a list of places.

Everything lands in one folder (life-story/ by default). Recordings that are
already transcribed are skipped, so new exports can be added at any time.
See story/README.md for the plain-language steps.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"

# Order of the recorder's topics, which runs roughly through a life.
TOPIC_ORDER = ["child", "young", "war", "boat", "camp", "arrive", "settle", "wisdom", "free"]

WRITER_MODEL = os.environ.get("KECHUYEN_WRITER_MODEL", "claude-opus-5-5")
HEAR_MODEL = os.environ.get("KECHUYEN_HEAR_MODEL", "gpt-transcribe")
LOCAL_MODEL = os.environ.get("KECHUYEN_LOCAL_MODEL", "large-v3")
OPENAI_UPLOAD_LIMIT = 24 * 1024 * 1024  # the API takes 25 MB; leave headroom


def say(msg: str) -> None:
    print(msg, flush=True)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- exports

def import_exports(inputs: list[Path], out: Path) -> dict:
    """Unpack each export (zip or folder) into out/ and merge them into one catalog."""
    catalog = read_json(out / "catalog.json", {"storytellers": [], "recordings": {}})
    audio_dir = out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for src in inputs:
        if not src.exists():
            sys.exit(f"Can't find {src}")
        with tempfile.TemporaryDirectory() as tmp:
            root = src
            if src.is_file() and src.suffix.lower() == ".zip":
                root = Path(tmp)
                with zipfile.ZipFile(src) as zf:
                    for name in zf.namelist():
                        # Only take what the recorder writes, and never a path that climbs out.
                        if ".." in Path(name).parts or name.startswith("/"):
                            continue
                        if name == "stories.json" or name.startswith("audio/"):
                            zf.extract(name, root)
            stories = read_json(root / "stories.json")
            if not stories:
                say(f"  {src.name}: no stories.json inside, skipping")
                continue
            known = {p["id"] for p in catalog["storytellers"]}
            for p in stories.get("storytellers", []):
                if p["id"] not in known:
                    catalog["storytellers"].append(p)
            added = 0
            for meta in stories.get("recordings", []):
                audio = root / "audio" / meta["filename"]
                if not audio.exists():
                    say(f"  missing audio for {meta['filename']}, skipping")
                    continue
                if not (audio_dir / meta["filename"]).exists():
                    shutil.copy2(audio, audio_dir / meta["filename"])
                if meta["id"] not in catalog["recordings"]:
                    added += 1
                catalog["recordings"][meta["id"]] = meta
            say(f"  {src.name}: {len(stories.get('recordings', []))} recordings ({added} new)")
    write_json(out / "catalog.json", catalog)
    return catalog


def ordered(recs: list[dict]) -> list[dict]:
    """Life order: by topic, then question, then when it was recorded."""
    def key(m):
        t = m.get("topicId", "free")
        return (TOPIC_ORDER.index(t) if t in TOPIC_ORDER else len(TOPIC_ORDER), m.get("promptId", ""), m.get("recordedAt", ""))
    return sorted(recs, key=key)


# ---------------------------------------------------------------- listening

def hear_openai(audio: Path, meta: dict, glossary: list[str]) -> str:
    from openai import OpenAI
    client = OpenAI()
    parts = [audio] if audio.stat().st_size <= OPENAI_UPLOAD_LIMIT else split_audio(audio)
    prompt = (f"{meta.get('prompt_vi', '')}\n{meta.get('prompt_en', '')}\n"
              "Người kể có thể nói tiếng Việt, tiếng Anh, hoặc xen lẫn cả hai. "
              "Ghi lại đúng từng chữ, đầy đủ dấu.")
    texts = []
    for i, part in enumerate(parts):
        extra: dict = {}
        if HEAR_MODEL == "gpt-transcribe":
            extra["languages"] = ["vi", "en"]
            if glossary:
                extra["keywords"] = glossary[:100]
        with open(part, "rb") as f:
            r = client.audio.transcriptions.create(
                model=HEAR_MODEL, file=f, prompt=prompt if i == 0 else texts[-1][-400:],
                response_format="json", chunking_strategy="auto", **extra)
        texts.append(r.text.strip())
    return "\n\n".join(texts)


def split_audio(audio: Path) -> list[Path]:
    """Cut a long recording into 10-minute pieces small enough to upload."""
    if not shutil.which("ffmpeg"):
        sys.exit(f"{audio.name} is longer than the upload limit. Install ffmpeg (brew install ffmpeg) so it can be cut into pieces.")
    tmp = Path(tempfile.mkdtemp(prefix="kechuyen-"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(audio), "-vn", "-ac", "1", "-c:a", "libopus",
                    "-b:a", "32k", "-f", "segment", "-segment_time", "600", str(tmp / "part-%03d.ogg")], check=True)
    return sorted(tmp.glob("part-*.ogg"))


_local_model = None


def hear_local(audio: Path, meta: dict, glossary: list[str]) -> str:
    """Whisper on this computer: free and private, slower, a little weaker on mixed speech."""
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("The local engine needs faster-whisper. Run with: uv run --with faster-whisper story/kechuyen.py ...")
    if _local_model is None:
        say(f"  loading Whisper {LOCAL_MODEL} (the first run downloads about 3 GB)")
        _local_model = WhisperModel(LOCAL_MODEL, compute_type="int8")
    segments, _info = _local_model.transcribe(
        str(audio), initial_prompt=meta.get("prompt_vi") or None, multilingual=True, vad_filter=True,
        hotwords=", ".join(glossary[:40]) or None)
    return " ".join(s.text.strip() for s in segments).strip()


ENGINES = {"openai": hear_openai, "local": hear_local}


# ---------------------------------------------------------------- Claude

def ask_claude(system: str, content: str, schema: dict, effort: str, max_tokens: int) -> dict:
    """One structured request to Claude, streamed so long answers don't time out."""
    import anthropic
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=WRITER_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        # If a request is declined, the API retries it on its recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Claude ran out of room before finishing; try again with fewer recordings.")
    text = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


def nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


STR = {"type": "string"}

PLACE = obj({
    "name": STR,                     # as it should be written, with diacritics: "Mỹ Tho"
    "name_en": STR,                  # English or common spelling: "My Tho"
    "kind": {"type": "string", "enum": ["village", "town", "city", "district", "province", "country",
                                         "region", "camp", "sea", "street", "building", "landmark", "other"]},
    "admin_area": nullable("string"),  # province or state it sits in, if known
    "country": nullable("string"),
    "when": nullable("string"),      # words as told: "khoảng năm 1975", "after the war"
    "year": nullable("integer"),
    "context": STR,                  # what happened there, one sentence in English
})

EVENT = obj({
    "when": STR, "year": nullable("integer"), "year_end": nullable("integer"),
    "approximate": {"type": "boolean"}, "what": STR, "place": nullable("string"),
})

PERSON = obj({"name": STR, "relation": nullable("string"), "context": STR})

POLISH_SCHEMA = obj({
    "spoken": STR,
    "english": STR,
    "vietnamese": STR,
    "languages": arr({"type": "string", "enum": ["vi", "en", "other"]}),
    "summary_en": STR,
    "summary_vi": STR,
    "places": arr(PLACE),
    "events": arr(EVENT),
    "people": arr(PERSON),
    "quotes": arr(obj({"spoken": STR, "english": STR})),
    "unclear": arr(STR),
})

POLISH_SYSTEM = """You are helping a family preserve the life story of an elder who came to the United States from Vietnam. You receive a machine transcript of one spoken answer to one interview question. The speaker is fluent in Vietnamese and English and may switch between them mid-sentence.

Your job is faithful clean-up, never rewriting:
- "spoken": the transcript in the language(s) actually spoken, word for word. Fix only clear machine errors: restore Vietnamese diacritics, correct misheard words when context makes the intended word certain, fix punctuation, break into paragraphs. Keep English phrases in English and Vietnamese in Vietnamese. Keep the speaker's own phrasing, repetitions that carry feeling, and dialect. Drop filler noises only. Mark anything you cannot make out as [không rõ] or [unclear].
- "english": a faithful, natural English translation of the whole answer.
- "vietnamese": the whole answer in Vietnamese (translate any English parts; if it was all Vietnamese, repeat "spoken").
- Place names: write Vietnamese places with full diacritics in "name" and the common English spelling in "name_en". Use the glossary of names already heard so spellings stay consistent across recordings. Never invent a place, date, or person that was not said; if a year is implied but not stated, give your best estimate only with approximate=true, and leave year null when there is no basis.
- "unclear": short notes on words, names or dates the family should double-check by listening."""


def polish(raw: str, meta: dict, people: dict, parent: dict | None, glossary: list[str]) -> dict:
    who = people.get(meta.get("speakerId"), {})
    lines = [
        f"Storyteller: {meta.get('speakerName') or who.get('name', '')} (addressed as \"{meta.get('addressTerm', '')}\")",
        f"Topic: {meta.get('topic_en', '')} / {meta.get('topic_vi', '')}",
        f"Question (Vietnamese): {meta.get('prompt_vi', '')}",
        f"Question (English): {meta.get('prompt_en', '')}",
    ]
    if parent:
        lines.append(f"This recording adds to an earlier answer by {parent['meta'].get('speakerName')}: {parent['summary_en']}")
    if glossary:
        lines.append("Names already heard in other recordings: " + "; ".join(glossary))
    lines += ["", "<transcript>", raw or "[no speech detected]", "</transcript>"]
    return ask_claude(POLISH_SYSTEM, "\n".join(lines), POLISH_SCHEMA, effort="medium", max_tokens=32000)


# ---------------------------------------------------------------- transcribe

MD_HEADINGS = {"spoken": "As told", "english": "English", "vietnamese": "Tiếng Việt"}


def write_transcript_md(path: Path, t: dict) -> None:
    m = t["meta"]
    p = t["polished"]
    places = ", ".join(pl["name"] for pl in p["places"]) or "none named"
    body = [
        f"# {m.get('prompt_en', '')}",
        f"*{m.get('prompt_vi', '')}*",
        "",
        f"{m.get('speakerName', '')} · {m.get('recordedAt', '')[:10]} · {round(m.get('durationSec', 0) / 60, 1)} min · [listen](../audio/{m['filename']})",
        "",
        "<!-- You can correct the text under the three headings below; the story is written from this file. Keep the headings as they are. -->",
        "",
    ]
    for key, heading in MD_HEADINGS.items():
        body += [f"## {heading}", "", p[key].strip(), ""]
    body += ["---", f"Places: {places}"]
    if p["unclear"]:
        body += ["", "To check by listening:"] + [f"- {u}" for u in p["unclear"]]
    path.write_text("\n".join(body) + "\n", "utf-8")


def read_transcript_md(path: Path) -> dict:
    """The three text sections of a transcript file, including any hand corrections."""
    if not path.exists():
        return {}
    text = path.read_text("utf-8").split("\n---\n")[0]
    found = {}
    keys = {v: k for k, v in MD_HEADINGS.items()}
    for chunk in re.split(r"^## ", text, flags=re.M)[1:]:
        heading, _, rest = chunk.partition("\n")
        if heading.strip() in keys:
            found[keys[heading.strip()]] = rest.strip()
    return found


def build_glossary(out: Path) -> list[str]:
    names: dict[str, None] = {}
    for f in sorted((out / "transcripts").glob("*.json")):
        p = read_json(f).get("polished") or {}
        for pl in p.get("places", []):
            names[pl["name"]] = None
        for pe in p.get("people", []):
            names[pe["name"]] = None
    return list(names)[:150]


def transcribe(out: Path, engine: str, redo: bool) -> None:
    catalog = read_json(out / "catalog.json")
    if not catalog or not catalog["recordings"]:
        sys.exit("No recordings yet. Give the export zip from the recorder's \"Save all\" button.")
    tdir = out / "transcripts"
    tdir.mkdir(exist_ok=True)
    people = {p["id"]: p for p in catalog["storytellers"]}
    # Answers first, then the voices that reply to them, so replies get their parent's context.
    recs = sorted(catalog["recordings"].values(), key=lambda m: (m.get("parentId") is not None, m.get("recordedAt", "")))
    todo = [m for m in recs if redo or not (tdir / f"{m['id']}.json").exists()
            or not read_json(tdir / f"{m['id']}.json").get("polished")]
    say(f"{len(recs)} recordings, {len(todo)} to transcribe")
    for n, meta in enumerate(todo, 1):
        jpath = tdir / f"{meta['id']}.json"
        t = read_json(jpath, {}) if not redo else {}
        say(f"[{n}/{len(todo)}] {meta.get('speakerName', '')}: {meta.get('prompt_en', '')[:70]}")
        glossary = build_glossary(out)
        if not t.get("raw"):
            t = {"meta": meta, "engine": engine, "raw": ENGINES[engine](out / "audio" / meta["filename"], meta, glossary),
                 "heard_at": dt.datetime.now().isoformat(timespec="seconds")}
            write_json(jpath, t)  # keep the raw text even if the clean-up below fails
        parent = None
        if meta.get("parentId"):
            pj = read_json(tdir / f"{meta['parentId']}.json", {})
            if pj.get("polished"):
                parent = {"meta": pj["meta"], "summary_en": pj["polished"]["summary_en"]}
        t["polished"] = polish(t["raw"], meta, people, parent, glossary)
        t["meta"] = meta
        write_json(jpath, t)
        write_transcript_md(tdir / f"{meta['id']}.md", t)
    write_all_transcripts(out, catalog)
    say(f"Transcripts are in {tdir}. Read them in {out / 'transcripts.md'}, fix anything misheard in the .md files, then run weave.")


def write_all_transcripts(out: Path, catalog: dict) -> None:
    """One readable file with every transcript, in life order."""
    parts = ["# Transcripts\n"]
    for m in ordered(list(catalog["recordings"].values())):
        md = out / "transcripts" / f"{m['id']}.md"
        if md.exists():
            parts.append(md.read_text("utf-8").replace("](../audio/", "](audio/"))
    (out / "transcripts.md").write_text("\n\n".join(parts), "utf-8")


# ---------------------------------------------------------------- weave

PARA = obj({"text": STR, "sources": arr(STR)})

STORY_SCHEMA = obj({
    "title": STR, "subtitle": STR, "dedication": STR,
    "prologue": arr(STR),
    "chapters": arr(obj({
        "id": STR, "title": STR, "era": STR,
        "year_start": nullable("integer"), "year_end": nullable("integer"),
        "paragraphs": arr(PARA),
        "quotes": arr(obj({"spoken": STR, "english": STR, "source": STR})),
        "places": arr(STR),
    })),
    "epilogue": arr(STR),
    "timeline": arr(obj({
        "year": nullable("integer"), "year_end": nullable("integer"), "approximate": {"type": "boolean"},
        "when": STR, "label": STR, "chapter": STR, "place": nullable("string"), "sources": arr(STR),
    })),
    "places": arr(obj({
        "id": STR, "name": STR, "name_en": STR, "kind": STR, "admin_area": nullable("string"),
        "country": nullable("string"), "period": STR, "note": STR, "chapters": arr(STR), "sources": arr(STR),
    })),
    "people": arr(obj({"name": STR, "relation": STR, "note": STR})),
    "open_questions": arr(obj({"question_en": STR, "question_vi": STR, "why": STR})),
})

VI_SCHEMA = obj({
    "title": STR, "subtitle": STR, "dedication": STR, "prologue": arr(STR),
    "chapters": arr(obj({"title": STR, "era": STR, "paragraphs": arr(STR)})),
    "epilogue": arr(STR),
    "timeline": arr(STR),
    "places": arr(STR),
    "people": arr(obj({"relation": STR, "note": STR})),
})

WEAVE_SYSTEM = """You are a gifted memoirist writing a family's keepsake: the life story of an elder who was born in Vietnam and made a life in the United States, written from interview recordings the family made with them. You receive every transcript, in life order, with the places, dates and people already pulled out.

Write a story the family will treasure and pass down:
- Arrange it into chapters that follow the life (childhood, youth, the war years, leaving, the camp, arrival, building a life, reflections), merging and reordering material across recordings wherever that tells the story better. Give chapters evocative titles. Skip a chapter rather than pad it.
- Write warm, vivid, literary prose in the third person, anchored in concrete detail from the recordings: names, places, smells, objects, numbers. Weave in the storyteller's own words as short quotations, keeping Vietnamese words where they carry flavour (with a gloss). Put 1 to 3 of the most moving lines in each chapter's "quotes", exactly as spoken plus a translation.
- Other voices (family members adding to an answer) appear as themselves: "as her son remembers it...". Use the address terms and names given; when the storyteller's own name is unknown, use the address term the family uses (for example Bà).
- Truth matters more than polish. Use only what the recordings say. Never invent events, names, dates, feelings or dialogue. Where recordings disagree, say so gently. You may give historical context the family would find helpful (for example what was happening in Saigon in April 1975) only when it is well established, and keep it brief.
- Every paragraph lists the recording ids it draws on in "sources". Every timeline entry and place cites its recordings too.
- "timeline": the dated moments of the life in order, with years as told; mark estimates as approximate. "places": every place named, one entry each, with diacritics, English spelling, kind, province/state and country, the period of life it belongs to, and a one-line note; give each a short stable id (kebab-case, like "my-tho"). Chapter "places" lists those ids.
- "open_questions": 5 to 12 questions to ask in the next recording session, to fill the biggest gaps or resolve contradictions, in English and in Vietnamese addressed with the family's address term.
Write in English. Use **bold** sparingly if at all; plain paragraphs only."""

VI_SYSTEM = """You translate a family memoir into Vietnamese for the family's elders and Vietnamese-speaking relatives. You receive the English edition (JSON) and the original transcripts. Write a natural, literary Vietnamese edition with the same structure, chapter for chapter and paragraph for paragraph, and the same number of items in every list. Where the English quotes the storyteller, use the storyteller's original Vietnamese words from the transcripts instead of translating back. Use full diacritics and the family's address terms (for example Bà, Ông, Má). "timeline" is the Vietnamese label for each timeline entry, "places" is the Vietnamese note for each place, in the same order."""


def gather(out: Path, catalog: dict) -> list[dict]:
    tdir = out / "transcripts"
    items = []
    for m in ordered(list(catalog["recordings"].values())):
        t = read_json(tdir / f"{m['id']}.json")
        if not t or not t.get("polished"):
            continue
        p = t["polished"]
        p.update(read_transcript_md(tdir / f"{m['id']}.md"))  # hand corrections win
        items.append({"meta": m, "polished": p})
    return items


def weave(out: Path, languages: list[str]) -> None:
    catalog = read_json(out / "catalog.json")
    items = gather(out, catalog) if catalog else []
    if not items:
        sys.exit("Nothing transcribed yet. Run transcribe first.")
    blocks = []
    for it in items:
        m, p = it["meta"], it["polished"]
        blocks.append(json.dumps({
            "id": m["id"], "storyteller": m.get("speakerName"), "address_term": m.get("addressTerm"),
            "replies_to": m.get("parentId"), "topic": m.get("topic_en"), "question": m.get("prompt_en"),
            "recorded": m.get("recordedAt", "")[:10], "minutes": round(m.get("durationSec", 0) / 60, 1),
            "spoken": p["spoken"], "english": p["english"],
            "places": p["places"], "events": p["events"], "people": p["people"],
        }, ensure_ascii=False))
    tellers = ", ".join(f"{s['name']} (addressed as {s.get('addr', '')})" for s in catalog["storytellers"])
    content = (f"Storytellers: {tellers}. The first is the elder whose life this is.\n\n<recordings>\n"
               + "\n".join(blocks) + "\n</recordings>")
    say(f"Writing the story from {len(items)} recordings (this can take several minutes)...")
    story = {"en": ask_claude(WEAVE_SYSTEM, content, STORY_SCHEMA, effort="high", max_tokens=128000)}
    if "vi" in languages:
        say("Writing the Vietnamese edition...")
        vi_in = f"<english_edition>\n{json.dumps(story['en'], ensure_ascii=False)}\n</english_edition>\n\n<recordings>\n" + "\n".join(blocks) + "\n</recordings>"
        story["vi"] = ask_claude(VI_SYSTEM, vi_in, VI_SCHEMA, effort="medium", max_tokens=128000)
    story["recordings"] = {it["meta"]["id"]: {
        "file": it["meta"]["filename"], "speaker": it["meta"].get("speakerName"),
        "question_en": it["meta"].get("prompt_en"), "question_vi": it["meta"].get("prompt_vi"),
        "recorded": it["meta"].get("recordedAt", "")[:10],
    } for it in items}
    story["storytellers"] = catalog["storytellers"]
    story["written_at"] = dt.datetime.now().isoformat(timespec="seconds")
    write_json(out / "story.json", story)
    render(out)


# ---------------------------------------------------------------- page

def render(out: Path, story_path: Path | None = None) -> Path:
    story = read_json(story_path or out / "story.json")
    if not story:
        sys.exit("No story.json yet. Run weave first.")
    data = json.dumps(story, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text("utf-8").replace("/*STORY_DATA*/null", data)
    title = story["en"]["title"].replace("&", "&amp;").replace("<", "&lt;")
    html = html.replace("<title>Life story</title>", f"<title>{title}</title>")
    page = out / "life-story.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(html, "utf-8")
    say(f"The story is ready: {page}")
    return page


# ---------------------------------------------------------------- cli

def need_keys(engine: str | None, writer: bool) -> None:
    missing = []
    if engine == "openai" and not os.environ.get("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY (for listening; or use --engine local)")
    if writer and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        missing.append("ANTHROPIC_API_KEY (for Claude)")
    if missing:
        sys.exit("Missing keys: " + "; ".join(missing) + ". See story/README.md, step 2.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="kechuyen", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("life-story"), help="folder for everything this makes (default: life-story)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("transcribe", "all"):
        p = sub.add_parser(name, help="transcribe recordings" if name == "transcribe" else "transcribe, then write the story")
        p.add_argument("exports", nargs="*", type=Path, help="export zips (or unzipped folders) from the recorder's Save all")
        p.add_argument("--engine", choices=list(ENGINES), default="openai", help="openai (best for mixed Vietnamese and English) or local Whisper")
        p.add_argument("--redo", action="store_true", help="transcribe everything again, even ones already done")
        if name == "all":
            p.add_argument("--languages", default="en,vi")
    p = sub.add_parser("weave", help="write the life story from the transcripts")
    p.add_argument("--languages", default="en,vi", help="en, or en,vi (default) for both editions")
    p = sub.add_parser("render", help="rebuild life-story.html from story.json without calling any service")
    p.add_argument("--story", type=Path)
    args = ap.parse_args(argv)
    out: Path = args.out

    if args.cmd in ("transcribe", "all"):
        need_keys(args.engine, True)
        if args.exports:
            say("Reading exports")
            import_exports(args.exports, out)
        transcribe(out, args.engine, args.redo)
    if args.cmd in ("weave", "all"):
        need_keys(None, True)
        weave(out, [l.strip() for l in args.languages.split(",")])
    if args.cmd == "render":
        render(out, args.story)


if __name__ == "__main__":
    main()
