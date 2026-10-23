# From recordings to a life story

This folder turns the recordings from **Kể Chuyện Ngày Xưa** into a written life story. It works in two steps:

1. **Transcribe.** Every recording is written down word for word, in whatever language was spoken: Vietnamese, English, or both mixed in one sentence. Claude then tidies each transcript (restoring Vietnamese accents, fixing misheard words), translates it, and notes every place, date and person mentioned.
2. **Weave.** Claude reads all the transcripts together and writes one story in chapters, in English and Vietnamese, with a timeline, a list of places, the people in it, and questions to ask in the next recording session.

The result is a single page, `life-story.html`, that opens in any browser. Next to each paragraph there is a small **♪ listen** button that plays the recording it came from.

To see what the finished page looks like before you have recordings, run `uv run story/kechuyen.py --out sample-preview render --story story/sample/story.json` and open `sample-preview/life-story.html`. Everything in that sample is invented.

## Why two services

Claude can't listen to audio, so the listening is done by OpenAI's `gpt-transcribe` model, which handles Vietnamese well and can be told to expect Vietnamese and English in the same recording. Claude does everything after that: the clean-up, the translation, and the writing.

If you would rather keep the audio on your own computer, there is a local option (`--engine local`) that uses Whisper. It costs nothing but is slower and weaker when the speaker switches languages mid-sentence.

## What it costs

Roughly, for 60 answers of about 10 minutes each: listening is a few dollars at OpenAI, and the clean-up and writing are a few dollars at Anthropic. Recordings that are already transcribed are never sent again.

## How to run it (on a Mac)

### 1. One-time setup

Open **Terminal** and install `uv`, which takes care of Python and everything else this needs:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close Terminal and open it again. Then get this code:

```sh
cd ~/Documents
git clone https://github.com/JDonnelly331287/ke-chuyen.git
cd ke-chuyen
```

(Later, `git pull` inside that folder brings in any updates.)

### 2. Get two keys

- **Anthropic** (for Claude): create a key at https://platform.claude.com/settings/keys
- **OpenAI** (for listening): create a key at https://platform.openai.com/api-keys

Each account needs a little credit added. Then, in Terminal, save the keys so they're there every time you open it (paste your own keys in place of the `...`):

```sh
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
echo 'export OPENAI_API_KEY="sk-..."' >> ~/.zshrc
source ~/.zshrc
```

### 3. Export the recordings

In the recorder, tap **Save all** (Lưu tất cả). You get a zip file such as `ke-chuyen-ngay-xua_2026-10-23_101500.zip`. Move it into the `ke-chuyen` folder (or anywhere; you just give its path below).

### 4. Transcribe

```sh
uv run story/kechuyen.py transcribe ~/Downloads/ke-chuyen-ngay-xua_*.zip
```

This makes a `life-story` folder. Each recording takes about a minute. When it finishes, open `life-story/transcripts.md` to read everything that was said.

**Check the transcripts.** This is the step where the family helps most. Each recording has its own file in `life-story/transcripts/` (open it in TextEdit). Under the headings *As told*, *English* and *Tiếng Việt* you can fix any name, place or word that was misheard; the story is written from your corrected text. At the bottom of each file is a short list of things Claude wasn't sure about.

### 5. Weave the story

```sh
uv run story/kechuyen.py weave
```

This takes a few minutes. Then open `life-story/life-story.html`. Keep the `audio` folder next to it so the ♪ listen buttons work.

### Shortcut

To do steps 4 and 5 at once: `uv run story/kechuyen.py all ~/Downloads/ke-chuyen-ngay-xua_*.zip`

## Adding more recordings later

Record more, tap **Save all** again, and run step 4 with the new zip. Only the new recordings are transcribed. Then run step 5 again to rewrite the story with everything included. The story's last section lists questions worth asking next, to fill gaps the recordings left.

## Options

| Command | What it does |
| --- | --- |
| `transcribe ZIP...` | Add exports and transcribe anything new. Several zips at once is fine. |
| `transcribe --redo` | Transcribe everything again from scratch. |
| `transcribe --engine local` | Listen on this computer with Whisper instead of OpenAI. Run it as `uv run --with faster-whisper story/kechuyen.py transcribe --engine local ZIP`. The first run downloads about 3 GB. |
| `weave --languages en` | Write the English edition only. |
| `render` | Rebuild the page from `story.json` without calling any service (after editing `story.json` by hand, for example). |
| `--out FOLDER` | Put everything somewhere other than `life-story/`. Goes before the command: `kechuyen.py --out ~/Desktop/ba transcribe ...` |

Very long recordings (over about 50 minutes) need `ffmpeg` to be cut into pieces: `brew install ffmpeg`.

## What's in the life-story folder

| File | What it is |
| --- | --- |
| `life-story.html` | The finished story. |
| `audio/` | Copies of every recording, used by the listen buttons. |
| `transcripts.md` | Every transcript in one file, in life order. |
| `transcripts/*.md` | One transcript per recording. Edit these to correct mistakes. |
| `transcripts/*.json` | The raw transcript and the extracted places, dates and people. |
| `story.json` | The story as data: chapters, timeline, and places with diacritics, English spellings, province and country. The later map and timeline phase builds on this. |
| `catalog.json` | Every recording from every export, merged. |

These hold private family recordings, so the `life-story/` folder is kept out of git.

## For developers

`kechuyen.py` is one file with no setup beyond `uv`; its dependencies are declared at the top (PEP 723). The writing model defaults to `claude-opus-5-5` and the listening model to `gpt-transcribe`; override with `KECHUYEN_WRITER_MODEL` and `KECHUYEN_HEAR_MODEL`. `template.html` is the page; `render` pours `story.json` into it. Tests stub out both services: `python3 -m unittest discover story/tests`.
