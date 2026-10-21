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
import time
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
DOMAIN = 'https://www.shikongren.com'
SITE_NAME = '时空人公益推广'
AUTHOR = '吕文艺'

# 静态资源版本戳：每次构建用源文件 mtime 生成，
# 这样改了 style.css / app.js 后，URL 会变，浏览器自动取新版本。
# 否则浏览器会一直用缓存里的旧 CSS（站点是纯静态生成，
# 文件名不带内容哈希，没法靠 long-cache + immutable）。
def _stamp():
    try:
        latest = 0.0
        for name in ('style.css', 'app.js'):
            p = os.path.join(SRC, name)
            if os.path.exists(p):
                latest = max(latest, os.path.getmtime(p))
        if latest:
            return str(int(latest))
    except Exception:
        pass
    # 退化方案：用当前时间，保证每次构建 URL 都不同
    return str(int(time.time()))


STAMP = _stamp()

# ---------------------------------------------------------------- 多语言

# 三种语言站点：
#   zh-Hans（简体）→ 站点根目录        URL 前缀 ''
#   zh-Hant（繁體）→ /tc/             URL 前缀 'tc'
#   en（English）→ /en/              URL 前缀 'en'
#
# 繁体版不是独立维护的内容，而是**构建时由简体源经 OpenCC 自动生成**，
# 所以繁体文章不进 CMS（避免双份维护、避免两边不同步）。
# 英文版目前只出「语言选择页 + 关于页」，文章翻译需要人工审校，
# 确认范围后再开。
LANGS = [
    {
        'code': 'zh-Hans',
        'dir': '',                 # 相对站点根的子目录，空 = 根
        'html_lang': 'zh-CN',
        'label': '简',
        'full': '简体中文',
        'og': 'zh_CN',
    },
    {
        'code': 'zh-Hant',
        'dir': 'tc',
        'html_lang': 'zh-Hant',
        'label': '繁',
        'full': '繁體中文',
        'og': 'zh_TW',
        'opencc': 's2twp',         # 简体 -> 台湾正体+词汇
    },
    {
        'code': 'en',
        'dir': 'en',
        'html_lang': 'en',
        'label': 'EN',
        'full': 'English',
        'og': 'en_US',
        'stub': True,              # 只出选择页，不出文章列表
    },
]
LANG_BY_DIR = {l['dir']: l for l in LANGS}


def get_opencc(conf):
    """取（或建）该语言需要的 OpenCC 转换器。取不到就返回 None（降级为不转换）。"""
    name = conf.get('opencc')
    if not name:
        return None
    global _OPENCC_CACHE
    if name in _OPENCC_CACHE:
        return _OPENCC_CACHE[name]
    try:
        import opencc
        _OPENCC_CACHE[name] = opencc.OpenCC(name)
    except Exception as e:
        print('  ! 警告：OpenCC 不可用（%s），该语言跳过转换：%s' % (name, e))
        _OPENCC_CACHE[name] = None
    return _OPENCC_CACHE[name]


_OPENCC_CACHE = {}


def url_for(lang, rel=''):
    """某一语言下的绝对站内 URL（用于 canonical / hreflang / 切换器）。

    rel 是**该语言自己的**相对路径，例如繁体的 'posts/xxx.html'。
    'index.html' 归一化成目录形式（/、/tc/、/en/）—— 目录形式的 URL 更符合
    搜索引擎与用户的习惯，也让 hreflang 三条互指的路径保持一致。
    """
    d = lang['dir']
    if rel in ('', 'index.html'):
        return ('%s/%s/' % (DOMAIN, d)) if d else (DOMAIN + '/')
    return '%s/%s/%s' % (DOMAIN, d, rel) if d else '%s/%s' % (DOMAIN, rel)


def lang_prefix(lang):
    """页面内相对链接的前缀。从 /tc/ 页面里链 'posts/x.html' 要写成 '../posts/x.html'。"""
    return ('../' if lang['dir'] else '')


def build_switcher(cur, rel=''):
    """三语切换器。cur 是当前语言对象，rel 是当前页在该语言下的相对路径。"""
    cc = get_opencc(cur)
    lbl = cc.convert('语言切换') if cc else '语言切换'
    out = ['<nav class="langsw" aria-label="%s">' % lbl]
    for l in LANGS:
        if l['code'] == cur['code']:
            cls, mark, href, cur_attr = 'on', '<span class="cur"> · </span>', '#', ' aria-current="true"'
        elif l.get('stub') and rel.replace('index.html', ''):
            # 英文文章页还不存在，点过去会到英文说明页 —— 仍可达，保留链接
            cls, mark, href, cur_attr = '', '', url_for(l, 'index.html'), ''
        else:
            cls, mark, href, cur_attr = '', '', url_for(l, rel), ''
        out.append('<a class="%s" href="%s" hreflang="%s"%s title="%s">%s%s</a>'
                   % (cls, esc(href), l['code'], cur_attr, l['full'], l['label'], mark))
    out.append('</nav>')
    return ''.join(out)


def hreflang_tags(lang, rel='index.html'):
    """三语互指 + x-default。rel 用该语言自己的相对路径，各语言同 slug。

    **未上线的语言不进 hreflang** —— 声明一个不存在的语言版本会让搜索引擎
    反复抓 404。英文版目前只有说明页，所以文章页只声明简 / 繁两条。
    """
    base = rel.replace('index.html', '')
    is_index = base == ''
    tags = []
    for l in LANGS:
        if l.get('stub') and not is_index:
            continue
        href = url_for(l, base)
        tags.append('<link rel="alternate" hreflang="%s" href="%s">' % (l['code'], esc(href)))
    tags.append('<link rel="alternate" hreflang="x-default" href="%s">'
                % esc(url_for(LANGS[0], base)))
    return '\n'.join(tags)


