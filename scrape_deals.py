#!/usr/bin/env python3
import os, re, csv, hashlib, pathlib, datetime, yaml, requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
TODAY = datetime.date.today().isoformat()
SCHEMA = ["date","store","product","price","unit","promo_text","valid_from","valid_to","url","id"]

def sha1(text): return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

def load_config():
    with open("config.yaml","r",encoding="utf-8") as f:
        return yaml.safe_load(f)

def fetch(url, timeout=30):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text

def extract_blocks(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript"]): tag.extract()
    text = soup.get_text("\n", strip=True)
    price_rx = re.compile(r"(\$\s?\d{1,3}(?:\.\d{1,2})?)|(\d+\s?for\s?\$\s?\d+(?:\.\d{1,2})?)|(buy\s?\d+\s?get\s?\d+)", re.I)
    blocks = []
    for line in text.splitlines():
        line = line.strip()
        if not line: continue
        if len(line) < 100 and not price_rx.search(line): continue
        blocks.append(re.sub(r"\s+", " ", line))
    return blocks

def guess_price_unit(s):
    m = re.search(r"\$\s?(\d{1,3}(?:\.\d{1,2})?)", s)
    price = m.group(1) if m else ""
    um = re.search(r"(/(?:lb|kg|ea))|(\d+\s?for\s?\$\s?\d+(?:\.\d{1,2})?)|(each)", s, re.I)
    unit = um.group(0) if um else ""
    return price, unit

def blocks_to_rows(store_name, url, keywords, blocks):
    rows=[]
    for b in blocks:
        hit_kw=None
        for kw in keywords:
            if re.search(kw, b, flags=re.I):
                hit_kw = kw
                break
        if not hit_kw: continue
        price, unit = guess_price_unit(b)
        rid = sha1("|".join([store_name, hit_kw, b, url]))
        rows.append({"date": TODAY,"store": store_name,"product": hit_kw,"price": price,"unit": unit,
                     "promo_text": b,"valid_from": "","valid_to": "","url": url,"id": rid})
    seen=set(); out=[]
    for r in rows:
        if r["id"] in seen: continue
        seen.add(r["id"]); out.append(r)
    return out

def ensure_dirs():
    pathlib.Path("docs").mkdir(parents=True, exist_ok=True)
    pathlib.Path("data").mkdir(parents=True, exist_ok=True)

def write_daily_and_all(rows):
    ensure_dirs()
    daily=f"data/daily-{TODAY}.csv"; allcsv="data/all.csv"
    with open(daily,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=SCHEMA); w.writeheader(); w.writerows(rows)
    newfile=not os.path.exists(allcsv)
    with open(allcsv,"a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=SCHEMA)
        if newfile: w.writeheader()
        w.writerows(rows)
    return daily, allcsv

def render_index_md(cfg, grouped_rows):
    history_files = sorted([p for p in pathlib.Path("data").glob("daily-*.csv")],key=lambda p:p.name,reverse=True)[:10]
    hist_lines=[f"- [{p.name}](../{p.as_posix()})" for p in history_files]
    dt=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    parts=[f"# Grocery Deals @ {cfg.get('location','')}",f"_Last updated: {dt}_","",
           "## Stores Searched",*[f"- {name}" for name in grouped_rows.keys()],"","## Matches"]
    for store, rows in grouped_rows.items():
        parts.append(f"### {store}")
        if not rows: parts.append("- （没有匹配到你的关键词）")
        else:
            for r in rows:
                parts.append(f"- **{r['product']}** → {r['promo_text']}")
        parts.append("")
    parts+=["## History (recent 10 days)",*(hist_lines or ["- (no history yet)"]),"",
            "> 数据来自各超市公开的 weekly ad/sales 页面。仅供个人跟踪使用。",""]
    return "\n".join(parts)

def main():
    cfg=load_config(); keywords=cfg.get("keywords",[]); stores=cfg.get("stores",[])
    all_rows=[]; grouped={}
    for store in stores:
        name=store["name"]; grouped.setdefault(name,[])
        for url in store.get("urls",[]):
            try:
                html=fetch(url)
                blocks=extract_blocks(html)
                rows=blocks_to_rows(name,url,keywords,blocks)
                grouped[name].extend(rows); all_rows.extend(rows)
            except Exception as e:
                grouped[name].append({"date":TODAY,"store":name,"product":"ERROR","price":"","unit":"","promo_text":f"{url} -> {e}",
                                      "valid_from":"","valid_to":"","url":url,"id":sha1(str(e))})
    write_daily_and_all(all_rows)
    out_cfg=cfg.get("output",{}); out_path=out_cfg.get("path","./docs/index.md")
    pathlib.Path(os.path.dirname(out_path)).mkdir(parents=True, exist_ok=True)
    md=render_index_md(cfg, grouped)
    with open(out_path,"w",encoding="utf-8") as f: f.write(md)
    print(f"[ok] wrote {out_path}")

if __name__=="__main__": main()
