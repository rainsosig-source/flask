# 옛 mp3 파일의 정확한 title 음성 길이를 Edge TTS로 측정하고 JSON 캐시에 저장

import asyncio
import json
import os
import subprocess
import tempfile

import edge_tts

CACHE_PATH = "/opt/flask-app/data/title_durations.json"
VOICE = "ko-KR-HyunsuMultilingualNeural"
RATE = "+15%"


def load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CACHE_PATH)


def _clean(title: str) -> str:
    return title.replace("[", "").replace("]", "").strip()


def _generate(title: str):
    """edge_tts로 title 음성 생성 후 ffprobe로 duration(초) 측정. 실패 시 None."""
    text = f"오늘의 뉴스. {_clean(title)}."
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    try:
        async def _gen():
            await edge_tts.Communicate(text, VOICE, rate=RATE).save(tmp_path)
        asyncio.run(_gen())
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception as e:
        print(f"  [title TTS 실패] {title[:30]}: {e}")
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def get_duration(episode_id, title: str, cache: dict = None, save: bool = True):
    """episode_id별 title 길이 lookup. 없으면 TTS 생성하고 캐시."""
    key = str(episode_id)
    if cache is None:
        cache = load_cache()
    if key in cache:
        return cache[key]
    dur = _generate(title)
    if dur is not None:
        cache[key] = dur
        if save:
            save_cache(cache)
    return dur


if __name__ == "__main__":
    # 단독 테스트: 인자로 받은 텍스트의 title 길이 측정
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else "가스공사, AI 기반 안전·정비 혁신 본격화"
    print(f"title: {text}")
    print(f"duration: {_generate(text)}s")