# ---------------------------------------------------------------- 界面文案
# 界面固定文案（导航、按钮、页脚免责声明等）。繁体版用 OpenCC 现场转，
# 英文版暂时只有少量文案（stub 语言不出文章页，用不到多少）。
UI_ZH = {
    'eyebrow': '吕氏时空人 · 公益推广',
    'h1': '时空人公益推广',
    'subtitle': '吕文艺老师作品归档',
    'lede': '吕文艺老师「吕氏时空人」理论技术作品的整理与检索。本站收录微信公众号「时空坐标」原创文章 %d 篇，覆盖 %d 个主题，%d 年内容。',
    'ph': '输入标题或关键词，例如：案例集锦、八字、化解、择日…',
    'clear': '清除',
    'stat_n': '篇原创',
    'stat_first': '最早',
    'stat_last': '最新',
    'stat_words': '万字',
    'about_link': '了解本站 →',
    'about_bio': '「吕氏时空人」理论技术体系创立者，本站收录其公众号「时空坐标」原创文章。',
    'all': '全部',
    'nomatch': '没有匹配的文章，换个关键词试试。',
    'home': '首页',
    'back': '← 返回时空人公益推广',
    'back_home': '← 返回首页',
    'words_unit': '字',
    'author': '作者',
    'source': '出处',
    'source_v': '微信公众号「时空坐标」｜ 作者：吕文艺',
    'stmt': '声明',
    'stmt_v': '本站内容仅供学习参考，版权归原作者所有，不构成任何建议。',
    'related': '同类文章',
    'disclaimer': '免责声明',
    'disclaimer_v': '本站为传统文化交流性质的内容归档，所载文章观点与内容仅供学习参考，不构成任何建议，亦不作为任何决策依据。读者应独立判断、自负其责。',
    'copyright': '本站全部内容来源于微信公众号「时空坐标」，作者署名吕文艺，版权归原作者及原作者所在平台所有。',
    'nogoods': '本站与原作者不存在商业关系，内容经原作者授权整理归档，仅供学习参考。如需引用，请注明出处并以原文为准。',
    'takedown': '如原作者或权利人认为收录内容需要处理，请通过下方方式联系，将立即处理。',
    'follow': '关注原作者公众号「时空坐标」，获取最新文章',
    'wechat': '微信公众号：',
    'tel': '联系原作者：吕文艺老师',
    'about_site': '关于本站',
    'sitemap': '网站地图',
    'cms': '内容管理',
    'copy': '© 时空人公益推广 · shikongren.com · 内容仅供学习参考',
    'copy_n': '© 时空人公益推广 · shikongren.com · %d 篇原创文章',
    'site_name': '时空人公益推广',
    'wechat_name': '时空坐标',
    'author_name': '吕文艺',
    'account_alt': '吕文艺老师',
    'lang_label': '语言切换',
    'orig_link': '原文链接',
    'cat_crumb': '分类',
}


def ui(lang):
    """界面文案 —— 繁体由简体文案经 OpenCC 转（保证两个版本措辞永远一致）。"""
    cc = get_opencc(lang)
    if not cc:
        return dict(UI_ZH)
    out = {}
    for k, v in UI_ZH.items():
        out[k] = cc.convert(v) if '%' not in v else v
    # 带占位符的文案单独处理（%d 要保留）
    for k in ('lede', 'copy_n'):
        out[k] = cc.convert(UI_ZH[k])
    return out


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
    # 电话链接（tel:）—— 不加 target="_blank"
    text = re.sub(r'\[([^\]]+)\]\((tel:[^)\s]+)\)',
                  r'<a class="tel" href="\2">\1</a>', text)
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

def load_articles(lang=None):
    """读全部 .md 文章。传入 lang 时返回繁体版（OpenCC 转换标题/分类/标签/正文）。

    繁体转换在**内存里**做，不写临时文件 —— 源码仓库里永远只有简体一份。
    """
    lang = lang or LANGS[0]
    cc = get_opencc(lang)
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
        cat0 = fm.get('category') or '综合论述'
        cat = cat0
        tags = fm.get('tags') or []
        if isinstance(tags, str):
            tags = [tags]
        author = fm.get('author') or AUTHOR
        if cc:
            title = cc.convert(title)
            cat = cc.convert(cat0)
            body = cc.convert(body)
            tags = [cc.convert(str(t)) for t in tags]
            series = cc.convert(fm.get('series') or '')
            author = cc.convert(author)
        else:
            series = fm.get('series') or ''
        arts.append({
            'title': title,
            'slug': slug,
            'date': date,
            'year': date[:4] if len(date) >= 4 else '',
            'author': author,
            'url': fm.get('url') or '',
            'account': '时空坐标',
            'series': series,
            # cat_key 永远是**简体原名** —— 统计、分类导航、CATS 查表都用它，
            # 与语言无关；category 是显示名（繁体版里是繁体）。
            # 早前版本只存 category，繁体下 '预测案例'→'預測案例' 查不到 CATS，
            # 导致分类导航和「覆盖 N 个主题」全部失效。
            'cat_key': cat0,
            'category': cat,
            'tags': tags,
            'words': int(words),
            'body': body.strip(),
        })
    # 时间靠后靠前；同日按标题排
    arts.sort(key=lambda a: (a['date'] or '', a['title']), reverse=True)
    return arts


def cat_labels(lang):
    """分类显示名 —— 繁体时连分类说明一起转。"""
    cc = get_opencc(lang)
    out = collections.OrderedDict()
    for k, v in CATS.items():
        out[k] = (cc.convert(v[0]), cc.convert(v[1])) if cc else v
    return out


def cat_display(lang, key, fallback=None):
    """取分类显示名。

    CATS 里没有的分类（如「其他」—— 有文章用了它但表里没这一项）
    直接用原名，**不要**退化成 `L.get(k, k)[0]`：
    那样会拿到字符串的第二个字，把「其他」截成「其」。
    """
    name = fallback if fallback is not None else key
    pair = cat_labels(lang).get(key)
    if pair:
        return pair[0]
    cc = get_opencc(lang)
    return cc.convert(name) if cc else name


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


