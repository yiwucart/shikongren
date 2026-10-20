#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
站点构建脚本 —— 以 content/posts/*.md 为唯一数据源，生成静态站。

部署位置：服务器 /www/wwwroot/shikongren.com/ 下运行，产出直接就是站点根目录。
只依赖 Python 标准库（服务器无 Node）。

用法：
    python3 build.py            # 在站点根目录构建
    python3 build.py --check    # 只做数据自检，不写文件

数据流：
    content/posts/*.md  +  content/settings/*.md
        -> data/articles.json  (全量元数据)
        -> data/index.json     (前端检索索引)
        -> index.html          (首页卡片)
        -> posts/<slug>.html   (详情页)
        -> sitemap.xml / robots.txt
"""
import os
import re
import sys
import json
import html
import datetime
import urllib.parse
import collections

# 站点根目录：
#   SRC  = 源码目录（含 content/ admin/ build/ site/ img/）—— 来自 GitHub 仓库
#   OUT  = 输出目录（网站根目录，nginx 指向这里）—— 生成的 index.html / posts/ / data/
# 用环境变量 SITE_SRC / SITE_OUT 指定；默认：当前目录为 SRC，输出到当前目录。
_env_src = os.environ.get('SITE_SRC', '').strip()
_env_out = os.environ.get('SITE_OUT', '').strip()
if _env_src:
    SRC = os.path.abspath(_env_src)
elif os.path.isdir(os.path.join(os.getcwd(), 'content')):
    SRC = os.getcwd()
else:
    SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.abspath(_env_out) if _env_out else SRC

CONTENT = os.path.join(SRC, 'content')
POSTS_DIR = os.path.join(CONTENT, 'posts')
SETTINGS_DIR = os.path.join(CONTENT, 'settings')
DATA_DIR = os.path.join(OUT, 'data')
POSTS_OUT = os.path.join(OUT, 'posts')
DOMAIN = 'https://shikongren.com'
SITE_NAME = '时空人公益推广'
AUTHOR = '吕文艺'

CATS = collections.OrderedDict([
    ('预测案例', ('预测案例', '现场断验实录')),
    ('预测理论', ('预测理论', '时空人预测术原理与体系')),
    ('风水实务', ('风水实务', '砂水·原点·祖坟·方位')),
    ('八字命理', ('八字命理', '八字与时间系统')),
    ('易学基础', ('易学基础', '八卦·阴阳·五行·奇门')),
    ('化解调理', ('化解调理', '化灾解厄与命运调理')),
    ('命运格局', ('命运格局', '命运层次与人生格局')),
    ('面相手相', ('面相手相', '相法与手相技术')),
    ('择日用事', ('择日用事', '择日选时')),
    ('姓名测字', ('姓名测字', '姓名与测字')),
    ('行业辨伪', ('行业辨伪', '揭穿江湖骗术')),
    ('学员心得', ('学员心得', '师友学习心得')),
    ('培训招生', ('培训招生', '历来的培训招生')),
    ('综合论述', ('综合论述', '其他文章')),
])

# ---------------------------------------------------------------- 工具

def esc(s):
    return html.escape(str(s or ''), quote=True)


def read_text(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def parse_frontmatter(text):
    """解析 YAML frontmatter（支持字符串/数字/行内列表/引号）"""
    if not text.startswith('---'):
        return {}, text
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', text, re.S)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)
    fm = {}
    for line in raw.split('\n'):
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        k = k.strip()
        v = v.strip()
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        elif v.startswith('[') and v.endswith(']'):
            inner = v[1:-1].strip()
            items = []
            if inner:
                for it in inner.split(','):
                    it = it.strip().strip('"').strip("'")
                    if it:
                        items.append(it)
            v = items
        elif re.fullmatch(r'-?\d+', v):
            v = int(v)
        fm[k] = v
    return fm, body


def clean_excerpt(body, n=110):
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', body)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)
    t = re.sub(r'https?://\S+', ' ', t)
    t = re.sub(r'&(?:quot|amp|lt|gt|#39|nbsp);', ' ', t)
    t = re.sub(r'[*#>`~\[\](){}|]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > n:
        t = t[:n].rstrip() + '…'
    return t


IMG_PREFIX = '../'

# 微信公众号排版残留：这些行在原公众号里是分页符/装饰符，
# 抓过来单独成行，在网页上表现为孤零零的圆点或「·2--」。
JUNK_RE = re.compile(
    r'^[·•‧∙]\s*\d*\s*-*-*$'   # ·1--  ·2--  •
    r'|^[-—–_=~·•\s]{3,}$'      # 纯符号行
    r'|^\s*\d+\s*$'                # 单独一行数字（无意义）
)


def plain_text(body, n=0):
    """把 markdown 正文压成纯文本，用于全文检索索引。

    去掉图片、链接地址、强调符号和排版残留，只留可读文字。
    n > 0 时截断到 n 字。
    """
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', body)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)
    t = re.sub(r'https?://\S+', ' ', t)
    t = re.sub(r'&(?:quot|amp|lt|gt|#39|nbsp);', ' ', t)
    t = re.sub(r'^#{1,6}\s+', '', t, flags=re.M)
    t = re.sub(r'[*#>`~\[\](){}|_]', ' ', t)
    # 逐行丢弃排版残留
    keep = []
    for ln in t.split('\n'):
        s = ln.strip()
        if not s or JUNK_RE.match(s):
            continue
        if re.fullmatch(r'[·•‧∙。．、，,：:；;！!？?\-—–~～]{1,2}', s):
            continue
        keep.append(s)
    t = ' '.join(keep)
    t = re.sub(r'\s+', ' ', t).strip()
    if n and len(t) > n:
        t = t[:n]
    return t


def _img_attrs(u, alt):
    """解析图片地址，兼容 Decap 插入的尺寸后缀 =800x / =x450。

    返回 (src, alt, style)。Decap CMS 图片按钮插入的是
    `path =800x` 这种带空格后缀的写法，不做剥离浏览器会把地址
    当成 `xxx.jpg =800x` 去请求，必然 404 裂图。
    """
    width = height = None
    m = re.search(r'\s*=(\d*)x(\d*)\s*$', u)
    if m:
        width, height = m.group(1), m.group(2)
        u = u[:m.start()].strip()
        # =x450 只给高度时，按原比例补宽度上限
        if not width and height:
            width = str(int(int(height) * 4 / 3))
        if not height and width:
            height = str(int(int(width) * 3 / 4))
    if u.startswith('/img/'):
        u = IMG_PREFIX + u[1:]
    style = ''
    if width:
        style = ' style="max-width:%spx;height:auto"' % width
    elif height:
        style = ' style="max-height:%spx;width:auto"' % height
    return u, alt, style


def _inline(text):
    """行内语法：图片 / 链接 / 粗体 / 斜体 / 行内代码。"""
    # 图片（可能带 =800x 尺寸后缀）
    def _img(mm):
        src, alt, style = _img_attrs(mm.group(2), mm.group(1))
        return '<img src="%s" alt="%s"%s loading="lazy">' % (src, alt, style)

    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _img, text)
    # 链接
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
                  r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    # 行内代码（先保护，避免里面内容被当成粗体）
    codes = []

    def _stash(m):
        codes.append('<code>%s</code>' % m.group(1))
        return '\x00%d\x00' % (len(codes) - 1)

    text = re.sub(r'`([^`]+)`', _stash, text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', text)
    for i, c in enumerate(codes):
        text = text.replace('\x00%d\x00' % i, c)
    return text


def md_to_html(body, img_prefix='../'):
    """Markdown -> HTML。

    markdown 里的图片写成 /img/xxx（绝对路径，Decap 预览和线上都通用），
    输出到 posts/ 子目录的详情页时需要转成 ../img/xxx。
    支持 Decap 插入的 `=800x` 尺寸后缀、标题、有序/无序列表、引用、
    分隔线、表格。
    """
    global IMG_PREFIX
    IMG_PREFIX = img_prefix

    out = []
    lines = (body or '').split('\n')
    i = 0
    n = len(lines)

    # 微信公众号排版残留由模块级 JUNK_RE 判定
    def is_junk(line):
        s = line.strip()
        if not s:
            return False
        if JUNK_RE.match(s):
            return True
        # 孤立圆点 / 单个标点
        if re.fullmatch(r'[·•‧∙。．、，,：:；;！!？?\-—–~～]{1,2}', s):
            return True
        return False

    while i < n:
        raw = lines[i]
        b = raw.strip()

        if not b:
            i += 1
            continue

        # --- 微信排版残留，直接吞掉 ---
        if is_junk(b):
            i += 1
            continue

        # --- 分隔线 ---
        if re.fullmatch(r'(-{3,}|\*{3,}|_{3,})', b):
            out.append('<hr>')
            i += 1
            continue

        # --- 标题 ---
        m = re.match(r'^(#{1,6})\s+(.*)$', b)
        if m:
            lvl = len(m.group(1))
            out.append('<h%d>%s</h%d>' % (lvl, _inline(esc(m.group(2))), lvl))
            i += 1
            continue

        # --- 独立成行的图片（含尺寸后缀） ---
        m = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', b)
        if m:
            src, alt, style = _img_attrs(esc(m.group(2)), esc(m.group(1)))
            out.append('<figure><img src="%s" alt="%s"%s loading="lazy"></figure>'
                       % (src, alt, style))
            i += 1
            continue

        # --- 表格 ---
        if ('|' in b) and i + 1 < n and re.fullmatch(
                r'\|?[\s:|-]+\|[\s:|-]*', lines[i + 1].strip()):
            tbl = []
            while i < n and '|' in lines[i] and lines[i].strip():
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if re.fullmatch(r'[\s:|-]+', lines[i].strip()):
                    i += 1
                    continue
                tbl.append(cells)
                i += 1
            if tbl:
                head, rows = tbl[0], tbl[1:]
                h = ['<table><thead><tr>']
                for c in head:
                    h.append('<th>%s</th>' % _inline(esc(c)))
                h.append('</tr></thead><tbody>')
                for r in rows:
                    h.append('<tr>')
                    for c in r:
                        h.append('<td>%s</td>' % _inline(esc(c)))
                    h.append('</tr>')
                h.append('</tbody></table>')
                out.append(''.join(h))
            continue

        # --- 引用块 ---
        if b.startswith('> '):
            buf = []
            while i < n and lines[i].strip().startswith('>'):
                buf.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            inner = _inline(esc('\n\n'.join(x.strip() for x in buf if x.strip())))
            out.append('<blockquote>%s</blockquote>' % inner)
            continue

        # --- 列表（连续行合并） ---
        m = re.match(r'^([-*+]|\d+\.)\s+(.*)$', b)
        if m:
            ordered = bool(re.match(r'^\d+\.$', m.group(1)))
            items = []
            while i < n:
                mm = re.match(r'^\s*([-*+]|\d+\.)\s+(.*)$', lines[i])
                if not mm:
                    if not lines[i].strip():
                        break
                    if items:  # 续行
                        items[-1] += ' ' + lines[i].strip()
                        i += 1
                        continue
                    break
                items.append(mm.group(2))
                i += 1
            tag = 'ol' if ordered else 'ul'
            li = ''.join('<li>%s</li>' % _inline(esc(t)) for t in items)
            out.append('<%s>%s</%s>' % (tag, li, tag))
            continue

        # --- 普通段落（连续非空行合并） ---
        buf = [b]
        i += 1
        while i < n and lines[i].strip():
            nxt = lines[i].strip()
            if is_junk(nxt):
                i += 1
                continue
            if (re.match(r'^(#{1,6})\s+', nxt) or nxt.startswith('> ')
                    or re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', nxt)
                    or re.match(r'^([-*+]|\d+\.)\s+', nxt)
                    or re.fullmatch(r'(-{3,}|\*{3,}|_{3,})', nxt)):
                break
            buf.append(nxt)
            i += 1
        out.append('<p>%s</p>' % _inline(esc(' '.join(buf))))

    # 收敛连续空段落
    final = []
    for h in out:
        if h == '<p></p>' and final and final[-1] == '<p></p>':
            continue
        final.append(h)
    return '\n'.join(final)


def slug_of(text, fallback):
    """从文件名/标题生成 URL 安全 slug"""
    s = re.sub(r'[^\w\-.一-鿿]', '', str(text or ''))
    s = s.strip('-')
    return s or fallback


# ---------------------------------------------------------------- 读取内容

def load_articles():
    arts = []
    if not os.path.isdir(POSTS_DIR):
        return arts
    for fn in sorted(os.listdir(POSTS_DIR)):
        if not fn.endswith('.md'):
            continue
        fm, body = parse_frontmatter(read_text(os.path.join(POSTS_DIR, fn)))
        title = fm.get('title') or os.path.splitext(fn)[0]
        slug = os.path.splitext(fn)[0]
        date = str(fm.get('date') or '')
        words = fm.get('words') or len(re.sub(r'\s+', '', body))
        arts.append({
            'title': title,
            'slug': slug,
            'date': date,
            'year': date[:4] if len(date) >= 4 else '',
            'author': fm.get('author') or AUTHOR,
            'url': fm.get('url') or '',
            'account': '时空坐标',
            'series': fm.get('series') or '',
            'category': fm.get('category') or '综合论述',
            'tags': fm.get('tags') or [],
            'words': int(words),
            'body': body.strip(),
        })
    # 时间靠后靠前；同日按标题排
    arts.sort(key=lambda a: (a['date'] or '', a['title']), reverse=True)
    return arts


def load_settings():
    out = {}
    if not os.path.isdir(SETTINGS_DIR):
        return out
    for fn in sorted(os.listdir(SETTINGS_DIR)):
        if not fn.endswith('.md'):
            continue
        fm, body = parse_frontmatter(read_text(os.path.join(SETTINGS_DIR, fn)))
        out[os.path.splitext(fn)[0]] = {'frontmatter': fm, 'body': body.strip()}
    return out


# ---------------------------------------------------------------- 输出

def build_index_html(arts, cats, years):
    cards = []
    for a in arts:
        ex = clean_excerpt(a['body'])
        cards.append(
            '<a class="card" href="posts/%s.html">'
            '<div class="c">%s</div>'
            '<h3>%s</h3>'
            '<p class="ex">%s</p>'
            '<div class="m"><time>%s</time><span>%d 字</span></div>'
            '</a>' % (esc(a['slug']), esc(a['category']), esc(a['title']),
                      esc(ex), esc(a['date']), a['words']))

    nav = ''.join('<a href="#cat-%s">%s <em>%d</em></a>' % (c, esc(CATS[c][0]), cats.get(c, 0))
                  for c in CATS if cats.get(c, 0))

    total_words = sum(a['words'] for a in arts)
    n_cat = len([c for c in CATS if cats.get(c, 0)])

    return '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>时空人公益推广 · 吕文艺老师作品归档</title>
<meta name="description" content="吕文艺老师「吕氏时空人」理论技术作品归档与检索站，收录原创文章 %d 篇。">
<link rel="stylesheet" href="style.css">
<link rel="canonical" href="%s/">
<meta property="og:url" content="%s/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="%s">
<meta property="og:locale" content="zh_CN">
<meta name="twitter:card" content="summary">
</head>
<body>
<header class="hero">
  <div class="wrap">
    <p class="eyebrow">吕氏时空人 · 公益推广</p>
    <h1>时空人公益推广</h1>
    <p class="lede">吕文艺老师「吕氏时空人」理论技术作品的整理与检索。本站收录微信公众号「时空坐标」原创文章 %d 篇，覆盖 %d 个主题，%d 年内容。</p>
    <div class="stats">
      <span><b>%d</b> 篇原创</span>
      <span><b>%s</b> 最早</span>
      <span><b>%s</b> 最新</span>
      <span><b>%d</b> 万字</span>
    </div>
    <div class="searchbar">
      <input id="q" type="search" placeholder="输入标题或关键词，例如：案例集锦、八字、化解、择日…" autocomplete="off">
      <button id="clear">清除</button>
    </div>
    <div id="count" class="count"></div>
    <div class="herobio">
      <img src="/img/uploads/luwenyi-avatar.jpg" alt="吕文艺老师" width="56" height="56" />
      <p><b>吕文艺</b>　「吕氏时空人」理论技术体系创立者，本站收录其公众号「时空坐标」原创文章。
        <a href="about.html">了解本站 →</a></p>
    </div>
  </div>
</header>

<nav class="cats wrap">
  <a href="#" class="all">全部</a>%s
</nav>

<main class="wrap">
  <div id="grid" class="grid">%s</div>
  <p id="empty" class="empty" hidden>没有匹配的文章，换个关键词试试。</p>
</main>

<footer class="wrap foot">
  <div class="disclaimer">
    <h4>免责声明</h4>
    <p>本站为传统文化交流性质的内容归档，所载文章观点与内容仅供学习参考，<strong>不构成任何建议</strong>，亦不作为任何决策依据。读者应独立判断、自负其责。</p>
  </div>
  <div class="src">
    <p>本站全部内容来源于微信公众号「<b>时空坐标</b>」，作者署名<b>吕文艺</b>，版权归原作者及原作者所在平台所有。</p>
    <p>本站与原作者不存在商业关系，内容经原作者授权整理归档，<strong>仅供学习参考</strong>。如需引用，请注明出处并以原文为准。</p>
    <p>如原作者或权利人认为收录内容需要处理，请通过下方方式联系，将立即处理。</p>
  </div>
  <div class="contact">
    <p>关注原作者公众号「时空坐标」，获取最新文章</p>
    <p>微信公众号：<b>时空坐标</b></p>
  </div>
  <p class="footlinks">
    <a href="about.html">关于本站</a> ·
    <a href="sitemap.xml">网站地图</a> ·
    <a href="/admin/">内容管理</a>
  </p>
  <p class="copy">© 时空人公益推广 · shikongren.com · 内容仅供学习参考</p>
</footer>

<script src="app.js"></script>
</body>
</html>''' % (len(arts), DOMAIN, DOMAIN, SITE_NAME,
              len(arts), n_cat, len(years),
              len(arts),
              min(years) if years else '', max(years) if years else '',
              total_words // 10000,
              nav, ''.join(cards))


POST_TPL = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · 时空人公益推广</title>
<meta name="description" content="{desc}">
<meta name="author" content="吕文艺">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="zh_CN">
<meta property="article:author" content="吕文艺">
<meta name="twitter:card" content="summary">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="../style.css">
</head>
<body>
<header class="top"><div class="wrap">
  <a href="../index.html" class="home">← 返回时空人公益推广</a>
</div></header>

<article class="wrap post">
  <nav class="crumb"><a href="../index.html">首页</a> / <span>{cat}</span></nav>
  <h1>{title}</h1>
  <div class="meta">
    <span class="badge">{cat}</span>
    <time>{date}</time><span>{words} 字</span><span>作者 {author}</span>
  </div>
  {tags}
  <div class="content">
{content}
  </div>

  <div class="notice">
    <p><b>出处</b>：微信公众号「时空坐标」｜ 作者：吕文艺</p>
    {url_line}
    <p><b>声明</b>：本站内容仅供学习参考，版权归原作者所有，不构成任何建议。</p>
  </div>

  {related}
</article>

<footer class="wrap foot">
  <p><a href="../about.html">关于本站</a> · <a href="../index.html">← 返回首页</a></p>
  <p class="copy">© 时空人公益推广 · shikongren.com · 内容仅供学习参考</p>
</footer>
</body>
</html>'''


def build_post_html(a, by_cat):
    desc = clean_excerpt(a['body'], 100)
    tg = ''
    if a['tags']:
        tg = '<div class="tags">' + ''.join(
            '<a href="../index.html#t-%s">#%s</a>' % (esc(t), esc(t))
            for t in a['tags']) + '</div>'
    rel = [x for x in by_cat.get(a['category'], []) if x['slug'] != a['slug']][:6]
    rh = ''
    if rel:
        rh = ('<div class="related"><h4>同类文章</h4><ul>' +
              ''.join('<li><a href="%s.html">%s</a><time>%s</time></li>'
                      % (x['slug'], esc(x['title']), x['date']) for x in rel) +
              '</ul></div>')
    if a['url']:
        url_line = ('<p><b>原文链接</b>：<a href="%s" target="_blank" rel="noopener">%s</a></p>'
                    % (esc(a['url']), esc(a['url'])))
    else:
        url_line = '<p><b>原文链接</b>：微信公众号「时空坐标」</p>'
    canonical = DOMAIN + '/posts/' + urllib.parse.quote(a['slug'] + '.html', safe='/-.')
    return POST_TPL.format(
        title=esc(a['title']), desc=esc(desc), cat=esc(CATS.get(a['category'], a['category'])[0]),
        date=esc(a['date']), words=a['words'], author=esc(a['author']),
        url_line=url_line, tags=tg, site=SITE_NAME, canonical=canonical,
        content=md_to_html(a['body']), related=rh)


ABOUT_TPL = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · 时空人公益推广</title>
<meta name="description" content="{desc}">
<meta name="author" content="吕文艺">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="zh_CN">
<meta name="twitter:card" content="summary">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="style.css">
</head>
<body>
<header class="top"><div class="wrap">
  <a href="index.html" class="home">← 返回时空人公益推广</a>
</div></header>

<article class="wrap page">
  <h1>{title}</h1>
  <p class="pagesub">{subtitle}</p>
  <div class="content">
{content}
  </div>
</article>

<footer class="wrap foot">
  <div class="disclaimer">
    <h4>免责声明</h4>
    <p>本站内容仅供学习参考，版权归原作者所有，不构成任何建议。</p>
  </div>
  <p><a href="index.html">← 返回首页</a> · <a href="sitemap.xml">网站地图</a></p>
  <p class="copy">© 时空人公益推广 · shikongren.com · {n_arts} 篇原创文章</p>
</footer>
</body>
</html>'''


def _about_blocks(body):
    """把about.md 里的自定义块转成 HTML。

    源文件里用原始 HTML 写了三种块级容器：
      <div class="qrbox"> 二维码区（居中）
      <div class="feat">  功能介绍
      <div class="faq">   常见问答

    md_to_html 会把裸 HTML 转义，所以这里先把整块抠出来占位，
    单独走markdown 渲染（块内标题降级、Q/A 转问答样式），
    最后再把渲染结果塞回占位符。
    """
    blocks = []

    def grab(m):
        blocks.append((m.group(1), m.group(2)))
        return '@@BLK%d@@' % (len(blocks) - 1)

    # 匹配 <div class="xxx"> ... </div>
    # author 块内含嵌套 div（authorpic / authorinfo），
    # 所以用「允许一层嵌套」的写法：先匹配到下一个 </div>，
    # 若内部还有 <div 则继续往后找配对的闭合标签。
    def find_block(text, after_open):
        """从 text[after_open]（开标签之后）开始扫描，
        找到配对的 </div>，返回 (开标签内的内容, 结束位置)。

        after_open 是开标签的结束位置，所以返回的内容**不含**开标签。
        """
        depth = 1
        i = after_open
        while True:
            mo = re.compile(r'<div\b[^>]*>|</div>').search(text, i)
            if not mo:
                return text[after_open:], len(text)
            if mo.group(0).startswith('</'):
                depth -= 1
                if depth == 0:
                    return text[after_open:mo.start()], mo.end()
            else:
                depth += 1
            i = mo.end()

    out = []
    pos = 0
    pat = re.compile(r'<div class="(qrbox|feat|faq|author)">')
    while True:
        m = pat.search(body, pos)
        if not m:
            out.append(body[pos:])
            break
        out.append(body[pos:m.start()])
        inner, endpos = find_block(body, m.end())
        blocks.append((m.group(1), inner))
        out.append('@@BLK%d@@' % (len(blocks) - 1))
        pos = endpos
    src = ''.join(out)

    # 块外的裸 HTML（如作者配图 <img>）同样要保护
    outer_html = []

    def keep_outer(m):
        outer_html.append(m.group(0))
        return '@@OUT%d@@' % (len(outer_html) - 1)

    src = re.sub(r'<img\b[^>]*>', keep_outer, src, flags=re.I)

    # 剩下的正常渲染
    html = md_to_html(src, img_prefix='')
    # 块占位符独占一行时，md_to_html 会包一层 <p>，去掉
    html = re.sub(r'<p>\s*(@@BLK\d+@@)\s*</p>', r'\1', html)
    html = re.sub(r'<p>\s*(@@OUT\d+@@)\s*</p>', r'\1', html)
    for j, tag in enumerate(outer_html):
        html = html.replace('@@OUT%d@@' % j, tag)

    # 逐块渲染并回填
    for i, (cls, inner) in enumerate(blocks):
        # 块内可能直接写了裸 HTML 标签（about.md 的二维码区、作者卡就是），
        # 先把裸 HTML 抠出来占位，避免被 markdown 转义成文本。
        raw_html = []

        def keep_html(m):
            raw_html.append(m.group(0))
            return '@@HTML%d@@' % (len(raw_html) - 1)

        # 保留 <img ...>、<div ...>、</div> 这类块级结构标签
        # 占位符用 @@ 包裹 —— md_to_html 的 esc() 不会动 @ 和数字，
        # 而 \x00 控制符会被转义成可见字符。
        inner2 = re.sub(
            r'<img\b[^>]*>|</?div\b[^>]*>',
            keep_html, inner, flags=re.I)

        sub = md_to_html(inner2, img_prefix='')
        # 块内 h3 降级为 h4
        sub = re.sub(r'<h3>(.*?)</h3>', r'<h4>\1</h4>', sub)
        # Q/A 问答对
        def fix_qa(m):
            return ('<div class="qa"><div class="q">%s</div>'
                    '<div class="a">%s</div></div>') % (m.group(1), m.group(2))
        sub = re.sub(
            r'<p><strong>Q：(.*?)</strong></p>\s*<p>(.*?)</p>',
            fix_qa, sub)
        sub = re.sub(r'<p><strong>Q：(.*?)</strong></p>',
                     r'<div class="q">\1</div>', sub)
        # 回填裸 HTML
        for j, tag in enumerate(raw_html):
            sub = sub.replace('@@HTML%d@@' % j, tag)
        # 段落里独立的占位符（<p>@@HTML0@@</p>）去掉外壳
        sub = re.sub(r'<p>(\s*<img\b[^>]*>\s*)</p>', r'\1', sub)
        # 占位符独占一行时，包着的 <p> 也要去掉
        sub = re.sub(r'<p>(\s*</?div\b[^>]*>\s*)</p>', r'\1', sub)
        sub = re.sub(r'<p>(\s*@@HTML\d+@@\s*)</p>', r'\1', sub)

        html = html.replace('@@BLK%d@@' % i,
                            '<div class="%s">%s</div>' % (cls, sub))

    html = re.sub(r'<p>\s*</p>', '', html)
    html = re.sub(r'@@BLK\d+@@', '', html)
    html = re.sub(r'\x00HTML\d+\x00', '', html)
    return html


def build_about_html(page, arts):
    fm = page.get('frontmatter') or {}
    body = page.get('body') or ''
    title = fm.get('title') or '关于本站'
    subtitle = fm.get('subtitle') or ''
    desc = clean_excerpt(body, 120)
    canonical = DOMAIN + '/about.html'
    return ABOUT_TPL.format(
        title=esc(title), subtitle=esc(subtitle), desc=esc(desc),
        site=SITE_NAME, canonical=canonical,
        content=_about_blocks(body), n_arts=len(arts))


def build_sitemap(arts):
    urls = ['<url><loc>%s/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>' % DOMAIN,
            '<url><loc>%s/about.html</loc><changefreq>monthly</changefreq>'
            '<priority>0.8</priority></url>' % DOMAIN]
    for a in arts:
        urls.append('<url><loc>%s/posts/%s.html</loc><lastmod>%s</lastmod>'
                    '<changefreq>monthly</changefreq><priority>0.6</priority></url>'
                    % (DOMAIN, urllib.parse.quote(a['slug'], safe='/-.'),
                       a['date'] or '2026-01-01'))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + '\n'.join(urls) + '\n</urlset>\n')


# ---------------------------------------------------------------- 主流程

def main():
    check_only = '--check' in sys.argv

    arts = load_articles()
    if not arts:
        print('错误：content/posts 下没有找到任何 .md 文章')
        return 1

    cats = collections.Counter(a['category'] for a in arts)
    years = set(a['year'] for a in arts if a['year'])
    by_cat = collections.defaultdict(list)
    for a in arts:
        by_cat[a['category']].append(a)

    # 自检
    problems = []
    seen = {}
    for a in arts:
        if a['slug'] in seen:
            problems.append('重复 slug: %s' % a['slug'])
        seen[a['slug']] = 1
        if re.search(r'[%?#&=+\[\]()<>\\"\'|:*^~!@$,;\s]', a['slug']):
            problems.append('slug 含 URL 危险字符: %s' % a['slug'])
        if 'mmbiz.qpic.cn' in a['body']:
            problems.append('正文残留微信外链: %s' % a['slug'])
        if not a['title']:
            problems.append('缺标题: %s' % a['slug'])
    if problems:
        print('自检发现 %d 个问题：' % len(problems))
        for p in problems[:20]:
            print('  -', p)
        if check_only:
            return 1
        print('（仍继续构建）')

    print('文章数: %d   分类: %d   年份: %d' %
          (len(arts), len([c for c in CATS if cats.get(c, 0)]), len(years)))
    if check_only:
        return 0

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(POSTS_OUT, exist_ok=True)

    # 详情页
    n = 0
    for a in arts:
        with open(os.path.join(POSTS_OUT, a['slug'] + '.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(build_post_html(a, by_cat))
        n += 1

    # 数据
    with open(os.path.join(DATA_DIR, 'articles.json'), 'w', encoding='utf-8') as f:
        json.dump(arts, f, ensure_ascii=False, indent=1)
    idx = [{'t': a['title'], 's': a['slug'], 'd': a['date'], 'c': a['category'],
            'w': a['words'], 'b': clean_excerpt(a['body'], 260), 'g': a['tags']}
           for a in arts]
    with open(os.path.join(DATA_DIR, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))

    # ---- 全文检索索引（分片） ----
    # 全部正文纯文本 272 万字，一次性加载约 7.8MB，移动端加载会卡。
    # 方案：按分片输出 6 个 JSON，前端首次输入关键词时才异步加载，
    # 全部加载完约 1.3MB/片，可并行请求，且只在本机磁盘不走网络。
    FULL = []
    for a in arts:
        FULL.append({
            't': a['title'],
            's': a['slug'],
            'd': a['date'],
            'c': a['category'],
            'g': a['tags'],
            'x': plain_text(a['body']),
        })

    # 清单：告诉前端有多少片、分片文件名
    shard_dir = os.path.join(DATA_DIR, 'search')
    os.makedirs(shard_dir, exist_ok=True)
    # 清掉上一轮的旧分片，避免残留
    for old in os.listdir(shard_dir):
        if old.startswith('s') and old.endswith('.json'):
            os.remove(os.path.join(shard_dir, old))

    # 按字符数均衡分片（不是按篇数）—— 文章长短差异大（几百到3.4万字），
    # 按篇数分会出现 2MB 和 0.6MB 的失衡片，前端并行加载时
    # 慢的那片决定总耗时。中文 UTF-8 约 3 字节/字，
    # 目标每片 1.3MB ≈ 44 万字符。
    SHARD_CHARS = 450000
    shard_files = []
    total_full_bytes = 0
    buf = []
    buf_len = 0
    idx_written = 0
    for item in FULL:
        buf.append(item)
        buf_len += len(item['x']) + len(item['t']) + 40
        if buf_len >= SHARD_CHARS and len(shard_files) < 15:
            name = 's%d.json' % len(shard_files)
            with open(os.path.join(shard_dir, name), 'w', encoding='utf-8') as f:
                json.dump(buf, f, ensure_ascii=False, separators=(',', ':'))
            total_full_bytes += os.path.getsize(os.path.join(shard_dir, name))
            shard_files.append(name)
            buf = []
            buf_len = 0
    if buf:
        name = 's%d.json' % len(shard_files)
        with open(os.path.join(shard_dir, name), 'w', encoding='utf-8') as f:
            json.dump(buf, f, ensure_ascii=False, separators=(',', ':'))
        total_full_bytes += os.path.getsize(os.path.join(shard_dir, name))
        shard_files.append(name)

    with open(os.path.join(DATA_DIR, 'search-manifest.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'shards': shard_files, 'count': len(FULL)},
                  f, separators=(',', ':'))

    # 首页
    with open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(build_index_html(arts, cats, years))

    # 关于本站
    settings = load_settings()
    if 'about' in settings:
        with open(os.path.join(OUT, 'about.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(build_about_html(settings['about'], arts))
        print('关于页: 已生成')
    else:
        print('关于页: 缺少 content/settings/about.md，跳过')

    # sitemap / robots
    with open(os.path.join(OUT, 'sitemap.xml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(build_sitemap(arts))
    with open(os.path.join(OUT, 'robots.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n' % DOMAIN)

    print('详情页: %d   列表索引: %d   全文索引: %d 篇 / %d 分片 (%.1fMB)'
          % (n, len(idx), len(FULL), len(shard_files), total_full_bytes / 1024 / 1024))
    print('构建完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
