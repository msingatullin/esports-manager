import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import jwt
import requests
from pydantic import BaseModel

OUT = Path(os.environ.get("AGENT_OUT", "output"))
MODEL = "claude-opus-5-5"
DEFAULT_QUERIES = [
    "nursery rhymes for kids",
    "kids cartoon english",
    "bedtime stories for kids animation",
    "learn colors for toddlers",
    "kids songs animation",
]


def load_env(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v:
                os.environ.setdefault(k.strip(), v.strip())


def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def search_api(query, limit, days, key):
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(limit, 50),
        "order": "viewCount",
        "relevanceLanguage": "en",
        "safeSearch": "strict",
        "key": key,
    }
    if days:
        params["publishedAfter"] = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=30)
    r.raise_for_status()
    ids = [i["id"]["videoId"] for i in r.json().get("items", [])]
    if not ids:
        return []
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": key},
        timeout=30,
    )
    r.raise_for_status()
    res = []
    for v in r.json().get("items", []):
        s, st = v["snippet"], v.get("statistics", {})
        res.append({
            "id": v["id"],
            "url": f"https://youtu.be/{v['id']}",
            "title": s["title"],
            "channel": s["channelTitle"],
            "published": s["publishedAt"],
            "description": s.get("description", "")[:500],
            "tags": s.get("tags", [])[:15],
            "duration": v["contentDetails"]["duration"],
            "views": int(st.get("viewCount", 0)),
            "likes": int(st.get("likeCount", 0)),
            "query": query,
        })
    return res


def search_ytdlp(query, limit):
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-j", "--no-warnings", f"ytsearch{limit}:{query}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = proc.stdout
    if proc.returncode:
        print(proc.stderr.strip().splitlines()[-1], file=sys.stderr)
    res = []
    for line in out.splitlines():
        v = json.loads(line)
        res.append({
            "id": v.get("id"),
            "url": f"https://youtu.be/{v.get('id')}",
            "title": v.get("title"),
            "channel": v.get("channel") or v.get("uploader"),
            "description": (v.get("description") or "")[:500],
            "duration": v.get("duration"),
            "views": v.get("view_count") or 0,
            "query": query,
        })
    return res


def cmd_search(args):
    key = os.environ.get("YOUTUBE_API_KEY")
    seen, videos = set(), []
    for q in args.query or DEFAULT_QUERIES:
        found = search_api(q, args.limit, args.days, key) if key else search_ytdlp(q, args.limit)
        for v in found:
            if v["id"] not in seen:
                seen.add(v["id"])
                videos.append(v)
        print(f"[search] {q}: {len(found)}")
    videos.sort(key=lambda v: v["views"], reverse=True)
    path = save("videos.json", videos)
    print(f"[search] {len(videos)} -> {path}")
    return videos


class Scene(BaseModel):
    prompt: str
    negative_prompt: str
    duration: int


class VideoIdea(BaseModel):
    title: str
    hook: str
    target_age: str
    based_on_trend: str
    scenes: list[Scene]
    voiceover_en: str
    youtube_title: str
    youtube_description: str
    tags: list[str]


class Plan(BaseModel):
    trends: list[str]
    ideas: list[VideoIdea]


SYSTEM = """You are a content strategist and AI-video prompt engineer for an English-language YouTube kids channel (ages 2-7).
Analyze the supplied list of top-performing kids cartoon videos and extract the patterns that drive views: formats, themes, characters types, colors, music, length, title structure.
Then produce original video ideas built on those patterns.
Rules:
- Never use copyrighted characters, brand names, channel names or recognizable IP (no Cocomelon, Peppa, Bluey, Paw Patrol, Disney etc.). Invent original characters.
- Content must be safe, gentle, educational or calming, compliant with YouTube Made for Kids policy.
- Each scene prompt is for a text-to-video model: one continuous shot, describe style (3D Pixar-like or 2D flat cartoon), characters with consistent appearance repeated verbatim in every scene, action, camera movement, lighting, colors. English only. Max 600 characters.
- Scene duration is 5 or 10 seconds.
- Keep the character description identical across scenes of one idea for visual consistency."""


def cmd_prompts(args):
    videos = json.loads((OUT / "videos.json").read_text())[: args.top]
    compact = [{k: v.get(k) for k in ("title", "channel", "views", "duration", "tags", "description")} for v in videos]
    client = anthropic.Anthropic()
    user = (
        f"Top kids videos (JSON):\n{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"Create {args.ideas} video ideas, each with {args.scenes} scenes."
    )
    if args.theme:
        user += f"\nRequired theme: {args.theme}"
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_format=Plan,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        sys.exit("[prompts] refusal")
    plan = resp.parsed_output
    path = save("plan.json", plan.model_dump())
    print(f"[prompts] {len(plan.ideas)} ideas -> {path}")
    return plan.model_dump()