def _load_verify_metas():
    """从 content/settings/verification.md 提取搜索引擎验证用的 meta 标签。

    文件里「## meta 标签」小节下、每行一个完整的 <meta ...> 标签原文。
    这些标签会被注入到所有页面的 <head>，用于 Google / 必应 / 百度
    的「HTML 标签」方式验证站点所有权。
    """
    p = os.path.join(SETTINGS_DIR, 'verification.md')
    if not os.path.exists(p):
        return []
    body = parse_frontmatter(read_text(p))[1]
    # 只取「## meta 标签」到下一个 ## 之间的内容
    m = re.search(r'##\s*meta\s*标签(.*?)(?=\n##\s|\Z)', body, re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).split('\n'):
        s = line.strip()
        if not s.startswith('<meta'):
            continue
        # 注释行 <!-- ... --> 里的内容要跳过
        if s.startswith('<!--'):
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------- 输出

# 搜索引擎验证 meta 标签（来自 content/settings/verification.md）
VERIFY_BLOCK = '\n'.join(_load_verify_metas())


def _ld_json(data):
    """把 dict 序列化成可安全嵌入 <script> 的 JSON-LD 字符串。"""
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')


def ld_home(lang, canonical, title, desc, n_posts):
    """首页结构化数据：WebSite + SearchAction + ItemList。

    Google 会把 SearchAction 当作站点的搜索入口展示；
    ItemList 让首页的 690 篇文章以列表富结果呈现。
    """
    site_name = '时空人公益推广'
    data = {
        '@context': 'https://schema.org',
        '@graph': [
            {
                '@type': 'WebSite',
                '@id': DOMAIN + '/#website',
                'name': site_name,
                'alternateName': '吕氏时空人',
                'url': canonical,
                'inLanguage': lang.get('html_lang', 'zh-CN'),
                'publisher': {'@id': DOMAIN + '/#person'},
                'potentialAction': {
                    '@type': 'SearchAction',
                    'target': {
                        '@type': 'EntryPoint',
                        'urlTemplate': canonical + '?q={search_term_string}',
                    },
                    'query-input': 'required name=search_term_string',
                },
            },
            {
                '@type': 'Person',
                '@id': DOMAIN + '/#person',
                'name': '吕文艺',
                'url': canonical + 'about.html',
                'description': '吕氏时空人理论技术作者',
            },
        ],
    }
    return _ld_json(data)


def ld_post(a, canonical, title, desc, lang):
    """文章详情页结构化数据：Article / BlogPosting + BreadcrumbList。

    datePublished / dateModified 用文章的原始日期，
    有了它 Google 才有资格给文章加「日期」富结果标签。
    """
    d = a['date'] or '2026-01-01'
    date_iso = d + 'T00:00:00+08:00'
    data = {
        '@context': 'https://schema.org',
        '@graph': [
            {
                '@type': 'BlogPosting',
                '@id': canonical + '#article',
                'mainEntityOfPage': {'@type': 'WebPage', '@id': canonical},
                'headline': title[:110],
                'description': desc,
                'datePublished': date_iso,
                'dateModified': date_iso,
                'inLanguage': lang.get('html_lang', 'zh-CN'),
                'wordCount': a.get('words', 0),
                'author': {
                    '@type': 'Person',
                    'name': '吕文艺',
                    'url': DOMAIN + '/about.html',
                },
                'publisher': {
                    '@type': 'Organization',
                    'name': '时空人公益推广',
                    'url': DOMAIN + '/',
                },
                'isPartOf': {
                    '@type': 'Blog',
                    'name': '时空人公益推广',
                    'url': DOMAIN + '/',
                },
            },
            {
                '@type': 'BreadcrumbList',
                'itemListElement': [
                    {'@type': 'ListItem', 'position': 1, 'name': '首页', 'item': DOMAIN + '/'},
                    {'@type': 'ListItem', 'position': 2, 'name': '关于', 'item': DOMAIN + '/about.html'},
                    {'@type': 'ListItem', 'position': 3, 'name': title[:60]},
                ],
            },
        ],
    }
    return _ld_json(data)


def ld_page(canonical, title, desc, lang):
    """关于页 / EN 页等普通页面：WebPage + BreadcrumbList。"""
    data = {
        '@context': 'https://schema.org',
        '@type': 'WebPage',
        '@id': canonical + '#webpage',
        'url': canonical,
        'name': title,
        'description': desc,
        'inLanguage': lang.get('html_lang', 'zh-CN'),
        'isPartOf': {'@type': 'WebSite', 'url': DOMAIN + '/'},
    }
    return _ld_json(data)


