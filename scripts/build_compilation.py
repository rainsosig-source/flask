#!/usr/bin/env python3
# 시간대별/일별 모음 팟캐스트(MP3)를 ffmpeg로 합쳐서 생성

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, date

import pymysql
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from compilation_periods import PERIOD_KEYS, period_range, period_label  # noqa: E402
from title_duration_cache import get_duration as get_title_dur, load_cache as load_title_cache, save_cache as save_title_cache  # noqa: E402

load_dotenv("/opt/flask-app/.env")

STATIC_ROOT = "/root/flask-app/static"
PODCAST_ROOT = os.path.join(STATIC_ROOT, "podcast")
OPENING_PATH = "/opt/flask-app/data/opening.mp3"
OUTPUT_BITRATE = "128k"
# 옛 파일 구조: TITLE + silence + OPENING + BODY
# clean 컬럼이 없는 파일은 trim 없이 그대로 포함 (title 음성을 보존하기 위함).
# 결과적으로 옛 에피소드들은 모음 안에서 오프닝 음악이 트랙 구분처럼 들린다.

DB = dict(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    user=os.getenv("DB_USER", "admin"),
    password=os.getenv("DB_PASS", ""),
    db=os.getenv("DB_NAME", "podcast"),
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)


def fetch_episodes(target_date: date, hour_start: int = None, hour_end: int = None):
    """target_date의 에피소드 조회. hour_start/end 지정 시 해당 시간대만."""
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            sql = (
                "SELECT id, title, mp3_path, clean_mp3_path, created_at "
                "FROM episodes "
                "WHERE DATE(created_at) = %s "
            )
            params = [target_date.isoformat()]
            if hour_start is not None and hour_end is not None:
                sql += "AND HOUR(created_at) BETWEEN %s AND %s "
                params += [hour_start, hour_end]
            sql += "ORDER BY created_at ASC"
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _resolve(rel_path: str) -> str:
    """DB의 상대경로를 절대경로로."""
    if not rel_path:
        return None
    if rel_path.startswith("/"):
        return rel_path
    return os.path.join(STATIC_ROOT, rel_path)


def _normalize(src_path: str, dst_path: str) -> bool:
    """src를 OUTPUT_BITRATE/48kHz로 재인코딩 (concat 통일용)."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", src_path,
                "-c:a", "libmp3lame", "-b:a", OUTPUT_BITRATE, "-ar", "48000",
                dst_path,
            ],
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [normalize 실패] {src_path}: {e}")
        return False


# 옛 파일 구조: TITLE + silence(500ms) + OPENING(7.176s) + BODY
# title 길이는 Edge TTS 재생성으로 정확히 측정 (캐시), OPENING 구간만 잘라냄.
# TTS 실패 시 silence detection 폴백.
OPENING_DURATION = 7.176
PYDUB_SILENCE_BETWEEN = 0.5
TITLE_OPENING_GAP = OPENING_DURATION + PYDUB_SILENCE_BETWEEN  # = 7.676s


def _detect_title_end_fallback(src_path: str):
    """폴백: silence detection으로 TITLE 끝 추정. TTS 실패 시만 사용."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", src_path, "-af", "silencedetect=noise=-30dB:d=0.7",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        for line in r.stderr.split("\n"):
            if "silence_start:" in line:
                ts = float(line.split("silence_start:")[1].strip())
                if 0.5 < ts < 15:
                    return ts
        return None
    except Exception as e:
        print(f"  [silencedetect 실패] {src_path}: {e}")
        return None


def _trim_opening_smart(src_path: str, dst_path: str, title_dur: float) -> bool:
    """[0:title_dur] (= TITLE) + [title_dur+7.676:end] (= BODY) 로 concat."""
    body_start = title_dur + TITLE_OPENING_GAP
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", src_path,
                "-filter_complex",
                f"[0:a]atrim=0:{title_dur},asetpts=PTS-STARTPTS[t];"
                f"[0:a]atrim={body_start},asetpts=PTS-STARTPTS[b];"
                f"[t][b]concat=n=2:v=0:a=1[out]",
                "-map", "[out]",
                "-c:a", "libmp3lame", "-b:a", OUTPUT_BITRATE, "-ar", "48000",
                dst_path,
            ],
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [smart trim 실패] {src_path}: {e}")
        return False