class Kling:
    def __init__(self):
        self.ak = os.environ["KLING_ACCESS_KEY"]
        self.sk = os.environ["KLING_SECRET_KEY"]
        self.base = os.environ.get("KLING_BASE_URL", "https://api-singapore.klingai.com")
        self.model = os.environ.get("KLING_MODEL", "kling-v2-1-master")

    def headers(self):
        now = int(time.time())
        token = jwt.encode({"iss": self.ak, "exp": now + 1800, "nbf": now - 5}, self.sk, algorithm="HS256", headers={"alg": "HS256", "typ": "JWT"})
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def generate(self, scene, aspect):
        body = {
            "model_name": self.model,
            "prompt": scene["prompt"],
            "negative_prompt": scene.get("negative_prompt", ""),
            "duration": str(10 if scene.get("duration", 5) >= 10 else 5),
            "aspect_ratio": aspect,
            "mode": "std",
        }
        r = requests.post(f"{self.base}/v1/videos/text2video", headers=self.headers(), json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(data)
        tid = data["data"]["task_id"]
        while True:
            time.sleep(15)
            r = requests.get(f"{self.base}/v1/videos/text2video/{tid}", headers=self.headers(), timeout=60)
            r.raise_for_status()
            d = r.json()["data"]
            status = d["task_status"]
            print(f"  kling {tid}: {status}")
            if status == "succeed":
                return d["task_result"]["videos"][0]["url"]
            if status == "failed":
                raise RuntimeError(d.get("task_status_msg"))


class Fal:
    def __init__(self):
        self.key = os.environ["FAL_KEY"]
        self.model = os.environ.get("FAL_MODEL", "fal-ai/kling-video/v2.1/master/text-to-video")

    def generate(self, scene, aspect):
        h = {"Authorization": f"Key {self.key}", "Content-Type": "application/json"}
        body = {
            "prompt": scene["prompt"],
            "negative_prompt": scene.get("negative_prompt", ""),
            "duration": str(10 if scene.get("duration", 5) >= 10 else 5),
            "aspect_ratio": aspect,
        }
        r = requests.post(f"https://queue.fal.run/{self.model}", headers=h, json=body, timeout=60)
        r.raise_for_status()
        job = r.json()
        while True:
            time.sleep(10)
            s = requests.get(job["status_url"], headers=h, timeout=60).json()
            print(f"  fal {job['request_id']}: {s['status']}")
            if s["status"] == "COMPLETED":
                return requests.get(job["response_url"], headers=h, timeout=60).json()["video"]["url"]
            if s["status"] in ("FAILED", "ERROR"):
                raise RuntimeError(s)


def download(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def concat(clips, path):
    lst = path.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(path)], check=True)
    lst.unlink()


def cmd_generate(args):
    plan = json.loads((OUT / "plan.json").read_text())
    provider = Fal() if os.environ.get("VIDEO_PROVIDER", "kling") == "fal" else Kling()
    ideas = plan["ideas"] if args.idea is None else [plan["ideas"][args.idea]]
    for n, idea in enumerate(ideas):
        slug = "".join(c if c.isalnum() else "_" for c in idea["title"].lower())[:40]
        d = OUT / "videos" / f"{n:02d}_{slug}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(idea, ensure_ascii=False, indent=2))
        clips = []
        for i, scene in enumerate(idea["scenes"]):
            clip = d / f"scene_{i:02d}.mp4"
            if not clip.exists():
                print(f"[generate] {idea['title']} scene {i + 1}/{len(idea['scenes'])}")
                download(provider.generate(scene, args.aspect), clip)
            clips.append(clip)
        final = d / "final.mp4"
        concat(clips, final)
        print(f"[generate] -> {final}")


def cmd_run(args):
    cmd_search(args)
    cmd_prompts(args)
    if not args.no_video:
        cmd_generate(args)


def main():
    load_env(Path(__file__).with_name(".env"))
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_search(sp):
        sp.add_argument("-q", "--query", action="append")
        sp.add_argument("--limit", type=int, default=20)
        sp.add_argument("--days", type=int, default=90)

    def add_prompts(sp):
        sp.add_argument("--top", type=int, default=40)
        sp.add_argument("--ideas", type=int, default=3)
        sp.add_argument("--scenes", type=int, default=6)
        sp.add_argument("--theme")

    def add_generate(sp):
        sp.add_argument("--idea", type=int)
        sp.add_argument("--aspect", default="16:9", choices=["16:9", "9:16", "1:1"])

    s = sub.add_parser("search"); add_search(s); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("prompts"); add_prompts(s); s.set_defaults(fn=cmd_prompts)
    s = sub.add_parser("generate"); add_generate(s); s.set_defaults(fn=cmd_generate)
    s = sub.add_parser("run"); add_search(s); add_prompts(s); add_generate(s)
    s.add_argument("--no-video", action="store_true"); s.set_defaults(fn=cmd_run)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