def build_index_html(arts, cats, years, lang):
    T = ui(lang)
    L = cat_labels(lang)
    pfx = lang_prefix(lang)
    cards = []
    for a in arts:
        ex = clean_excerpt(a['body'])
        cards.append(
            '<a class="card" href="posts/%s.html">'
            '<div class="c">%s</div>'
            '<h3>%s</h3>'
            '<p class="ex">%s</p>'
            '<div class="m"><time>%s</time><span>%d %s</span></div>'
            '</a>' % (esc(a['slug']), esc(a['category']), esc(a['title']),
                      esc(ex), esc(a['date']), a['words'], T['words_unit']))

    nav = ''.join('<a href="#cat-%s">%s <em>%d</em></a>' % (c, esc(cat_display(lang, c)), cats.get(c, 0))
                  for c in cats if cats.get(c, 0))

    total_words = sum(a['words'] for a in arts)
    n_cat = len([c for c in CATS if cats.get(c, 0)])

    return INDEX_TPL.format(
        hlang=lang['html_lang'],
        title='%s · %s' % (T['h1'], T['subtitle']),
        desc='吕文艺老师「吕氏时空人」理论技术作品归档与检索站，收录原创文章 %d 篇。' % len(arts),
        stamp=STAMP,
        canonical=url_for(lang, 'index.html'),
        og=lang['og'],
        site=T['site_name'],
        alt=hreflang_tags(lang, 'index.html'),
        verify=VERIFY_BLOCK,
        eyebrow=T['eyebrow'],
        h1=T['h1'],
        lede=T['lede'] % (len(arts), n_cat, len(years)),
        s_n=len(arts), s_nu=T['stat_n'],
        s_first=min(years) if years else '', s_firstu=T['stat_first'],
        s_last=max(years) if years else '', s_lastu=T['stat_last'],
        s_words=total_words // 10000, s_wordsu=T['stat_words'],
        ph=T['ph'], clear=T['clear'],
        bio=T['about_bio'], aboutlink=T['about_link'],
        author_name=T['author_name'], author_alt=T['account_alt'],
        wechat_name=T['wechat_name'],
        langsw=build_switcher(lang, 'index.html'),
        allu=T['all'], nav=nav, cards=''.join(cards), nomatch=T['nomatch'],
        disc_h=T['disclaimer'], disc=T['disclaimer_v'],
        cp=T['copyright'], ng=T['nogoods'], td=T['takedown'],
        follow=T['follow'], wechat=T['wechat'], tel=T['tel'],
        about_site=T['about_site'], sitemap=T['sitemap'], cms=T['cms'],
        copy=T['copy'],
        pfx=pfx,
        ld=ld_home(lang, url_for(lang, 'index.html'),
                   '%s · %s' % (T['h1'], T['subtitle']),
                   '吕文艺老师「吕氏时空人」理论技术作品归档与检索站，收录原创文章 %d 篇。' % len(arts),
                   len(arts)))


INDEX_TPL = '''<!doctype html>
<html lang="{hlang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="stylesheet" href="{pfx}style.css?v={stamp}">
<link rel="canonical" href="{canonical}">
{alt}
<meta property="og:url" content="{canonical}">
<meta property="og:type" content="website">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="{og}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="baidu-site-verification" content="codeva-m8N0GhhwKD" />
<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
{verify}
<script type="application/ld+json">{ld}</script>
</head>
<body>
<header class="hero">
  <div class="wrap">
    <p class="eyebrow">{eyebrow}</p>
    {langsw}
    <h1>{h1}</h1>
    <p class="lede">{lede}</p>
    <div class="stats">
      <span><b>{s_n}</b> {s_nu}</span>
      <span><b>{s_first}</b> {s_firstu}</span>
      <span><b>{s_last}</b> {s_lastu}</span>
      <span><b>{s_words}</b> {s_wordsu}</span>
    </div>
    <div class="searchbar">
      <input id="q" type="search" placeholder="{ph}" autocomplete="off">
      <button id="clear">{clear}</button>
    </div>
    <div id="count" class="count"></div>
    <div class="herobio">
      <img src="/img/uploads/luwenyi-avatar.jpg" alt="{author_alt}" width="56" height="56" />
      <p><b>{author_name}</b>　{bio}
        <a href="about.html">{aboutlink}</a></p>
    </div>
  </div>
</header>

<nav class="cats wrap">
  <a href="#" class="all">{allu}</a>{nav}
</nav>

<main class="wrap">
  <div id="grid" class="grid">{cards}</div>
  <p id="empty" class="empty" hidden>{nomatch}</p>
</main>

<footer class="wrap foot">
  <div class="disclaimer">
    <h4>{disc_h}</h4>
    <p>{disc}</p>
  </div>
  <div class="src">
    <p>{cp}</p>
    <p>{ng}</p>
    <p>{td}</p>
  </div>
  <div class="contact">
    <p>{follow}</p>
    <p>{wechat}<b>{wechat_name}</b></p>
    <p>{tel} <a class="tel" href="tel:18601944999">18601944999</a></p>
  </div>
  <p class="footlinks">
    <a href="about.html">{about_site}</a> ·
    <a href="{pfx}sitemap.xml">{sitemap}</a> ·
    <a href="/admin/">{cms}</a>
  </p>
  <p class="copy">{copy}</p>
</footer>

<script src="{pfx}app.js?v={stamp}"></script>
</body>
</html>'''


POST_TPL = '''<!doctype html>
<html lang="{hlang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {site}</title>
<meta name="description" content="{desc}">
<meta name="author" content="{author}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="{og}">
<meta property="article:author" content="{author}">
<meta property="article:published_time" content="{pubdate}">
<meta property="article:modified_time" content="{pubdate}">
<meta property="article:section" content="{category}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="baidu-site-verification" content="codeva-m8N0GhhwKD" />
<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
<link rel="canonical" href="{canonical}">
{alt}
<link rel="stylesheet" href="{pfx}style.css?v={stamp}">
{verify}
<script type="application/ld+json">{ld}</script>
</head>
<body>
<header class="top"><div class="wrap">
  <a href="{pfx}index.html" class="home">{back}</a>
  {langsw}
</div></header>

<article class="wrap post">
  <nav class="crumb"><a href="{pfx}index.html">{home}</a> / <span>{cat}</span></nav>
  <h1>{title}</h1>
  <div class="meta">
    <span class="badge">{cat}</span>
    <time>{date}</time><span>{words} {wu}</span><span>{author_l} {author}</span>
  </div>
  {tags}
  <div class="content">
{content}
  </div>

  <div class="notice">
    <p><b>{source}</b>：{source_v}</p>
    {url_line}
    <p><b>{stmt}</b>：{stmt_v}</p>
  </div>

  {related}
</article>

<footer class="wrap foot">
  <div class="contact">
    <p>{tel} <a class="tel" href="tel:18601944999">18601944999</a> · {wechat_v}</p>
  </div>
  <p><a href="{pfx}about.html">{about_site}</a> · <a href="{pfx}index.html">{back_home}</a></p>
  <p class="copy">{copy}</p>
</footer>
</body>
</html>'''


