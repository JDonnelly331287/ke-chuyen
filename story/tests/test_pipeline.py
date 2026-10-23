"""End-to-end check of the pipeline with the listening and writing services stubbed out.

Run: python3 -m unittest discover story/tests
"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kechuyen  # noqa: E402

SAMPLE = json.loads((Path(kechuyen.HERE) / "sample" / "story.json").read_text("utf-8"))

RECS = [
    {"id": "2026-10-20_101500_child-01_ba", "recordedAt": "2026-10-20T10:15:00Z", "durationSec": 184,
     "speakerId": "p1", "speakerName": "Bà", "parentId": None, "topicId": "child",
     "topic_vi": "Tuổi thơ ở Việt Nam", "topic_en": "Childhood in Vietnam", "promptId": "child-01",
     "prompt_vi": "Bà ơi, Bà sinh ra ở đâu?", "prompt_en": "Where exactly were you born?",
     "asksForPlace": True, "addressTerm": "Bà", "filename": "2026-10-20_101500_child-01_ba.webm"},
    {"id": "2026-10-20_103000_boat-01_minh", "recordedAt": "2026-10-20T10:30:00Z", "durationSec": 60,
     "speakerId": "p2", "speakerName": "Minh", "parentId": "2026-10-20_101500_child-01_ba", "topicId": "boat",
     "topic_vi": "Vượt biên bằng thuyền", "topic_en": "Leaving by boat", "promptId": "boat-01",
     "prompt_vi": "Anh ơi, thuyền ra sao?", "prompt_en": "What was the boat like?",
     "asksForPlace": False, "addressTerm": "Anh", "filename": "2026-10-20_103000_boat-01_minh.webm"},
]


def fake_polish_answer(content):
    return {
        "spoken": "Bà sinh ra ở Mỹ Tho, gần sông Tiền. My mother sold nước mắm.",
        "english": "I was born in My Tho, near the Tien River. My mother sold fish sauce.",
        "vietnamese": "Bà sinh ra ở Mỹ Tho, gần sông Tiền. Má bà bán nước mắm.",
        "languages": ["vi", "en"], "summary_en": "Born in My Tho.", "summary_vi": "Sinh ở Mỹ Tho.",
        "places": [{"name": "Mỹ Tho", "name_en": "My Tho", "kind": "city", "admin_area": "Tiền Giang",
                    "country": "Vietnam", "when": "1948", "year": 1948, "context": "Birthplace."}],
        "events": [], "people": [], "quotes": [], "unclear": ["the year, 1948 or 1949?"],
    }


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.zip = self.tmp / "export.zip"
        with zipfile.ZipFile(self.zip, "w") as z:
            for r in RECS:
                z.writestr("audio/" + r["filename"], b"not really audio")
            z.writestr("stories.json", json.dumps({
                "app": "Kể Chuyện Ngày Xưa", "version": 2,
                "storytellers": [{"id": "p1", "name": "Bà", "addr": "Bà"}, {"id": "p2", "name": "Minh", "addr": "Anh"}],
                "recordings": RECS}))
            z.writestr("../escape.txt", b"should be ignored")
        self.heard, self.asked = [], []
        kechuyen.ENGINES["openai"] = lambda audio, meta, gl: (self.heard.append(audio) or "ba sinh ra o my tho")

        def fake_claude(system, content, schema, effort, max_tokens):
            self.asked.append((system, content))
            if schema is kechuyen.POLISH_SCHEMA:
                return fake_polish_answer(content)
            if schema is kechuyen.PHOTO_SCHEMA:
                return {"photos_on_page": 2, "handwriting": "Mỹ Tho 1956", "description": "Two prints on an album page.",
                        "caption_en": "Children by the river", "caption_vi": "Trẻ con bên sông", "date": "1956",
                        "place": "Mỹ Tho", "people": "three children"}
            if schema is kechuyen.STORY_SCHEMA:
                story = json.loads(json.dumps(SAMPLE["en"]))
                pid = json.loads((self.tmp / "out" / "media.json").read_text("utf-8"))["photos"][0]["id"]
                story["chapters"][0]["figures"] = [{"photo": pid, "after_paragraph": 0, "caption": "By the river"},
                                                   {"photo": "made-up", "after_paragraph": 1, "caption": "x"}]
                story["chapters"][0]["paragraphs"][1]["text"] += " See [a page](https://example.invalid/nope)."
                return story
            return SAMPLE["vi"]
        kechuyen.ask_claude = fake_claude

    def run_cli(self, *args):
        kechuyen.main(["--out", str(self.tmp / "out"), *args])

    def add_photo(self):
        from PIL import Image
        folder = self.tmp / "album"
        folder.mkdir()
        Image.new("RGB", (3000, 2000), (180, 160, 120)).save(folder / "IMG_0001.jpg")
        (folder / ".DS_Store").write_bytes(b"junk")
        self.run_cli("photos", str(folder))
        self.run_cli("photos", str(folder))  # the same picture twice is added once

    def test_all(self):
        import os
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        kechuyen.link_works = lambda url, cache: "wikipedia.org" in url
        self.add_photo()
        out = self.tmp / "out"
        media = json.loads((out / "media.json").read_text("utf-8"))
        self.assertEqual(len(media["photos"]), 1)
        ph = media["photos"][0]
        self.assertIn("separate shot", ph["description"])
        from PIL import Image
        self.assertEqual(max(Image.open(out / ph["file"]).size), kechuyen.WEB_SIZE)
        md = (out / "photos.md").read_text("utf-8")
        self.assertIn("Caption: Children by the river", md)
        (out / "photos.md").write_text(md.replace("People: three children", "People: Bà, Dì Tư and Cậu Ba")
                                       .replace("Where in the story:", "Where in the story: childhood by the river"), "utf-8")
        self.run_cli("all", str(self.zip))
        self.assertFalse((self.tmp / "escape.txt").exists())
        self.assertEqual(len(self.heard), 2)
        # The reply was cleaned up knowing what its parent recording said.
        polished = [c for s_, c in self.asked if s_ is kechuyen.POLISH_SYSTEM]
        self.assertIn("adds to an earlier answer", polished[1])
        self.assertIn("Mỹ Tho", polished[1])  # glossary carried across recordings
        md = (out / "transcripts" / f"{RECS[0]['id']}.md").read_text("utf-8")
        self.assertIn("## As told", md)
        self.assertIn("../audio/", md)
        story = json.loads((out / "story.json").read_text("utf-8"))
        self.assertEqual(set(story["recordings"]), {r["id"] for r in RECS})
        html = (out / "life-story.html").read_text("utf-8")
        self.assertIn("<title>The River and the Sea</title>", html)
        self.assertNotIn("/*STORY_DATA*/", html)
        self.assertTrue((out / "audio" / RECS[0]["filename"]).exists())
        # Pictures: the family's edits reach the writer, unknown pictures and dead links are dropped.
        weave_prompt = next(c for s_, c in self.asked if s_ is kechuyen.WEAVE_SYSTEM)
        self.assertIn("Bà, Dì Tư and Cậu Ba", weave_prompt)
        self.assertIn("childhood by the river", weave_prompt)
        figs = story["en"]["chapters"][0]["figures"]
        self.assertEqual([f["photo"] for f in figs], [ph["id"]])
        self.assertNotIn("example.invalid", json.dumps(story))
        self.assertIn("[Tiền River](https://en.wikipedia.org", json.dumps(story, ensure_ascii=False))
        self.assertIn(ph["id"], story["photos"])
        self.assertTrue((out / "pictures-to-find.md").exists())

        # Running again does not listen again, and hand corrections reach the story.
        md_path = out / "transcripts" / f"{RECS[0]['id']}.md"
        md_path.write_text(md.replace("Bà sinh ra ở Mỹ Tho", "Bà sinh ra ở Cái Bè"), "utf-8")
        self.asked.clear()
        self.run_cli("all", str(self.zip))
        self.assertEqual(len(self.heard), 2)
        self.assertIn("Cái Bè", self.asked[0][1])


if __name__ == "__main__":
    unittest.main()
