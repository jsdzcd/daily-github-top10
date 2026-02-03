#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, datetime as dt, urllib.parse, urllib.request
from collections import defaultdict

API = "https://api.github.com/search/repositories"
ROOT = os.path.dirname(os.path.dirname(__file__))
DAILY_DIR = os.path.join(ROOT, "daily")
STATE_DIR = os.path.join(ROOT, "state")
CONFIG_PATH = os.path.join(ROOT, "config.json")
SEEN_PATH = os.path.join(STATE_DIR, "seen.json")

def utc_today():
    return dt.datetime.now(dt.timezone.utc).date()

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def gh_get(url, token):
    req = urllib.request.Request(url)
    # topics 兼容：加 mercy preview 不会有坏处
    req.add_header("Accept", "application/vnd.github+json, application/vnd.github.mercy-preview+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))

def iso_date(d: dt.date) -> str:
    return d.isoformat()

def md_escape(s: str) -> str:
    return (s or "").replace("\n", " ").replace("|", "\\|").strip()

def build_why(it):
    topics = it.get("topics") or []
    desc = (it.get("description") or "").strip()
    t = ", ".join(topics[:3])
    if desc and t:
        return f"{t}｜{desc[:60]}"
    return (desc[:80] or t or "").strip()

def search(q, sort, order, per_page, token):
    params = {
        "q": q,
        "sort": sort,
        "order": order,
        "per_page": str(per_page),
        "page": "1",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    data = gh_get(url, token)
    return data.get("items", [])

def topic_lang_filters(cfg_focus):
    topics_any = cfg_focus.get("topics_any", [])
    languages_any = cfg_focus.get("languages_any", [])
    exclude_topics = set(cfg_focus.get("exclude_topics", []))
    return topics_any, languages_any, exclude_topics

def match_focus(it, topics_any, languages_any, exclude_topics):
    topics = set((it.get("topics") or []))
    lang = it.get("language") or ""
    if topics & exclude_topics:
        return False
    ok_topic = True if not topics_any else any(t in topics for t in topics_any)
    ok_lang = True if not languages_any else (lang in languages_any)
    return ok_topic or ok_lang

def load_seen(dedupe_days: int):
    seen = load_json(SEEN_PATH, {"items": []})
    # items: [{"name":"owner/repo","date":"YYYY-MM-DD"}]
    cutoff = (utc_today() - dt.timedelta(days=dedupe_days)).isoformat()
    kept = [x for x in seen.get("items", []) if x.get("date","") >= cutoff]
    return {"items": kept}

def is_seen(seen, full_name: str):
    return any(x.get("name") == full_name for x in seen.get("items", []))

def add_seen(seen, full_name: str, date_str: str):
    seen["items"].append({"name": full_name, "date": date_str})

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def render_digest(title, date_str, items):
    total_stars = sum(int(it.get("stargazers_count", 0)) for it in items)
    lang_count = defaultdict(int)
    topic_count = defaultdict(int)

    for it in items:
        lang = it.get("language") or "Unknown"
        lang_count[lang] += 1
        for t in (it.get("topics") or [])[:6]:
            topic_count[t] += 1

    top_lang = sorted(lang_count.items(), key=lambda x: (-x[1], x[0]))[:6]
    top_topic = sorted(topic_count.items(), key=lambda x: (-x[1], x[0]))[:8]
    top3 = items[:3]

    lines = []
    lines.append(f"# {title} — {date_str}")
    lines.append("")
    lines.append("## 速览（30 秒）")
    lines.append(f"- Top10 合计 Stars：{total_stars}")
    if top_lang:
        lines.append("- 语言分布：" + " / ".join([f"{k} {v}" for k, v in top_lang]))
    if top_topic:
        lines.append("- 热门主题：" + ", ".join([k for k, _ in top_topic]))
    if top3:
        lines.append("- 最热 3 个：")
        for i, it in enumerate(top3, 1):
            lines.append(f"  {i}) {it.get('full_name')} — ⭐ {it.get('stargazers_count',0)} — {build_why(it)[:50]}")
    lines.append("")
    lines.append("## Top10")
    lines.append("| # | Repo | Stars | Lang | Topics | License | Updated | Why |")
    lines.append("|---:|------|------:|------|--------|---------|---------|-----|")
    for i, it in enumerate(items, 1):
        full_name = it.get("full_name", "")
        url = it.get("html_url", "")
        stars = it.get("stargazers_count", 0)
        lang = it.get("language") or ""
        topics = ", ".join((it.get("topics") or [])[:6])
        lic = (it.get("license") or {}).get("spdx_id") or ""
        pushed = (it.get("pushed_at") or "")[:10]
        why = md_escape(build_why(it))
        desc = f"[{full_name}]({url})"
        lines.append(f"| {i} | {desc} | {stars} | {lang} | {md_escape(topics)} | {md_escape(lic)} | {pushed} | {why} |")
    lines.append("")
    lines.append("## 备忘")
    lines.append("- ✅ 想回看的：把链接丢到 `review/queue.md`（你手动维护，最省心）")
    lines.append("")
    return "\n".join(lines)

def update_readme(index):
    """
    index: { "YYYY-MM-DD": {"dir":"daily/YYYY-MM-DD", "files":[("新建", "01-new.md"), ...]} }
    """
    dates = sorted(index.keys(), reverse=True)
    latest = dates[0] if dates else None

    marker_start = "<!--LATEST_START-->"
    marker_end = "<!--LATEST_END-->"

    parts = []
    parts.append("# Daily GitHub Top10 (3-in-1)\n")
    parts.append(f"{marker_start}\n")
    if latest:
        base = index[latest]["dir"]
        parts.append("## Latest\n\n")
        parts.append(f"- **{latest}**\n")
        for label, fname in index[latest]["files"]:
            parts.append(f"  - {label} → [{fname}]({base}/{fname})\n")
        parts.append("\n")
    else:
        parts.append("## Latest\n\n- (waiting for first run)\n\n")
    parts.append(f"{marker_end}\n\n")

    parts.append("## Archive\n\n")
    by_month = defaultdict(list)
    for d in dates:
        by_month[d[:7]].append(d)

    for ym in sorted(by_month.keys(), reverse=True):
        parts.append(f"### {ym}\n\n")
        for d in by_month[ym]:
            base = index[d]["dir"]
            links = " · ".join([f"[{label}]({base}/{fname})" for label, fname in index[d]["files"]])
            parts.append(f"- **{d}** — {links}\n")
        parts.append("\n")

    with open(os.path.join(ROOT, "README.md"), "w", encoding="utf-8") as f:
        f.write("".join(parts))

def main():
    cfg = load_json(CONFIG_PATH, {})
    top_n = int(cfg.get("top_n", 10))
    days_back = int(cfg.get("days_back", 1))
    dedupe_days = int(cfg.get("dedupe_days", 14))
    focus_cfg = cfg.get("focus", {})
    focus_mode = (focus_cfg.get("mode") or "new").lower()

    token = os.getenv("GITHUB_TOKEN")

    today = utc_today()
    start = today - dt.timedelta(days=days_back)
    # 用“昨天”做文件夹名，代表过去24h窗口的归档日
    day_str = iso_date(start)

    # 去重状态
    seen = load_seen(dedupe_days)

    # 1) 新建 Top10：created:start..today
    q_new = f"created:{iso_date(start)}..{iso_date(today)} fork:false archived:false"
    new_items = search(q_new, sort="stars", order="desc", per_page=50, token=token)

    # 2) 活跃/被关注 Top10：pushed:>=start + stars
    q_active = f"pushed:>={iso_date(start)} fork:false archived:false"
    active_items = search(q_active, sort="stars", order="desc", per_page=50, token=token)

    # 3) 限定领域：从 new 或 active 里筛（更快也更“符合你定义”）
    topics_any, languages_any, exclude_topics = topic_lang_filters(focus_cfg)

    base_for_focus = new_items if focus_mode == "new" else active_items
    focus_candidates = [it for it in base_for_focus if match_focus(it, topics_any, languages_any, exclude_topics)]

    def pick_top10(items):
        picked = []
        for it in items:
            name = it.get("full_name", "")
            if not name:
                continue
            if is_seen(seen, name):
                continue
            picked.append(it)
            if len(picked) >= top_n:
                break
        # 如果去重太严格导致不足，允许补齐（不去重）
        if len(picked) < top_n:
            for it in items:
                if it in picked:
                    continue
                picked.append(it)
                if len(picked) >= top_n:
                    break
        return picked[:top_n]

    top_new = pick_top10(new_items)
    top_active = pick_top10(active_items)
    top_focus = pick_top10(focus_candidates)

    # 写文件
    day_dir = os.path.join(DAILY_DIR, day_str)
    ensure_dir(day_dir)

    files = [
        ("新建 Top10（过去24h）", "01-new.md", top_new),
        ("活跃/被关注 Top10（过去24h）", "02-active.md", top_active),
        ("限定领域 Top10", "03-focus.md", top_focus),
    ]

    for label, fname, items in files:
        path = os.path.join(day_dir, fname)
        md = render_digest(label, day_str, items)
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)

        for it in items:
            if it.get("full_name"):
                add_seen(seen, it["full_name"], day_str)

    save_json(SEEN_PATH, seen)

    # 更新 README 索引
    index = {}
    if os.path.exists(DAILY_DIR):
        for d in sorted(os.listdir(DAILY_DIR)):
            dd = os.path.join(DAILY_DIR, d)
            if not os.path.isdir(dd):
                continue
            index[d] = {"dir": f"daily/{d}", "files": [("新建", "01-new.md"), ("活跃", "02-active.md"), ("限定领域", "03-focus.md")]}

    update_readme(index)

    print(f"OK: wrote daily/{day_str}/ (3 files), updated README, seen saved.")

if __name__ == "__main__":
    main()