def build_post_html(a, by_cat, lang):
    T = ui(lang)
    L = cat_labels(lang)
    # 详情页在 <lang>/posts/ 下，比索引页深一层，站内相对链接要多上一级 ../
    pfx = '../../' if lang['dir'] else '../'
    desc = clean_excerpt(a['body'], 100)
    tg = ''
    if a['tags']:
        tg = '<div class="tags">' + ''.join(
            '<a href="%sindex.html#t-%s">#%s</a>' % (pfx, esc(t), esc(t))
            for t in a['tags']) + '</div>'
    rel = [x for x in by_cat.get(a['category'], []) if x['slug'] != a['slug']][:6]
    rh = ''
    if rel:
        rh = ('<div class="related"><h4>%s</h4><ul>' % T['related'] +
              ''.join('<li><a href="%s.html">%s</a><time>%s</time></li>'
                      % (x['slug'], esc(x['title']), x['date']) for x in rel) +
              '</ul></div>')
    if a['url']:
        url_line = ('<p><b>%s</b>：<a href="%s" target="_blank" rel="noopener">%s</a></p>'
                    % (T['orig_link'], esc(a['url']), esc(a['url'])))
    else:
        url_line = '<p><b>%s</b>：%s</p>' % (T['orig_link'], T['source_v'].split('｜')[0])
    slugq = urllib.parse.quote(a['slug'] + '.html', safe='/-.')
    return POST_TPL.format(
        hlang=lang['html_lang'],
        title=esc(a['title']), desc=esc(desc), cat=esc(cat_display(lang, a['cat_key'])),
        date=esc(a['date']), words=a['words'], wu=T['words_unit'],
        author=esc(a['author']), author_l=T['author'],
        url_line=url_line, tags=tg, site=T['site_name'], og=lang['og'],
        canonical=url_for(lang, 'posts/' + slugq),
        alt=hreflang_tags(lang, 'posts/' + slugq),
        langsw=build_switcher(lang, 'posts/' + slugq),
        back=T['back'], home=T['home'], pfx=pfx,
        source=T['source'], source_v=T['source_v'], stmt=T['stmt'], stmt_v=T['stmt_v'],
        content=md_to_html(a['body']), related=rh, stamp=STAMP,
        verify=VERIFY_BLOCK, tel=T['tel'], wechat_v=T['source_v'].split('｜')[0].strip(),
        about_site=T['about_site'], back_home=T['back_home'], copy=T['copy'],
        ld=ld_post(a, url_for(lang, 'posts/' + urllib.parse.quote(a['slug'] + '.html', safe='/-.')),
                   a['title'], desc, lang),
        pubdate=(a['date'] or '2026-01-01'),
        category=cat_display(lang, a['category'], a['category']))


ABOUT_TPL = '''<!doctype html>
<html lang="{hlang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {site}</title>
<meta name="description" content="{desc}">
<meta name="author" content="吕文艺">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="{og}">
<meta name="twitter:card" content="summary">
<meta name="baidu-site-verification" content="codeva-m8N0GhhwKD" />
<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
<link rel="canonical" href="{canonical}">
{alt}
<link rel="stylesheet" href="{pfx}style.css?v={stamp}">
{verify}
<script type="application/ld+json">{ld}</script>
</head>
<body>
<header class="top"><div class="wrap">
  <a href="{pfx}index.html" class="home">{back}</a>
  {langsw}
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
    <h4>{disc_h}</h4>
    <p>{disc}</p>
  </div>
  <p><a href="{pfx}index.html">{back_home}</a> · <a href="{pfx}sitemap.xml">{sitemap}</a></p>
  <p class="copy">{copy}</p>
</footer>
</body>
</html>'''


