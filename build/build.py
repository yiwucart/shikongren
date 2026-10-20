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


def md_to_html(body, img_prefix='../'):
    """极简 Markdown -> HTML：段落 / 图片 / 粗体 / 斜体 / 链接

    markdown 里的图片写成 /img/xxx（绝对路径，Decap 预览和线上都通用），
    输出到 posts/ 子目录的详情页时需要转成 ../img/xxx。
    """
    def fix_src(u):
        if u.startswith('/img/'):
            return img_prefix + u[1:]
        return u

    out = []
    for blk in re.split(r'\n\s*\n', body):
        b = blk.strip()
        if not b:
            continue
        # 独立成行的图片
        m = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', b)
        if m:
            out.append('<figure><img src="%s" alt="%s" loading="lazy"></figure>'
                       % (esc(fix_src(m.group(2))), esc(m.group(1))))
            continue
        if b.startswith('!['):
            b = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)',
                       lambda mm: '<img src="%s" alt="%s" loading="lazy">'
                       % (esc(fix_src(mm.group(2))), esc(mm.group(1))), b)
        b = esc(b)
        # 段内图片
        b = re.sub(r'!\[([^\]]*)\]\(([^)\s]+)\)',
                   lambda mm: '<img src="%s" alt="%s" loading="lazy">'
                   % (esc(fix_src(mm.group(2))), esc(mm.group(1))), b)
        # 链接 [文字](地址)
        b = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
                   r'<a href="\2" target="_blank" rel="noopener">\1</a>', b)
        b = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', b)
        b = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', b)
        out.append('<p>%s</p>' % b)
    return '\n'.join(out)


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


def build_sitemap(arts):
    urls = ['<url><loc>%s/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>' % DOMAIN]
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

    # 首页
    with open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(build_index_html(arts, cats, years))

    # sitemap / robots
    with open(os.path.join(OUT, 'sitemap.xml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(build_sitemap(arts))
    with open(os.path.join(OUT, 'robots.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n' % DOMAIN)

    print('详情页: %d   索引: %d' % (n, len(idx)))
    print('构建完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