def build(episodes, output_path: str, prepend_opening: bool = True) -> bool:
    """에피소드 목록을 합쳐 output_path에 MP3로 저장. 시작에 opening 1회 prepend."""
    if not episodes:
        print("  [skip] 합칠 에피소드 없음")
        return False

    title_cache = load_title_cache()

    with tempfile.TemporaryDirectory(prefix="podcast_compile_") as tmp:
        files = []
        opening_added = False

        if prepend_opening and os.path.exists(OPENING_PATH):
            opening_norm = os.path.join(tmp, "opening.mp3")
            if _normalize(OPENING_PATH, opening_norm):
                files.append(opening_norm)
                opening_added = True

        # 기사 사이 구분용 무음 1.5초 (concat 파라미터 통일)
        silence_path = os.path.join(tmp, "silence.mp3")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                 "-t", "1.5",
                 "-c:a", "libmp3lame", "-b:a", OUTPUT_BITRATE,
                 silence_path],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"  [silence 생성 실패] {e}")
            silence_path = None

        trim_tts = 0
        trim_silence = 0
        trim_failed = 0
        for i, ep in enumerate(episodes):
            clean_abs = _resolve(ep.get("clean_mp3_path"))
            intro_abs = _resolve(ep.get("mp3_path"))

            tmp_norm = os.path.join(tmp, f"ep_{i:03d}.mp3")
            if clean_abs and os.path.exists(clean_abs):
                if _normalize(clean_abs, tmp_norm):
                    files.append(tmp_norm)
                continue
            if intro_abs and os.path.exists(intro_abs):
                # 옛 파일: TTS로 정확한 title 길이 측정 후 OPENING 구간 제거
                title_dur = get_title_dur(ep["id"], ep["title"], cache=title_cache, save=False)
                method = "tts"
                if title_dur is None:
                    title_dur = _detect_title_end_fallback(intro_abs)
                    method = "silence"
                if title_dur is not None and _trim_opening_smart(intro_abs, tmp_norm, title_dur):
                    files.append(tmp_norm)
                    if method == "tts":
                        trim_tts += 1
                    else:
                        trim_silence += 1
                    continue
                trim_failed += 1
                if _normalize(intro_abs, tmp_norm):
                    files.append(tmp_norm)
                continue
            print(f"  [skip] 파일 없음: id={ep.get('id')} {ep.get('title','')[:30]}")

        # 캐시는 batch 끝에 한 번만 저장 (TTS 호출이 새로 있었을 수 있음)
        save_title_cache(title_cache)

        if trim_tts or trim_silence or trim_failed:
            print(f"  [trim] TTS={trim_tts}, silence_fallback={trim_silence}, fail={trim_failed}")

        if not files:
            print("  [skip] 합칠 가능한 파일이 하나도 없음")
            return False

        # 에피소드 사이에 silence 삽입 (opening은 첫 head로 분리)
        if silence_path:
            head = files[:1] if opening_added else []
            eps_files = files[1:] if opening_added else files
            if len(eps_files) > 1:
                interleaved = [eps_files[0]]
                for fp in eps_files[1:]:
                    interleaved.append(silence_path)
                    interleaved.append(fp)
                files = head + interleaved

        # concat demuxer용 list 파일
        list_path = os.path.join(tmp, "list.txt")
        with open(list_path, "w") as f:
            for fp in files:
                f.write(f"file '{fp}'\n")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", list_path,
                    "-c:a", "libmp3lame", "-b:a", OUTPUT_BITRATE,
                    output_path,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"  [concat 실패] {e}")
            return False

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"  ✅ 생성 완료: {output_path} ({size_mb:.2f}MB, {len(files)}개)")
    return True


def build_period(target_date: date, period_key: str) -> bool:
    start, end = period_range(period_key)
    eps = fetch_episodes(target_date, start, end)
    print(f"[period] {target_date} {period_label(period_key)}({period_key} {start:02d}-{end:02d}) → 에피소드 {len(eps)}개")
    out = os.path.join(
        PODCAST_ROOT, "hourly",
        target_date.strftime("%Y/%m/%d"),
        f"{period_key}.mp3",
    )
    return build(eps, out, prepend_opening=True)


def build_daily(target_date: date) -> bool:
    eps = fetch_episodes(target_date)
    print(f"[daily] {target_date} → 에피소드 {len(eps)}개")
    out = os.path.join(
        PODCAST_ROOT, "daily",
        target_date.strftime("%Y/%m"),
        f"{target_date.strftime('%d')}.mp3",
    )
    return build(eps, out, prepend_opening=True)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="kind", required=True)

    pp = sub.add_parser("period")
    pp.add_argument("--date", required=True, help="YYYY-MM-DD")
    pp.add_argument("--name", required=True, choices=PERIOD_KEYS)

    dp = sub.add_parser("daily")
    dp.add_argument("--date", required=True, help="YYYY-MM-DD")

    args = p.parse_args()
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    if args.kind == "period":
        ok = build_period(target_date, args.name)
    else:
        ok = build_daily(target_date)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
