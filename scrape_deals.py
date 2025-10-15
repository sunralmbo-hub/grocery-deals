#!/usr/bin/env python3
import os, re, csv, hashlib, pathlib, datetime, yaml, requests, urllib.parse
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
TODAY = datetime.date.today().isoformat()

SCHEMA = [
    "date","store","product","price","unit","promo_text",
    "valid_from","valid_to","url","id",
    "image_url","product_url"  # 新增
]

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def load_config():
    with open("config.yaml","r",encoding="utf-8") as f:
        return yaml.safe_load(f)

def fetch(url: str, timeout=30) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text

# ---------- 工具：清理 & 取相对→绝对URL ----------
def absolutize(base, url):
    if not url: return ""
    return urllib.parse.urljoin(base, url)

def textnorm(s: str) -> str:
    return re.sub(r"\s+"," ", (s or "")).strip()

# ---------- Step 1: 抽取“商品卡片” ----------
def find_product_cards(soup: BeautifulSoup):
    """
    返回一批商品候选：每个是 dict(title, price, img, href, raw_text)
    尽量使用通用结构，避免站点专有选择器。
    """
    cards = []

    # 1) schema.org Product
    for prod in soup.select('[itemscope][itemtype*="Product"], [itemtype*="schema.org/Product"]'):
        title = prod.select_one('[itemprop="name"]')
        price = prod.select_one('[itemprop="price"], [data-test*="price"], .price, .Price, [class*="price"]')
        img   = prod.select_one('img')
        link  = prod.select_one('a[href]')
        cards.append({
            "title": textnorm(title.get_text() if title else ""),
            "price": textnorm(price.get_text() if price else ""),
            "img":   img.get("src") if img else "",
            "href":  link.get("href") if link else "",
            "raw":   textnorm(prod.get_text(" "))
        })

    # 2) 常见卡片模式：有图片、有标题、有价格关键字的容器
    #    例如 div.card / li.product / article 等
    #    用宽松匹配，避免漏掉
    if not cards:
        candidates = soup.select("div, li, article, section")
        price_rx = re.compile(r"\$\s?\d{1,3}(?:\.\d{1,2})?|(\d+\s?for\s?\$\s?\d+(?:\.\d{1,2})?)|buy\s?\d+\s?get\s?\d+", re.I)
        for c in candidates:
            txt = textnorm(c.get_text(" "))
            if not price_rx.search(txt): 
                continue
            img = c.select_one("img")
            title = c.select_one("h1, h2, h3, h4, [class*='title'], [data-test*='title'], [itemprop='name']")
            if not (img or title):
                continue
            link = c.select_one("a[href]")
            # 价格节点再精炼一次
            price = c.select_one("[class*='price'], [data-test*='price'], [itemprop='price']")
            cards.append({
                "title": textnorm(title.get_text() if title else ""),
                "price": textnorm(price.get_text() if price else ""),
                "img":   img.get("src") if img else "",
                "href":  link.get("href") if link else "",
                "raw":   txt
            })

    # 3) Fallback：页面级 OpenGraph 当作“集合海报”，以免整页无图
    if not cards:
        og_title = soup.select_one('meta[property="og:title"]')
        og_img   = soup.select_one('meta[property="og:image"]')
        if og_title or og_img:
            cards.append({
                "title": textnorm(og_title.get("content") if og_title else ""),
                "price": "",
                "img":   og_img.get("content") if og_img else "",
                "href":  "",
                "raw":   textnorm(soup.get_text(" "))
            })

    return cards

# ---------- Step 2: 关键词匹配到“商品卡片” ----------
def match_cards_by_keywords(cards, keywords):
    matched = []
    for card in cards:
        blob = " ".join([card.get("title",""), card.get("price",""), card.get("raw","")])
        for kw in keywords:
            if re.search(kw, blob, flags=re.I):
                matched.append({**card, "hit": kw})
                break
    return matched

# ---------- 旧逻辑：文本块回退 ----------
def extract_blocks_for_fallback(soup: BeautifulSoup):
    for tag in soup(["script","style","noscript"]): tag.extract()
    text = soup.get_text("\n", strip=True)
    # 较长或含价格的行
    price_rx = re.compile(r"(\$\s?\d{1,3}(?:\.\d{1,2})?)|(\d+\s?for\s?\$\s?\d+(?:\.\d{1,2})?)|(buy\s?\d+\s?get\s?\d+)", re.I)
    blocks=[]
    for line in text.splitlines():
        line=line.strip()
        if not line: continue
        if len(line) < 100 and not price_rx.search(line): 
            continue
        blocks.append(textnorm(line))
    return blocks

def guess_price_unit(s: str):
    m = re.search(r"\$\s?(\d{1,3}(?:\.\d{1,2})?)", s)
    price = m.group(1) if m else ""
    um = re.search(r"(/(?:lb|kg|ea))|(\d+\s?for\s?\$\s?\d+(?:\.\d{1,2})?)|(each)", s, re.I)
    unit = um.group(0) if um else ""
    return price, unit

