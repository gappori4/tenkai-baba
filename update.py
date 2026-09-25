#!/usr/bin/env python3
import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; TenkaiBaba/1.0)"
JST = timezone(timedelta(hours=9))


def get(url, data=None):
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": UA, "Accept-Language": "ja", **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})},
    )
    raw = urllib.request.urlopen(req, timeout=20).read()
    for enc in ("cp932", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def parse_stamp(text, now):
    m = re.search(r"(\d+)月(\d+)日.*?(\d+)時(\d+)分", text)
    if not m:
        return None
    month, day, hour, minute = map(int, m.groups())
    year = now.year
    if month > now.month + 1:
        year -= 1
    return datetime(year, month, day, hour, minute, tzinfo=JST)


def hold(html):
    rows = re.findall(
        r'<td class="place">(\d+)</td>[\s\S]*?<td class="num">(\d+)</td>[\s\S]*?<a [^>]*>([^<]+)</a>[\s\S]*?<li title="4コーナー通過順位">([^<]*)</li>',
        html,
    )
    leaders = [r for r in rows if r[3].strip() == "1"]
    if not leaders:
        return "読めない", "4角の先頭が結果に無い。"
    best = min(int(r[0]) for r in leaders)
    who = "、".join(f"{r[1]}番{re.sub(r'\s+', '', r[2])}が{r[0]}着" for r in leaders)
    if best == 1:
        return "残った", f"4角先頭 {who}"
    if best == 2:
        return "2着まで", f"4角先頭 {who}"
    return "残らず", f"4角先頭 {who}"


def freshness(cushion_at, race_date, now):
    if not cushion_at:
        return True, "測定時刻が読めない。推測しない。"
    at = datetime.fromisoformat(cushion_at)
    race = datetime.fromisoformat(f"{race_date}T00:00:00+09:00")
    friday = race - timedelta(days=2)
    if now >= datetime.fromisoformat(f"{race_date}T09:35:00+09:00"):
        if at.date() < race.date():
            return True, f"当日クッション待ち。最終は{at.strftime('%-m/%-d %-H:%M')}。推測しない。"
        return False, ""
    noon = datetime(friday.year, friday.month, friday.day, 12, 0, tzinfo=JST)
    if now >= noon:
        if at.date() < friday.date():
            return True, f"金曜昼過ぎのクッション待ち。最終は{at.strftime('%-m/%-d %-H:%M')}。推測しない。"
        return False, ""
    if at.date() < friday.date():
        return True, f"今週金曜のJRAクッションは昼過ぎ発表。現在値は{at.strftime('%-m/%-d %-H:%M')}"
    return False, ""


def main():
    cfg = json.loads((ROOT / "config.json").read_text())
    now = datetime.now(JST)
    out = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "label": cfg["label"],
        "date": cfg["date"],
        "venue": cfg["venue"],
        "course": cfg["course"],
        "post": cfg["post"],
        "chief": cfg.get("chief", ""),
        "cushion": None,
        "cushion_at": None,
        "stale": True,
        "reason": "公式を取れなかった。推測しない。",
        "turf": [],
        "error": None,
    }
    try:
        html = get("https://www.jra.go.jp/keiba/baba/_data_cushion.html")
        block = html.split('id="rcA"')[1].split('id="rcB"')[0] if cfg["venue"] == "中山" else html.split('id="rcB"')[1]
        units = re.findall(r'<div class="time">([^<]+)</div>\s*<div class="cushion">([0-9.]+)</div>', block)
        if units:
            at = parse_stamp(units[0][0], now)
            out["cushion"] = float(units[0][1])
            out["cushion_at"] = at.isoformat(timespec="minutes") if at else None
            out["stale"], out["reason"] = freshness(out["cushion_at"], cfg["date"], now)
    except Exception as e:
        out["error"] = f"クッション: {e}"

    day = None
    try:
        index = get("https://www.jra.go.jp/JRADB/accessS.html", b"CNAME=pw01sli00/AF")
        ymd = cfg["date"].replace("-", "")
        pat = rf"pw01srl10{cfg['track']}{cfg['date'][:4]}{cfg['kai']}{cfg['nichi']}{ymd}/[0-9A-Fa-f]{{2}}"
        found = re.search(pat, index)
        day = found.group(0) if found else None
    except Exception as e:
        out["error"] = ((out["error"] + " / ") if out["error"] else "") + f"結果一覧: {e}"

    card = ""
    if day:
        try:
            card = get("https://www.jra.go.jp/JRADB/accessS.html", f"CNAME={day}".encode())
        except Exception as e:
            out["error"] = ((out["error"] + " / ") if out["error"] else "") + f"開催: {e}"

    ymd = cfg["date"].replace("-", "")
    for race in cfg["turf"]:
        row = {
            "n": race["n"],
            "time": race["time"],
            "name": race["name"],
            "note": race.get("note"),
            "check": race.get("check"),
            "status": "未出走",
            "detail": "結果の一覧にこの日がまだ無い。" if not day else "このレースの結果リンクがまだ無い。",
        }
        if card:
            nn = f"{race['n']:02d}"
            cname = re.search(
                rf"pw01sde10{cfg['track']}{cfg['date'][:4]}{cfg['kai']}{cfg['nichi']}{nn}{ymd}/[0-9A-Fa-f]{{2}}",
                card,
            )
            if cname:
                try:
                    html = get(f"https://www.jra.go.jp/JRADB/accessS.html?CNAME={cname.group(0)}")
                    if "4コーナー通過順位" in html:
                        row["status"], row["detail"] = hold(html)
                    else:
                        row["status"] = "結果待ち"
                        row["detail"] = "結果のページがまだ空。"
                except Exception:
                    row["status"] = "結果待ち"
                    row["detail"] = "結果を取れなかった。"
        out["turf"].append(row)

    (ROOT / "data.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "stale": out["stale"], "cushion": out["cushion"], "day": bool(day)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