def _about_blocks(body):
    """把about.md 里的自定义块转成 HTML。

    源文件里用原始 HTML 写了三种块级容器：
      <div class="qrbox"> 二维码区（居中）
      <div class="feat">  功能介绍
      <div class="faq">   常见问答
      <div class="author">作者卡
      <div class="bio">   履历（列表）
      <div class="works"> 著作（列表）
      <div class="courses">课程体系（含表格）
      <div class="contact"> 联系方式

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
    pat = re.compile(r'<div class="(qrbox|feat|faq|author|bio|works|courses|contact)">')
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

        # 保留 <img ...>、<div ...>、</div>、<a ...> 这类块级结构标签
        # 占位符用 @@ 包裹 —— md_to_html 的 esc() 不会动 @ 和数字，
        # 而 \x00 控制符会被转义成可见字符。
        inner2 = re.sub(
            r'<img\b[^>]*>|</?div\b[^>]*>|</?a\b[^>]*>',
            keep_html, inner, flags=re.I)

        sub = md_to_html(inner2, img_prefix='')
        # 课程体系里的表格包一层横向滚动容器，手机上不撑破布局
        if '<table>' in sub:
            sub = sub.replace('<table>',
                              '<div class="tablewrap"><table>')
            sub = sub.replace('</table>', '</table></div>')
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


def build_about_html(page, arts, lang):
    T = ui(lang)
    cc = get_opencc(lang)
    fm = page.get('frontmatter') or {}
    body = page.get('body') or ''
    title = fm.get('title') or '关于本站'
    subtitle = fm.get('subtitle') or ''
    if cc:
        title = cc.convert(title)
        subtitle = cc.convert(subtitle) if subtitle else subtitle
        body = cc.convert(body)
    desc = clean_excerpt(body, 120)
    slugq = urllib.parse.quote('about.html', safe='/-.')
    return ABOUT_TPL.format(
        hlang=lang['html_lang'],
        title=esc(title), subtitle=esc(subtitle), desc=esc(desc),
        site=T['site_name'], og=lang['og'],
        canonical=url_for(lang, 'about.html'),
        alt=hreflang_tags(lang, 'about.html'),
        langsw=build_switcher(lang, 'about.html'),
        back=T['back'], pfx=lang_prefix(lang),
        content=_about_blocks(body), stamp=STAMP,
        verify=VERIFY_BLOCK, disc_h=T['disclaimer'], disc=T['stmt_v'],
        back_home=T['back_home'], sitemap=T['sitemap'],
        copy=T['copy_n'] % len(arts),
        ld=ld_page(url_for(lang, 'about.html'), title, desc, lang))


EN_TPL = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shikongren Archive · Lü Wenyi\'s works</title>
<meta name="description" content="An archive and search index of Lü Wenyi\'s original articles on the Lü School of Shikongren (时空人) theory.">
<link rel="stylesheet" href="{pfx}style.css?v={stamp}">
<link rel="canonical" href="{canonical}">
{alt}
<meta property="og:url" content="{canonical}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="en_US">
<meta name="twitter:card" content="summary">
<meta name="baidu-site-verification" content="codeva-m8N0GhhwKD" />
<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
{verify}
<script type="application/ld+json">{ld}</script>
</head>
<body>
<header class="top"><div class="wrap">
  <a href="../index.html" class="home">&larr; Back to the Chinese archive</a>
  {langsw}
</div></header>

<article class="wrap page">
  <h1>English edition is being prepared</h1>
  <p class="pagesub">{n} original articles &middot; Lü Wenyi (吕文艺), founder of the Lü School of Shikongren</p>
  <div class="content">
    <div class="notice">
      <p><b>Status</b>: This English edition is <b>not yet available</b>. The Chinese archive is
      complete with {n} articles; a full English translation is being prepared and reviewed.</p>
      <p><b>Why it takes time</b>: These articles concern Chinese metaphysical arts
      (八字 Bāzì / 風水 Fēngshuǐ / 六爻 Liùyáo) whose technical terms
      &mdash; 用神 <i>yòngshén</i>, 忌神 <i>jìshén</i>, 大運 <i>dàyùn</i>, 五行 <i>wǔxíng</i> &mdash;
      have no standard English equivalents. A literal machine translation would be misleading,
      so every article is reviewed by a human before publication.</p>
      <p><b>In the meantime</b>, please use the Chinese edition. If you read Chinese and would like
      to help review translations, please get in touch.</p>
    </div>

    <h2>Read in Chinese</h2>
    <ul>
      <li><a href="../index.html">Simplified Chinese &mdash; {n} articles, full-text search</a></li>
      <li><a href="../tc/index.html">Traditional Chinese &mdash; {n} articles, full-text search</a></li>
      <li><a href="../about.html">About this site</a></li>
    </ul>

    <h2>About Lü Wenyi (吕文艺)</h2>
    <p>Lü Wenyi is the founder of the Lü School of Shikongren (吕氏时空人), a system that
    approaches Chinese metaphysics as a natural science. He has taught it publicly since 2001
    and has published over ten million characters of original writing, roughly 90% of which is
    his own. More than two hundred thousand students have studied his work, of whom about
    fourteen thousand attended in-person classes.</p>
    <p>His published books include
    <i>Natural Science Feng Shui</i> (2018, 410pp),
    <i>Natural Science Eight Characters</i> (2018, 496pp),
    <i>Six Lines Prediction</i>, <i>Character Analysis</i>,
    <i>Twenty-three Master Techniques of Lü School Feng Shui</i>, and others.</p>
    <p>The full archive of his articles is published under the WeChat public account
    「时空坐标」 and collected on this site for study and reference only.</p>
  </div>
</article>

<footer class="wrap foot">
  <div class="disclaimer">
    <h4>Disclaimer</h4>
    <p>All content on this site is collected for the study of traditional Chinese culture.
    The views expressed are for reference only and constitute no advice. Readers should
    judge independently and bear their own responsibility.</p>
  </div>
  <p><a href="../index.html">&larr; Back to homepage</a> &middot;
     <a href="../tc/index.html">繁體中文</a> &middot;
     <a href="../sitemap.xml">Sitemap</a></p>
  <p class="copy">&copy; Shikongren Archive &middot; shikongren.com &middot; For study and reference only</p>
</footer>
</body>
</html>'''


def build_sitemap(arts, lang):
    urls = ['<url><loc>%s</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>'
            % url_for(lang, 'index.html'),
            '<url><loc>%s</loc><changefreq>monthly</changefreq>'
            '<priority>0.8</priority></url>' % url_for(lang, 'about.html')]
    if lang.get('stub'):
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + '\n'.join(urls) + '\n</urlset>\n')
    # 三语互指：xhtml:link 必须写在**同一个 <url> 节点内部**。
    # 早期版本把每种语言各生成一个独立 <url> 节点，loc 完全相同，
    # 结果 690 篇文章生成了 2072 条 loc（只有 692 条唯一），
    # 百度对重复 sitemap 会判定站点质量差。详见 2026-10-21 排查记录。
    alts_by_lang = [l for l in LANGS if l['code'] != lang['code']]
    for a in arts:
        loc = url_for(lang, 'posts/' + urllib.parse.quote(a['slug'] + '.html', safe='/-.'))
        alt = ''
        for l in alts_by_lang:
            if l.get('stub'):
                continue
            alt += ('<xhtml:link rel="alternate" hreflang="%s" href="%s"/>'
                    % (l['code'],
                       url_for(l, 'posts/' + urllib.parse.quote(a['slug'] + '.html', safe='/-.'))))
        urls.append('<url><loc>%s</loc><lastmod>%s</lastmod>'
                    '<changefreq>monthly</changefreq><priority>0.6</priority>%s</url>'
                    % (loc, a['date'] or '2026-01-01', alt))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
            + '\n'.join(urls) + '\n</urlset>\n')


# ---------------------------------------------------------------- 单语言构建

def build_one_lang(lang, settings):
    """构建一种语言的完整站点。输出到 OUT/[<dir>/]。

    简体 → 站点根；繁體 → OUT/tc/；English → OUT/en/。
    每个语言有独立的 data/ 目录（articles.json / index.json / search-manifest.json），
    这样前端按当前页面语言加载对应索引，互不干扰。
    """
    out_dir = os.path.join(OUT, lang['dir']) if lang['dir'] else OUT
    data_dir = os.path.join(out_dir, 'data')
    posts_out = os.path.join(out_dir, 'posts')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(posts_out, exist_ok=True)

    arts = load_articles(lang)
    cats = collections.Counter(a['cat_key'] for a in arts)
    years = set(a['year'] for a in arts if a['year'])
    by_cat = collections.defaultdict(list)
    for a in arts:
        by_cat[a['cat_key']].append(a)

    # 前端按这个字段决定加载哪个索引
    idx_prefix = ('%s/data/' % lang['dir']) if lang['dir'] else '/data/'

    stub = bool(lang.get('stub'))

    if stub:
        # 英文版暂只有一篇说明页，放在该语言的 index.html（搜索引擎抓 /en/ 就是它）
        with open(os.path.join(out_dir, 'index.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(EN_TPL.format(
                stamp=STAMP, site=SITE_NAME, n=len(arts), pfx='../',
                canonical=url_for(lang, 'index.html'),
                alt=hreflang_tags(lang, 'index.html'),
                langsw=build_switcher(lang, 'index.html'),
                verify=VERIFY_BLOCK,
                ld=ld_page(url_for(lang, 'index.html'),
                           'Shikongren Archive · Lü Wenyi\'s works',
                           'An archive and search index of Lü Wenyi\'s original articles.',
                           lang)))
        with open(os.path.join(out_dir, 'about.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(EN_TPL.format(
                stamp=STAMP, site=SITE_NAME, n=len(arts), pfx='../',
                canonical=url_for(lang, 'about.html'),
                alt=hreflang_tags(lang, 'about.html'),
                langsw=build_switcher(lang, 'about.html'),
                verify=VERIFY_BLOCK,
                ld=ld_page(url_for(lang, 'about.html'),
                           'Shikongren Archive · Lü Wenyi\'s works',
                           'An archive and search index of Lü Wenyi\'s original articles.',
                           lang)))
        with open(os.path.join(out_dir, 'sitemap.xml'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(build_sitemap([], lang))
        return dict(n=0, brief=0, full=0, shards=0)

    # 详情页
    for a in arts:
        with open(os.path.join(posts_out, a['slug'] + '.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(build_post_html(a, by_cat, lang))

    with open(os.path.join(data_dir, 'articles.json'), 'w', encoding='utf-8') as f:
        json.dump(arts, f, ensure_ascii=False, indent=1)
    idx = [{'t': a['title'], 's': a['slug'], 'd': a['date'], 'c': a['category'],
            'w': a['words'], 'b': clean_excerpt(a['body'], 260), 'g': a['tags']}
           for a in arts]
    with open(os.path.join(data_dir, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))

    brief_bytes, full_bytes, n_shard = build_search_index(arts, data_dir)

    # 繁简搜索映射表：繁体页允许用简体关键词搜（「阳宅」→ 命中「陽宅」）。
    # 映射表从**真实语料**用 OpenCC 提取（1194 对正向 / 1238 对反向），
    # 不是通用 OpenCC 全表 —— 只覆盖本站实际用到的字，体积小、命中准。
    tcmap_src = os.path.join(SRC, 'build', 'tcmap.json')
    if os.path.isfile(tcmap_src):
        with open(tcmap_src, 'rb') as fr:
            blob = fr.read()
        with open(os.path.join(data_dir, 'tcmap.json'), 'wb') as fw:
            fw.write(blob)

    # 繁简搜索映射表：繁体页允许用简体关键词搜（「阳宅」→ 命中「陽宅」）。
    # 映射表从**真实语料**用 OpenCC 提取（1194 对正向 / 1238 对反向），
    # 不是通用 OpenCC 全表 —— 只覆盖本站实际用到的字，体积小、命中准。
    tcmap_src = os.path.join(SRC, 'build', 'tcmap.json')
    if os.path.isfile(tcmap_src):
        with open(tcmap_src, 'rb') as fr:
            blob = fr.read()
        with open(os.path.join(data_dir, 'tcmap.json'), 'wb') as fw:
            fw.write(blob)

    # 首页
    with open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(build_index_html(arts, cats, years, lang))

    # 关于页
    html = ''
    if 'about' in settings:
        html = build_about_html(settings['about'], arts, lang)
    if html:
        with open(os.path.join(out_dir, 'about.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(html)

    # sitemap：每种语言一份，放在该语言的目录里，与页面层级一致
    with open(os.path.join(out_dir, 'sitemap.xml'), 'w',
              encoding='utf-8', newline='\n') as f:
        f.write(build_sitemap(arts, lang))

    # sitemapindex.xml：根目录一份，汇总三语 sitemap
    if not lang['dir']:
        with open(os.path.join(out_dir, 'sitemapindex.xml'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(build_sitemap_index())

    # robots.txt：只根目录一份，指向主 sitemap。
    # 站点在百度资源平台验证的是 https://www.shikongren.com，
    # 所以这里必须用 www 域名，否则百度会当成两个不同站点。
    if not lang['dir']:
        with open(os.path.join(out_dir, 'robots.txt'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(
                # 显式放行百度爬虫。理论上 User-agent: * 已经覆盖，
                # 但站长平台的「Robots 检查」工具按 UA 精确匹配，
                # 显式写一段 Baiduspider 能让检查结果直接显示「允许」，
                # 也避免以后有人加了别的 Disallow 规则误伤。
                'User-agent: Baiduspider\n'
                'Allow: /\n\n'
                'User-agent: Baiduspider-image\n'
                'Allow: /\n\n'
                'User-agent: Bingbot\n'
                'Allow: /\n\n'
                'User-agent: Googlebot\n'
                'Allow: /\n\n'
                'User-agent: Googlebot-Image\n'
                'Allow: /\n\n'
                'User-agent: *\n'
                'Allow: /\n\n'
                'Sitemap: %s/sitemap.xml\n' % DOMAIN)

    # 把语言信息写进 manifest，前端据此知道去哪加载、当前是什么语言
    mpath = os.path.join(data_dir, 'search-manifest.json')
    with open(mpath, encoding='utf-8') as f:
        manifest = json.load(f)
    manifest['lang'] = lang['code']
    manifest['dir'] = lang['dir']
    manifest['indexBase'] = idx_prefix
    with open(mpath, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(',', ':'))

    return dict(n=len(arts), brief=brief_bytes, full=full_bytes, shards=n_shard)


def build_sitemap_index():
    """生成 sitemapindex.xml —— 汇总简 / 繁 / en 三份 sitemap。

    百度 sitemap 提交有「天级提交配额」和「存量文件配额」两重限制，
    未备案新站的天级配额往往是 0。提交一个 sitemapindex 比提交三份
    sitemap 更省配额（存量文件名额按份数算）。
    """
    items = []
    for l in LANGS:
        loc = url_for(l, 'sitemap.xml')
        items.append('  <sitemap><loc>%s</loc></sitemap>' % loc)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + '\n'.join(items) + '\n</sitemapindex>\n')


def build_search_index(arts, data_dir):
    """生成双层检索索引（摘要层 + 全文分片层）。

    全部正文纯文本 272 万字，一次性加载约 7.9MB，跨境访问要好几分钟。
    改成两层：
      1) 摘要索引 summaries.json —— 标题 + 正文前 600 字，约 1.2MB，
         gzip 后约 0.45MB，输入关键词后立即加载并出结果（秒开）。
      2) 全文索引 s*.json —— 完整正文分片，摘要层结果不够时按需补充。
    """
    SUMMARY_CHARS = 600
    BRIEF, FULL = [], []
    for a in arts:
        pt = plain_text(a['body'])
        base = {'t': a['title'], 's': a['slug'], 'd': a['date'],
                'c': a['category'], 'g': a['tags']}
        BRIEF.append(dict(base, x=pt[:SUMMARY_CHARS]))
        FULL.append(dict(base, x=pt))

    shard_dir = os.path.join(data_dir, 'search')
    os.makedirs(shard_dir, exist_ok=True)
    for old in os.listdir(shard_dir):
        if old.startswith('s') and old.endswith('.json'):
            os.remove(os.path.join(shard_dir, old))

    brief_path = os.path.join(shard_dir, 'summaries.json')
    with open(brief_path, 'w', encoding='utf-8') as f:
        json.dump(BRIEF, f, ensure_ascii=False, separators=(',', ':'))
    brief_bytes = os.path.getsize(brief_path)

    # 按字符数均衡分片（不是按篇数）—— 文章长短差异大（几百到 3.4 万字），
    # 按篇数分会出现 2MB 和 0.6MB 的失衡片，前端并行加载时慢的那片决定总耗时。
    SHARD_CHARS = 450000
    shard_files = []
    total_full = 0
    buf, buf_len = [], 0
    for item in FULL:
        buf.append(item)
        buf_len += len(item['x']) + len(item['t']) + 40
        if buf_len >= SHARD_CHARS and len(shard_files) < 15:
            name = 's%d.json' % len(shard_files)
            with open(os.path.join(shard_dir, name), 'w', encoding='utf-8') as f:
                json.dump(buf, f, ensure_ascii=False, separators=(',', ':'))
            total_full += os.path.getsize(os.path.join(shard_dir, name))
            shard_files.append(name)
            buf, buf_len = [], 0
    if buf:
        name = 's%d.json' % len(shard_files)
        with open(os.path.join(shard_dir, name), 'w', encoding='utf-8') as f:
            json.dump(buf, f, ensure_ascii=False, separators=(',', ':'))
        total_full += os.path.getsize(os.path.join(shard_dir, name))
        shard_files.append(name)

    with open(os.path.join(data_dir, 'search-manifest.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'shards': shard_files, 'count': len(FULL),
                   'summary': 'search/summaries.json'},
                  f, ensure_ascii=False, separators=(',', ':'))

    return brief_bytes, total_full, len(shard_files)


# ---------------------------------------------------------------- 主流程

def main():
    check_only = '--check' in sys.argv

    arts = load_articles()
    if not arts:
        print('错误：content/posts 下没有找到任何 .md 文章')
        return 1

    # 自检（只对简体源做一次，与语言无关）
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

    cats = collections.Counter(a['cat_key'] for a in arts)
    years = set(a['year'] for a in arts if a['year'])
    print('文章数: %d   分类: %d   年份: %d' %
          (len(arts), len([c for c in CATS if cats.get(c, 0)]), len(years)))
    if check_only:
        return 0

    settings = load_settings()

    # 搜索引擎验证文件（verify/ 目录下的文件原样复制到站点根目录）
    verify_src = os.path.join(SRC, 'verify')
    n_verify = 0
    if os.path.isdir(verify_src):
        for fn in sorted(os.listdir(verify_src)):
            fpath = os.path.join(verify_src, fn)
            if not os.path.isfile(fpath) or not re.match(r'^[A-Za-z0-9._-]+$', fn):
                continue
            with open(fpath, 'rb') as fr:
                blob = fr.read()
            with open(os.path.join(OUT, fn), 'wb') as fw:
                fw.write(blob)
            n_verify += 1
    if n_verify:
        print('验证文件: %d 个已复制到站点根目录' % n_verify)

    for lang in LANGS:
        r = build_one_lang(lang, settings)
        if lang.get('stub'):
            print('%s: 选择页已生成（文章翻译待做）' % lang['full'])
        else:
            print('%s: 详情页 %d   摘要索引 %.1fMB + 全文 %.1fMB / %d 分片'
                  % (lang['full'], r['n'], r['brief'] / 1024 / 1024,
                     r['full'] / 1024 / 1024, r['shards']))

    print('构建完成')


if __name__ == '__main__':
    sys.exit(main() or 0)