# ---------- 汇总为标准 rows ----------
def rows_from_matches(store_name, base_url, matches):
    rows=[]
    for m in matches:
        title = m.get("title") or m.get("raw","")[:120]
        price = m.get("price","")
        unit  = ""
        product_url = absolutize(base_url, m.get("href",""))
        image_url   = absolutize(base_url, m.get("img",""))
        promo_text  = textnorm(m.get("raw",""))
        rid = sha1("|".join([store_name, title, price, product_url or base_url]))
        rows.append({
            "date": TODAY,
            "store": store_name,
            "product": title if title else m.get("hit",""),
            "price": price,
            "unit": unit,
            "promo_text": promo_text,
            "valid_from": "",
            "valid_to": "",
            "url": product_url or base_url,
            "id": rid,
            "image_url": image_url,
            "product_url": product_url
        })
    # 去重
    seen=set(); out=[]
    for r in rows:
        if r["id"] in seen: continue
        seen.add(r["id"]); out.append(r)
    return out

def rows_from_fallback_blocks(store_name, base_url, keywords, blocks):
    out=[]
    for b in blocks:
        hit=None
        for kw in keywords:
            if re.search(kw, b, flags=re.I):
                hit = kw; break
        if not hit: 
            continue
        price, unit = guess_price_unit(b)
        rid = sha1("|".join([store_name, hit, b, base_url]))
        out.append({
            "date": TODAY, "store": store_name,
            "product": hit, "price": price, "unit": unit,
            "promo_text": b, "valid_from": "", "valid_to": "",
            "url": base_url, "id": rid,
            "image_url": "", "product_url": ""
        })
    return out

def ensure_dirs():
    pathlib.Path("docs").mkdir(parents=True, exist_ok=True)
    pathlib.Path("data").mkdir(parents=True, exist_ok=True)

def write_daily_and_all(rows):
    ensure_dirs()
    daily = f"data/daily-{TODAY}.csv"
    allcsv = "data/all.csv"
    newfile = not os.path.exists(allcsv)

    with open(daily, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SCHEMA)
        w.writeheader(); w.writerows(rows)

    with open(allcsv, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SCHEMA)
        if newfile: w.writeheader()
        w.writerows(rows)

def render_index_md(cfg, grouped_rows):
    # 最近 10 天历史
    hist_files = sorted([p for p in pathlib.Path("data").glob("daily-*.csv")],
                        key=lambda p: p.name, reverse=True)[:10]
    hist_links = [f"- [{p.name}](../{p.as_posix()})" for p in hist_files]

    dt = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"# Grocery Deals @ {cfg.get('location','')}",
        f"_Last updated: {dt}_",
        "",
        "## Matches"
    ]

    for store, rows in grouped_rows.items():
        parts.append(f"### {store}")
        if not rows:
            parts.append("- （没有匹配到你的关键词）")
        else:
            for r in rows:
                title = r["product"] or "(untitled)"
                link  = r["product_url"] or r["url"]
                img   = r["image_url"]
                price = f" — {r['price']}" if r['price'] else ""
                # 带图片的 Markdown 行（有图就展示，没图就只展示文字）
                if img:
                    parts.append(f"- <a href='{link}' target='_blank'><img src='{img}' alt='{title}' width='80' style='vertical-align:middle;margin-right:8px;'/></a> **[{title}]({link})**{price}")
                else:
                    parts.append(f"- **[{title}]({link})**{price}")
        parts.append("")

    parts += [
        "## History (recent 10 days)",
        *(hist_links or ["- (no history yet)"]),
        "",
        "> 仅供个人跟踪使用；商品与价格以各超市官网为准。"
    ]
    return "\n".join(parts)

def main():
    cfg = load_config()
    keywords = cfg.get("keywords", [])
    stores = cfg.get("stores", [])

    all_rows=[]
    grouped = {}

    for store in stores:
        name = store["name"]
        grouped.setdefault(name, [])
        for url in store.get("urls", []):
            try:
                html = fetch(url)
                soup = BeautifulSoup(html, "lxml")

                # 先尝试商品卡片模式
                cards = find_product_cards(soup)
                matches = match_cards_by_keywords(cards, keywords)

                if matches:
                    rows = rows_from_matches(name, url, matches)
                else:
                    # 回退：文本块匹配
                    blocks = extract_blocks_for_fallback(soup)
                    rows = rows_from_fallback_blocks(name, url, keywords, blocks)

                grouped[name].extend(rows)
                all_rows.extend(rows)

            except Exception as e:
                err = {
                    "date": TODAY, "store": name, "product": "ERROR",
                    "price": "", "unit": "",
                    "promo_text": f"{url} -> {e}",
                    "valid_from": "", "valid_to": "",
                    "url": url, "id": sha1(str(e)),
                    "image_url":"", "product_url":""
                }
                grouped[name].append(err)
                all_rows.append(err)

    write_daily_and_all(all_rows)

    out_path = cfg.get("output", {}).get("path", "./docs/index.md")
    pathlib.Path(os.path.dirname(out_path)).mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render_index_md(cfg, grouped))

if __name__ == "__main__":
    main()
