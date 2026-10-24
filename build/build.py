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
        # 英文不是转出来的，是人工翻译：正文放 content/en/posts/，
        # frontmatter 里用 src 指回中文原文（hreflang 靠它配对）。
        'posts_sub': ('en', 'posts'),
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


_EN_PAIR = None


def _en_pairs():
    """中文 slug -> 英文 slug 对照表。

    英文文章的 frontmatter 里 `src` 写中文原文件名（含 `-370` 这种旧编号），
    这里把它剥成 slug。表只建一次，构建全程复用。
    """
    global _EN_PAIR
    if _EN_PAIR is not None:
        return _EN_PAIR
    m = {}
    d = os.path.join(CONTENT, 'en', 'posts')
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.md'):
                continue
            fm, _ = parse_frontmatter(read_text(os.path.join(d, fn)))
            src = str(fm.get('src') or '').strip()
            if not src:
                continue
            m[os.path.splitext(src)[0]] = os.path.splitext(fn)[0]
    _EN_PAIR = m
    return m


def _post_slugs(lang, post):
    """给定当前语言的文章，返回 {语言code: slug} —— 没有译文的语言不出现在结果里。"""
    zh_slug = post['slug']
    if lang.get('code') == 'en':
        zh_slug = os.path.splitext(post.get('src') or '')[0]
        if not zh_slug:
            # 英文原创稿（没有中文原文）：只有英文版
            return {'en': post['slug']}
    out = {'zh-Hans': zh_slug, 'zh-Hant': zh_slug}
    en = _en_pairs().get(zh_slug)
    if en:
        out['en'] = en
    return out


def build_switcher(cur, rel='', post=None, fallback_home=False):
    """三语切换器。cur 是当前语言对象，rel 是当前页在该语言下的相对路径。

    post 不为空时按译文实际存在与否给链接：没有英文译文的文章，
    点 EN 回到英文首页（可到达），而不是给一个 404。

    fallback_home=True 用于**只有一种语言存在**的独立页（如英文术语表）：
    其他语言的链接一律回到该语言的首页，免得指向不存在的同路径页面。
    """
    cc = get_opencc(cur)
    lbl = cc.convert('语言切换') if cc else ('Language' if cur.get('code') == 'en' else '语言切换')
    slugs = _post_slugs(cur, post) if post else None
    out = ['<nav class="langsw" aria-label="%s">' % lbl]
    for l in LANGS:
        if l['code'] == cur['code']:
            cls, mark, href, cur_attr = 'on', '<span class="cur"> · </span>', '#', ' aria-current="true"'
        elif slugs is not None:
            s = slugs.get(l['code'])
            if s:
                href = url_for(l, 'posts/' + urllib.parse.quote(s + '.html', safe='/-.'))
            else:
                href = url_for(l, 'index.html')
            cls, mark, cur_attr = '', '', ''
        elif l.get('stub') and rel.replace('index.html', ''):
            # 英文文章页还不存在，点过去会到英文说明页 —— 仍可达，保留链接
            cls, mark, href, cur_attr = '', '', url_for(l, 'index.html'), ''
        elif fallback_home:
            # 该页面只有当前语言有，别的语言回首页
            cls, mark, href, cur_attr = '', '', url_for(l, 'index.html'), ''
        else:
            cls, mark, href, cur_attr = '', '', url_for(l, rel), ''
        out.append('<a class="%s" href="%s" hreflang="%s"%s title="%s">%s%s</a>'
                   % (cls, esc(href), l['code'], cur_attr, l['full'], l['label'], mark))
    out.append('</nav>')
    return ''.join(out)


def hreflang_tags(lang, rel='index.html', post=None):
    """三语互指 + x-default。

    索引页/关于页：三种语言同路径，直接用 rel。
    文章页：slug 各语言不同 —— 中文与繁体同 slug（繁体是构建时转的），
    英文 slug 是独立的，所以要用 frontmatter 的 src 建对照表。
    **没有译文的语言不进 hreflang**，否则等于让搜索引擎去抓 404。
    """
    tags = []
    if post is None:
        base = rel.replace('index.html', '')
        for l in LANGS:
            href = url_for(l, base)
            tags.append('<link rel="alternate" hreflang="%s" href="%s">' % (l['code'], esc(href)))
        tags.append('<link rel="alternate" hreflang="x-default" href="%s">'
                    % esc(url_for(LANGS[0], base)))
        return '\n'.join(tags)

    slugs = _post_slugs(lang, post)
    for l in LANGS:
        s = slugs.get(l['code'])
        if not s:
            continue
        href = url_for(l, 'posts/' + urllib.parse.quote(s + '.html', safe='/-.'))
        tags.append('<link rel="alternate" hreflang="%s" href="%s">' % (l['code'], esc(href)))
    zh = slugs.get('zh-Hans')
    if zh:
        href = url_for(LANGS[0], 'posts/' + urllib.parse.quote(zh + '.html', safe='/-.'))
        tags.append('<link rel="alternate" hreflang="x-default" href="%s">' % esc(href))
    return '\n'.join(tags)


# ---------------------------------------------------------------- 界面文案
# 界面固定文案（导航、按钮、页脚免责声明等）。繁体版用 OpenCC 现场转，
# 英文版暂时只有少量文案（stub 语言不出文章页，用不到多少）。
UI_ZH = {
    'h1': '时空人公益推广',
    'subtitle': '吕文艺老师作品归档',
    # 不再重复统计里的数字（690 篇 / 2017–2026 都在下面的数据栏里），
    # 眉题也去掉了：原来「吕氏时空人 · 公益推广」和 H1 说的是同一件事。
    'lede': '吕文艺老师「吕氏时空人」理论技术作品的整理与检索。公众号「时空坐标」九年原创文章，按主题归档，可全文检索。',
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
    # 首页页脚已压成两段：cp 是免责+来源版权合一句，联系只剩一行。
    # disclaimer_v 只剩「免责声明」框还在用（首页框已删），保留 key 以防其他页引用。
    'disclaimer_v': '本站为传统文化交流性质的内容归档，文章观点仅供学习参考，不构成任何建议。',
    'copyright': '本站为传统文化交流性质的内容归档，观点仅供学习参考，不构成任何建议；内容整理自吕文艺公众号「时空坐标」，版权归原作者所有，引用请注明出处。',
    'wechat': '微信公众号：',
    'tel': '联系原作者：吕文艺老师',
    'about_site': '关于本站',
    'sitemap': '网站地图',
    'cms': '内容管理',
    'copy': '© 时空人公益推广 · shikongren.com',
    'copy_n': '© 时空人公益推广 · shikongren.com · %d 篇原创文章',
    'site_name': '时空人公益推广',
    'wechat_name': '时空坐标',
    'author_name': '吕文艺',
    'account_alt': '吕文艺老师',
    'lang_label': '语言切换',
    'orig_link': '原文链接',
    'cat_crumb': '分类',
    'meta_desc': '吕文艺老师「吕氏时空人」理论技术作品归档与检索站，收录原创文章 %d 篇。',
}


# 英文界面文案。英文版不是 OpenCC 转的，是独立撰写 —— 这些key必须与 UI_ZH 一一对应，
# 缺一个就会在页面上留一个空的 {} 位置，所以下面用 assert 卡住。
UI_EN = {
    'h1': 'Shikongren Archive',
    'subtitle': "Lü Wenyi's collected works",
    'lede': ('A searchable archive of Lü Wenyi’s essays on the Lü School of Shikongren — original '
             'articles from the WeChat account “Shikong Coordinate”, filed by topic.'),
    'ph': 'Search articles — e.g. Bazi, feng shui, direction…',
    'clear': 'Clear',
    'stat_n': 'articles',
    'stat_first': 'from',
    'stat_last': 'to',
    'stat_words': 'k words',
    'about_link': 'About this archive →',
    'about_bio': ('Founder of the Lü School of Shikongren (Lüshi Shikongren), a system that treats '
                  'Chinese metaphysics as a natural science.'),
    'all': 'All',
    'nomatch': 'Nothing matches. Try a different keyword.',
    'home': 'Home',
    'back': '← Back to the archive',
    'back_home': '← Back to the index',
    'words_unit': 'words',
    'author': 'by',
    'source': 'Source',
    'source_v': 'WeChat public account “Shikongren Coordinates” (时空坐标) · by Lü Wenyi',
    'stmt': 'Notice',
    'stmt_v': ('This is a study archive. Copyright remains with the author. '
               'Nothing here constitutes advice.'),
    'related': 'Related articles',
    'disclaimer': 'Disclaimer',
    'disclaimer_v': ('This site archives material for the study of traditional Chinese culture. '
                     'The views expressed are those of the author and constitute no advice.'),
    'copyright': ('This archive is for study and reference only and constitutes no advice. Content '
                  'is from Lü Wenyi’s WeChat account “Shikongren Coordinates” (时空坐标); copyright '
                  'remains with the author — cite the source when quoting.'),
    'wechat': 'WeChat: ',
    'tel': 'Contact the author, Lü Wenyi: ',
    'about_site': 'About this archive',
    'sitemap': 'Sitemap',
    'cms': 'Content admin',
    'copy': '© Shikongren Archive · shikongren.com',
    'copy_n': '© Shikongren Archive · shikongren.com · %d original articles',
    'site_name': 'Shikongren Archive',
    'wechat_name': '时空坐标 (Shikongren Coordinates)',
    'author_name': 'Lü Wenyi',
    'account_alt': 'Lü Wenyi',
    'lang_label': 'Language',
    'orig_link': 'Chinese original',
    'cat_crumb': 'Topic',
    'meta_desc': ('An archive and search index of %d original articles by Lü Wenyi on the Lü School '
                  'of Shikongren theory — Bazi, feng shui, Liuyao and the classical foundations.'),
}

_missing = set(UI_ZH) - set(UI_EN)
assert not _missing, 'UI_EN 缺文案：%s' % ', '.join(sorted(_missing))


def ui(lang):
    """界面文案 —— 繁体由简体文案经 OpenCC 转；英文用独立撰写的 UI_EN。

    两种语言共用同一套 key，模板里只写 {key}，所以这里必须返回**完整**的字典。
    """
    if lang.get('code') == 'en':
        return dict(UI_EN)
    cc = get_opencc(lang)
    if not cc:
        return dict(UI_ZH)
    out = {}
    for k, v in UI_ZH.items():
        out[k] = cc.convert(v) if '%' not in v else v
    # 带占位符的文案单独处理（%d 要保留）
    for k in ('lede', 'copy_n', 'meta_desc'):
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

# 前端（app.js）用的文案。搜索计数是 JS 现场拼的，模板管不到，
# 所以构建时把当前语言的一份塞进 window.I18N，app.js 优先用它、没有就退回中文。
I18N_ZH = {
    'count_all': '共收录 %d 篇',
    'count_search': '搜索「%s」：命中 %d 篇',
    'count_full': '（全文）',
    'count_sum': '（标题与摘要）',
    'count_summary': '（摘要）',
    'count_loading': '搜索「%s」：摘要命中 %d 篇，正在补全全文…',
    'len_k': '%d 千字',
    'len_c': '%d 字',
    'more_btn': '再看 %d 篇',
    'more_rest': '还有 %d 篇未展开',
    'more_end': '已经到底了',
}

I18N_EN = {
    'count_all': '%d articles',
    'count_all_one': '%d article',
    'count_search': 'Search “%s”: %d results',
    'count_search_one': 'Search “%s”: %d result',
    'count_loading_one': 'Search “%s”: %d result in summaries, loading full text…',
    'count_full': ' (full text)',
    'count_sum': ' (title and summary)',
    'count_summary': ' (summary)',
    'count_loading': 'Search “%s”: %d results in summaries, loading full text…',
    'len_k': '%dk chars',
    'len_c': '%d chars',
    'more_btn': 'Show %d more',
    'more_rest': '%d more not shown',
    'more_end': 'That’s everything',
}


def i18n(lang):
    """前端文案。繁体走 OpenCC（这些串含 % 但不能不转，否则 JS 里蹦简体）。"""
    if lang.get('code') == 'en':
        return dict(I18N_EN)
    cc = get_opencc(lang)
    if not cc:
        return dict(I18N_ZH)
    return {k: cc.convert(v) for k, v in I18N_ZH.items()}


# 英文分类名。key 仍是简体原名 —— cat_key 从不翻译，只在显示时查这张表。
CATS_EN = {
    '预测案例': ('Divination Cases', 'Live readings, field records and the method behind them'),
    '预测理论': ('Prediction Theory', 'What prediction can and cannot claim'),
    '风水实务': ('Feng Shui Practice', 'Sand and water, the origin point, graves, direction'),
    '八字命理': ('Bazi and Four Pillars', 'The four pillars, the ten gods and the three systems'),
    '易学基础': ('Foundations of Yixue', 'Trigrams, yin-yang, the five phases, Qimen'),
    '化解调理': ('Remediation', 'What can be done about a fate, and where the claim stops'),
    '命运格局': ('Fate and Life Pattern', 'Levels of fate and the shape of a life'),
    '面相手相': ('Physiognomy', 'Face and palm reading'),
    '择日用事': ('Date Selection', 'Choosing days and hours'),
    '姓名测字': ('Names and Characters', 'Personal names and character analysis'),
    '行业辨伪': ('Industry Critique', 'Exposing the trade’s tricks'),
    '学员心得': ('Student Notes', 'Reflections from students'),
    '培训招生': ('Courses', 'Past courses and enrolment'),
    '综合论述': ('Essays', 'Other writing'),
}


# ---------------------------------------------------------------- 工具

def _plural(n, word):
    """英文单复数：1 topic / 3 topics。只处理规则复数，够用。"""
    if n == 1:
        return '1 %s' % word
    if word.endswith('y'):
        return '%d %sies' % (n, word[:-1])
    return '%d %ss' % (n, word)


def ebooks_link(lang, rel=''):
    """页脚的电子书入口。三语都有这一页，所以不需要条件判断内容是否存在。"""
    code = lang.get('code')
    if code == 'en':
        lbl = 'Ebooks'
    else:
        lbl = '電子書' if lang.get('opencc') else '电子书'
    return '<a href="%sebooks.html">%s</a> · ' % (rel, lbl)


def ebook_chip(lang):
    """分类条里的电子书入口。

    原先放在 hero 里，手机上等于白白多吃掉四十多像素；挪到分类条这一行，
    既跟「找内容」的动线在一起，也不再撑高头部。
    """
    code = lang.get('code')
    if code == 'en':
        head, tail = 'Free ebooks', ' ↓'
    elif lang.get('opencc'):
        head, tail = '免費下載電子書', ' ↓'
    else:
        head, tail = '免费下载电子书', ' ↓'
    return ('<a class="dl" href="ebooks.html"><span class="dl-h">%s</span>'
            '<span class="dl-t">%s</span></a>' % (head, tail))


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
    # 英文译文正文以译者注（markdown 引用块）开头。卡片摘要、meta description、
    # 搜索索引的摘要都走这里，不剥掉的话读者看到的第一句永远是
    # "Translator's note. ..."。已核查中文 690 篇没有以引用开头的，剥离是安全的。
    body = re.sub(r'^\s*(?:>[^\n]*\n)+', '', body or '')
    body = re.sub(r'^\s*(?:-{3,}|\*{3,}|_{3,})[ \t]*\n', '', body)
    body = body.lstrip('\n')
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', body)
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)
    t = re.sub(r'https?://\S+', ' ', t)
    t = re.sub(r'&(?:quot|amp|lt|gt|#39|nbsp);', ' ', t)
    # 强调符直接删掉而不是换成空格 —— 否则 "*zhenxing*," 会变成 "zhenxing ,"。
    t = re.sub(r'[*`~]', '', t)
    t = re.sub(r'[#>()\[\]{}|]', ' ', t)
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


# ---- 术语表反向内链 ----------------------------------------------------
# 规则集中放在 content/en/glossary-links.json：
#   patterns = 术语 id -> 正文里可能的英文词形
#   links    = 文章 slug -> 这篇文章真正在讲的术语 id
# 放在 HTML 层注入而不是改 111 个源文件：规则改了只动一张表，
# 正文 markdown 保持干净，中文站一行都不用碰。
_GLOSS_CACHE = None


def _gloss_rules():
    global _GLOSS_CACHE
    if _GLOSS_CACHE is not None:
        return _GLOSS_CACHE
    pats, links = {}, {}
    path = os.path.join(CONTENT, 'en', 'glossary-links.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        for tid, p in (data.get('patterns') or {}).items():
            pats[tid] = re.compile(r'(?<![A-Za-z])(?:%s)(?![A-Za-z])' % p, re.I)
        links = data.get('links') or {}
    except (OSError, ValueError) as e:
        print('  ! glossary-links: %s' % e)
    _GLOSS_CACHE = (pats, links)
    return _GLOSS_CACHE


def with_glossary(html, slug):
    """给英文文章正文首次出现的术语挂上术语表锚点。

    只处理 <p> 和 <li>：<h1>-<h3> 里的词是标题，链上去读着累；
    <blockquote> 里是译者注和成段引文，不该塞链接。每篇每术语最多一次，
    段落里已有链接就整段跳过，避免 a 套 a。
    """
    pats, links = _gloss_rules()
    wanted = links.get(slug) or []
    if not wanted:
        return html
    used = set()

    def para(m):
        tag, inner = m.group(1), m.group(2)
        if '<a ' in inner:
            return m.group(0)
        cands = []
        for tid in wanted:
            if tid in used or tid not in pats:
                continue
            mm = pats[tid].search(inner)
            if mm:
                cands.append((mm.start(), mm.end(), tid, mm.group(0)))
        if not cands:
            return m.group(0)
        cands.sort()
        out, pos = [], 0
        for s, e, tid, txt in cands:
            if s < pos:          # 与上一条重叠，这条放弃，等下个段落
                continue
            out.append(inner[pos:s])
            out.append('<a class="gl" href="../glossary.html#%s">%s</a>' % (tid, txt))
            pos = e
            used.add(tid)
        out.append(inner[pos:])
        return '<%s>%s</%s>' % (tag, ''.join(out), tag)

    return re.sub(r'<(p|li)>(.*?)</\1>', para, html, flags=re.S)


def slug_of(text, fallback):
    """从文件名/标题生成 URL 安全 slug"""
    s = re.sub(r'[^\w\-.一-鿿]', '', str(text or ''))
    s = s.strip('-')
    return s or fallback


# ---------------------------------------------------------------- 读取内容

def load_articles(lang=None):
    """读全部 .md 文章。传入 lang 时返回繁体版（OpenCC 转换标题/分类/标签/正文）。

    繁体转换在**内存里**做，不写临时文件 —— 源码仓库里永远只有简体一份。
    英文不走转换：正文是人工翻译，单独放在 content/en/posts/（lang['posts_sub']）。
    """
    lang = lang or LANGS[0]
    cc = get_opencc(lang)
    sub = lang.get('posts_sub')
    posts_dir = os.path.join(CONTENT, *sub) if sub else POSTS_DIR
    arts = []
    if not os.path.isdir(posts_dir):
        return arts
    for fn in sorted(os.listdir(posts_dir)):
        if not fn.endswith('.md'):
            continue
        fm, body = parse_frontmatter(read_text(os.path.join(posts_dir, fn)))
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
            # 英文译文的 frontmatter 用 src 指回中文原文件名（如 '五行相生的顺序…-614.md'）。
            # 英文篇没有微信链接，靠它生成「Chinese original」指向中文对应页。
            'src': (fm.get('src') or '').strip(),
            'account': '时空坐标',
            'series': series,
            # 系列内阅读顺序。英文精选集要能顺着读，但原文日期和阅读顺序
            # 并不同步（来源篇写于 2020，总论写于 2021，却应该排在总论之后），
            # 所以给个显式顺序号；没写的排到该系列末尾，仍按日期。
            'order': int(fm.get('order') or 999),
            # cat_key 永远是**简体原名** —— 统计、分类导航、CATS 查表都用它，
            # 与语言无关；category 是显示名（繁体版里是繁体）。
            # 早前版本只存 category，繁体下 '预测案例'→'預測案例' 查不到 CATS，
            # 导致分类导航和「覆盖 N 个主题」全部失效。
            # 英文文章的 category 是英文，所以必须在 frontmatter 里显式写 cat_key。
            'cat_key': fm.get('cat_key') or cat0,
            'category': cat,
            'tags': tags,
            'words': int(words),
            'body': body.strip(),
            # 英文文章指回中文原文文件名（不含 .md），hreflang 配对靠它；
            # 中文文章没有这个字段。
            'src': str(fm.get('src') or ''),
        })
    # 时间靠后靠前；同日按标题排
    arts.sort(key=lambda a: (a['date'] or '', a['title']), reverse=True)
    if lang.get('code') == 'en':
        # 英文是精选集不是全量归档：同一系列要能顺着读（一→二→三），
        # 所以先按系列归堆、再按日期正序，而不是像中文那样倒序压在最前。
        arts.sort(key=lambda a: (a.get('series') or '~', a.get('order') or 999, a['date'] or ''))
    return arts


def cat_labels(lang):
    """分类显示名 —— 繁体经 OpenCC 转；英文查 CATS_EN（缺的退回简体原名）。"""
    if lang.get('code') == 'en':
        out = collections.OrderedDict()
        for k, v in CATS.items():
            out[k] = CATS_EN.get(k, v)
        return out
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


def load_en_about():
    """英文关于页（content/en/about.md）。没有就返回 None，由调用方退回中文版。"""
    p = os.path.join(CONTENT, 'en', 'about.md')
    if not os.path.isfile(p):
        return None
    fm, body = parse_frontmatter(read_text(p))
    return {'frontmatter': fm, 'body': body.strip()}


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


def cover_image(body, lang, fallback='/img/uploads/luwenyi-avatar.jpg'):
    """取正文第一张图片的**绝对 URL**，用作 og:image。

    og:image 必须是绝对地址（Google / 微信 / Facebook 都不会去解析相对路径）。

    正文源码是 **markdown**，图片写成 `![alt](/img/xxx.jpg)`；
    渲染后的 HTML 则是 `<img src="../img/xxx.jpg">`。
    两种写法都要认 —— 优先 markdown 源码，其次渲染后的 HTML。
    没有图就退回作者头像。
    """
    src = None
    m = re.search(r'!\[[^\]]*\]\(([^)\s]+)', body or '')
    if m:
        src = m.group(1)
    if not src:
        m = re.search(r'<img[^>]+src="([^"]+)"', body or '')
        if m:
            src = m.group(1)
    if not src:
        return DOMAIN + fallback
    if src.startswith('http://') or src.startswith('https://'):
        return src
    if src.startswith('/'):
        return DOMAIN + src
    # 相对路径（../img/xxx），去掉 ../ 前缀后按站点根拼
    rel = re.sub(r'^(\.\./)+', '', src)
    return DOMAIN + '/' + rel



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
    is_en = lang.get('code') == 'en'
    L = cat_labels(lang)
    pfx = lang_prefix(lang)
    cards = []
    for a in arts:
        ex = clean_excerpt(a['body'], 78)
        cards.append(
            '<a class="card" href="posts/%s.html">'
            '<div class="c">%s</div>'
            '<h3>%s</h3>'
            '<p class="ex">%s</p>'
            '<div class="m"><time>%s</time><span>%d %s</span></div>'
            '</a>' % (esc(a['slug']), esc(a['category']), esc(a['title']),
                      esc(ex), esc(a['date']), a['words'], T['words_unit']))

    # 分类条：英文站指向六张落地页（有独立 URL，能被收录）；
    # 中文/繁体没有落地页，仍然是首页内的锚点筛选。
    if is_en:
        _sec_slug = {s['key']: s['slug'] for s in SECTIONS}
        nav = ''.join(
            ('<a href="%s.html">%s <em>%d</em></a>'
             % (_sec_slug[c], esc(cat_display(lang, c)), cats.get(c, 0)))
            if c in _sec_slug else
            ('<a href="#cat-%s">%s <em>%d</em></a>'
             % (c, esc(cat_display(lang, c)), cats.get(c, 0)))
            for c in cats if cats.get(c, 0))
    else:
        nav = ''.join('<a href="#cat-%s">%s <em>%d</em></a>' % (c, esc(cat_display(lang, c)), cats.get(c, 0))
                      for c in cats if cats.get(c, 0))

    total_words = sum(a['words'] for a in arts)
    n_cat = len([c for c in CATS if cats.get(c, 0)])

    return INDEX_TPL.format(
        hlang=lang['html_lang'],
        title='%s · %s' % (T['h1'], T['subtitle']),
        desc=T['meta_desc'] % len(arts),
        stamp=STAMP,
        canonical=url_for(lang, 'index.html'),
        og=lang['og'],
        site=T['site_name'],
        alt=hreflang_tags(lang, 'index.html'),
        verify=VERIFY_BLOCK,
        h1=T['h1'],
        lede=T['lede'],
        s_n=len(arts), s_nu=T['stat_n'],
        # 中文是「2021 最早」，英文得写成「from 2021」—— 语序相反，
        # 模板只有一个 <b>{值}</b> {单位} 的位置，所以英文把介词并进值里。
        s_first=('from %s' % min(years)) if (is_en and years) else (min(years) if years else ''),
        s_firstu='' if is_en else T['stat_first'],
        s_last=('to %s' % max(years)) if (is_en and years) else (max(years) if years else ''),
        s_lastu='' if is_en else T['stat_last'],
        # 中文按万字折算；英文只有几千词，折算出来是 0，直接给准数。
        s_words=format(total_words, ',') if is_en else total_words // 10000,
        s_wordsu=('words' if total_words != 1 else 'word') if is_en else T['stat_words'],
        ph=T['ph'], clear=T['clear'],
        bio=T['about_bio'], aboutlink=T['about_link'],
        author_name=T['author_name'], author_alt=T['account_alt'],
        wechat_name=T['wechat_name'],
        langsw=build_switcher(lang, 'index.html'),
        allu=T['all'], nav=nav, cards=''.join(cards), nomatch=T['nomatch'],
        # 手机上分类条默认只占一行，「更多」展开其余；桌面端这个按钮不显示
        more_u='More ▾' if is_en else '更多 ▾',
        less_u='Less ▴' if is_en else '收起 ▴',
        disc_h=T['disclaimer'], disc=T['disclaimer_v'],
        cp=T['copyright'],
        wechat=T['wechat'], tel=T['tel'],
        about_site=T['about_site'], sitemap=T['sitemap'], cms=T['cms'],
        # 术语表只有英文版。中文页给空串，页脚保持原样。
        gloss_link=('<a href="glossary.html">Glossary</a> · ' if is_en else '')
                   + ebooks_link(lang),
        ebookchip=ebook_chip(lang),
        copy=T['copy'],
        pfx=pfx,
        i18n=json.dumps(i18n(lang), ensure_ascii=False),
        ld=ld_home(lang, url_for(lang, 'index.html'),
                   '%s · %s' % (T['h1'], T['subtitle']),
                   T['meta_desc'] % len(arts),
                   len(arts)),
        cover=DOMAIN + '/img/uploads/luwenyi-avatar.jpg')


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
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{cover}">
<meta property="og:image" content="{cover}">
<meta property="og:image:alt" content="{title}">
<meta name="baidu-site-verification" content="codeva-m8N0GhhwKD" />
<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
{verify}
<script type="application/ld+json">{ld}</script>
</head>
<body>
<header class="hero">
  <div class="wrap">
    <div class="herohead">
      <h1>{h1}</h1>
      {langsw}
    </div>
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
      <img src="/img/uploads/luwenyi-avatar.jpg" alt="{author_alt}" width="34" height="34" loading="lazy" />
      <p><b>{author_name}</b>　{bio}
        <a href="about.html">{aboutlink}</a></p>
    </div>
  </div>
</header>

<div class="catbar">
  <nav class="cats wrap" id="cats">
    <a href="#" class="all">{allu}</a>{ebookchip}{nav}
  </nav>
  <button type="button" class="catsmore" id="catsmore" aria-expanded="false" aria-controls="cats"
          data-more="{more_u}" data-less="{less_u}"><span>{more_u}</span></button>
</div>

<main class="wrap">
  <div id="grid" class="grid">{cards}</div>
  <p id="empty" class="empty" hidden>{nomatch}</p>
  <!-- 首页一次不铺 690 张：先出 PAGE 张，按钮每次再放 STEP 张。
       卡片仍在 HTML 里（SEO 抓得到），只是超出部分先 display:none。 -->
  <div id="loadmore" class="loadmore" hidden>
    <button id="loadmore-btn" type="button"></button>
    <span id="loadmore-note"></span>
  </div>
</main>

<footer class="wrap foot">
  <div class="src">
    <p>{cp}</p>
    <p>{wechat}<b>{wechat_name}</b> · {tel} <a class="tel" href="tel:18601944999">18601944999</a></p>
  </div>
  <p class="footlinks">
    <a href="about.html">{about_site}</a> ·
    {gloss_link}<a href="{pfx}sitemap.xml">{sitemap}</a> ·
    <a href="/admin/">{cms}</a>
  </p>
  <p class="copy">{copy}</p>
</footer>

<script>window.I18N={i18n};</script>
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
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{cover}">
<meta property="og:image" content="{cover}">
<meta property="og:image:alt" content="{title}">
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
  <a href="{home_pfx}index.html" class="home">{back}</a>
  {langsw}
</div></header>

<article class="wrap post">
  <nav class="crumb"><a href="{home_pfx}index.html">{home}</a> / <span>{cat}</span></nav>
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
  <p><a href="{home_pfx}about.html">{about_site}</a> · {gloss_link}<a href="{home_pfx}index.html">{back_home}</a></p>
  <p class="copy">{copy}</p>
</footer>
</body>
</html>'''


def build_post_html(a, by_cat, lang):
    T = ui(lang)
    L = cat_labels(lang)
    # 详情页在 <lang>/posts/ 下，比索引页深一层，站内相对链接要多上一级 ../
    # pfx 给静态资源用（style.css / app.js 在站点根）；
    # home_pfx 给语言首页用 —— 文章永远在 <lang>/posts/ 下一层，所以固定 '../'。
    # 之前二者混用，繁体/英文文章页的「返回首页」会跳到简体首页。
    pfx = '../../' if lang['dir'] else '../'
    home_pfx = '../'
    desc = clean_excerpt(a['body'], 100)
    tg = ''
    if a['tags']:
        tg = '<div class="tags">' + ''.join(
            '<a href="%sindex.html#t-%s">#%s</a>' % (home_pfx, esc(t), esc(t))
            for t in a['tags']) + '</div>'
    # 同类文章按 cat_key（简体原名）取 —— 不能用 a['category']：
    # 繁体版的 category 是繁体、英文版是英文，都查不到 by_cat 的键，
    # 结果就是繁体/英文页面永远没有「同类文章」。
    rel = [x for x in by_cat.get(a['cat_key'], []) if x['slug'] != a['slug']][:6]
    rh = ''
    if rel:
        rh = ('<div class="related"><h4>%s</h4><ul>' % T['related'] +
              ''.join('<li><a href="%s.html">%s</a><time>%s</time></li>'
                      % (x['slug'], esc(x['title']), x['date']) for x in rel) +
              '</ul></div>')
    if a['url']:
        url_line = ('<p><b>%s</b>：<a href="%s" target="_blank" rel="noopener">%s</a></p>'
                    % (T['orig_link'], esc(a['url']), esc(a['url'])))
    elif a.get('src'):
        # 英文译文：没有微信链接，但能指回中文原文。
        # 不加这个分支时，这里会退化成 source_v，和上面那行 Source 完全一样。
        _z = a['src'][:-3] if a['src'].endswith('.md') else a['src']
        _zh = url_for(LANGS[0], 'posts/' + urllib.parse.quote(_z + '.html', safe='/-.'))
        # 文件名尾部是原文序号（-614），读者不需要看见
        _zt = re.sub(r'-\d+$', '', _z)
        url_line = ('<p><b>%s</b>：<a href="%s">%s</a></p>'
                    % (T['orig_link'], esc(_zh), esc(_zt)))
    else:
        url_line = '<p><b>%s</b>：%s</p>' % (T['orig_link'], T['source_v'].split('｜')[0])
    slugq = urllib.parse.quote(a['slug'] + '.html', safe='/-.')
    # 英文正文挂术语表锚点；简繁两站不碰
    _content = md_to_html(a['body'], img_prefix=pfx)
    if lang.get('code') == 'en':
        _content = with_glossary(_content, a['slug'])
    return POST_TPL.format(
        hlang=lang['html_lang'],
        title=esc(a['title']), desc=esc(desc), cat=esc(cat_display(lang, a['cat_key'])),
        date=esc(a['date']), words=a['words'], wu=T['words_unit'],
        author=esc(a['author']), author_l=T['author'],
        url_line=url_line, tags=tg, site=T['site_name'], og=lang['og'],
        canonical=url_for(lang, 'posts/' + slugq),
        alt=hreflang_tags(lang, 'posts/' + slugq, post=a),
        langsw=build_switcher(lang, 'posts/' + slugq, post=a),
        back=T['back'], home=T['home'], pfx=pfx, home_pfx=home_pfx,
        source=T['source'], source_v=T['source_v'], stmt=T['stmt'], stmt_v=T['stmt_v'],
        # 正文图片源是 /img/xxx，渲染成相对路径要按当前页面深度算：
        #   /posts/x.html     → ../img/
        #   /tc/posts/x.html  → ../../img/
        #   /en/posts/x.html  → ../../img/
        # 之前一律用 '../'，繁体靠 `cp -al img tc/img` 的硬链接兜着才没破图，
        # 英文没有这层兜底就 404 了。pfx 正好就是这个深度，直接复用。
        content=_content, related=rh, stamp=STAMP,
        verify=VERIFY_BLOCK, tel=T['tel'], wechat_v=T['source_v'].split('｜')[0].strip(),
        about_site=T['about_site'], back_home=T['back_home'], copy=T['copy'],
        gloss_link=('<a href="../glossary.html">Glossary</a> · '
                    if lang.get('code') == 'en' else '') + ebooks_link(lang, '../'),
        ld=ld_post(a, url_for(lang, 'posts/' + urllib.parse.quote(a['slug'] + '.html', safe='/-.')),
                   a['title'], desc, lang),
        pubdate=(a['date'] or '2026-01-01'),
        category=cat_display(lang, a['cat_key'], a['category']),
        cover=cover_image(a['body'], lang))


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
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{cover}">
<meta property="og:image" content="{cover}">
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
  <p>{gloss_link}<a href="{pfx}index.html">{back_home}</a> · <a href="{pfx}sitemap.xml">{sitemap}</a></p>
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
        gloss_link=('<a href="glossary.html">Glossary</a> · '
                    if lang.get('code') == 'en' else '') + ebooks_link(lang),
        copy=T['copy_n'] % len(arts),
        ld=ld_page(url_for(lang, 'about.html'), title, desc, lang),
        cover=DOMAIN + '/img/uploads/luwenyi-avatar.jpg')


# ---------------------------------------------------------------- 术语表
# 内容放在 content/en/glossary.json：词条是数据，不该长在构建脚本里。
# 这个页面**只有英文版**，中文站没有对应页 —— 所以 hreflang 与切换器都要特判，
# 否则会生成 /glossary.html、/tc/glossary.html 两条 404。

GLOSSARY_TPL = '''<!doctype html>
<html lang="{hlang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{doc_title} · {site}</title>
<meta name="description" content="{desc}">
<meta name="author" content="Lü Wenyi">
<meta property="og:title" content="{doc_title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="en_US">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{doc_title}">
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

<article class="wrap page glossary">
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


def _glossary_alt(lang):
    """术语表只有英文版：hreflang 只写 en 与 x-default。

    不能复用 hreflang_tags —— 它给三种语言各生成一条，
    而 /glossary.html 与 /tc/glossary.html 并不存在，等于让搜索引擎抓 404。
    """
    href = url_for(lang, 'glossary.html')
    return ('<link rel="alternate" hreflang="en" href="%s">\n'
            '<link rel="alternate" hreflang="x-default" href="%s">'
            % (esc(href), esc(href)))


# ---------------------------------------------------------------- 电子书下载页
# 四卷 PDF（中英各四卷）放在站点根 /ebooks/ 下，这一页只是入口。
# PDF 与封面缩略图不进 Git（合计 35MB），由 outputs/ebook 的生成流程产出后
# 直接放到服务器的 /www/wwwroot/shikongren.com/ebooks/，部署脚本不会清理该目录。
# 书目文案在 content/ebooks.json，页面只读数据，不碰任何 md。
EBOOKS_TPL = '''<!doctype html>
<html lang="{hlang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{doc_title} · {site}</title>
<meta name="description" content="{desc}">
<meta name="author" content="Lü Wenyi">
<meta property="og:title" content="{doc_title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="{locale}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{doc_title}">
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

<article class="wrap page ebooks">
  <h1>{h1}</h1>
  <p class="pagesub">{sub}</p>
  <div class="content">
{intro}
  </div>
  <div class="shelves">
{books}
  </div>
  <div class="ebnote">
    <h2>{note_h}</h2>
{note}
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


def build_ebooks_html(lang):
    """中英各四卷 PDF 的下载页。三语都生成：繁体由简体文案经 OpenCC 转。"""
    path = os.path.join(CONTENT, 'ebooks.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    code = lang.get('code')
    key = 'en' if code == 'en' else 'zh'
    meta = data[key]
    T = ui(lang)
    cc = get_opencc(lang) if lang.get('opencc') else None

    def tr(s):
        return cc.convert(s) if cc else s

    pfx = lang_prefix(lang)
    dl_word = 'Download PDF' if code == 'en' else '下载 PDF'

    def card(b):
        url = '%sebooks/%s' % (pfx, b['file'])
        img = '%sebooks/%s' % (pfx, b['cover'])
        if b.get('n'):
            meta_line = ('%d essays · %d pages · %s' if code == 'en'
                         else '%d 篇 · %d 页 · %s') % (b['n'], b['pages'], b['size'])
        else:
            meta_line = ('%d pages · %s' if code == 'en'
                         else '%d 页 · %s') % (b['pages'], b['size'])
        return (
            '<div class="book">\n'
            '  <a class="bk-cover" href="%s" aria-label="%s"><img src="%s" alt="%s" loading="lazy" width="112" height="159"></a>\n'
            '  <div class="bk-body">\n'
            '    <h3>%s</h3>\n'
            '    <p class="bk-sub">%s</p>\n'
            '    <p class="bk-desc">%s</p>\n'
            '    <p class="bk-meta">%s</p>\n'
            '    <a class="bk-dl" href="%s" download>%s</a>\n'
            '  </div>\n'
            '</div>' % (
                esc(url), esc(tr(b['title'])), esc(img), esc(tr(b['title'])),
                esc(tr(b['title'])), esc(tr(b['sub'])), esc(tr(b['desc'])),
                esc(tr(meta_line)), esc(url), esc(dl_word)))

    shells = []
    for sh in data['shelves'][key]:
        bodies = '\n'.join(card(b) for b in sh['books'])
        shells.append(
            '<section class="shelf" id="%s">\n'
            '  <div class="shelf-hd"><h2>%s</h2><span class="shelf-n">%d</span></div>\n'
            '  <p class="shelf-note">%s</p>\n'
            '  <div class="booklist">\n%s\n  </div>\n'
            '</section>' % (
                esc(sh['id']), esc(tr(sh['title'])), len(sh['books']),
                esc(tr(sh['note'])), bodies))

    intro = '\n'.join('  <p class="ebintro">%s</p>' % esc(tr(p))
                      for p in meta['intro'])
    note = '\n'.join('    <p>%s</p>' % esc(tr(p)) for p in meta['note'])
    all_books = [b for sh in data['shelves'][key] for b in sh['books']]
    copy = ('Twelve books · free to download · %s' % DOMAIN if code == 'en'
            else '十二种 · 免费下载 · %s' % DOMAIN)

    return EBOOKS_TPL.format(
        hlang=lang['html_lang'], doc_title=esc(tr(meta['doc_title'])),
        site=T['site_name'], desc=esc(tr(meta.get('desc') or meta['sub'])),
        locale=('en_US' if code == 'en' else 'zh_CN'),
        canonical=url_for(lang, 'ebooks.html'),
        alt=hreflang_tags(lang, 'ebooks.html'),
        pfx=pfx, stamp=STAMP, verify=VERIFY_BLOCK,
        h1=esc(tr(meta['h1'])), sub=esc(tr(meta['sub'])),
        intro=intro, books='\n'.join(shells),
        note_h=esc(tr(meta['note_h'])), note=note,
        back=T['back'], langsw=build_switcher(lang, 'ebooks.html'),
        disc_h=T['disclaimer'], disc=T['stmt_v'],
        back_home=T['back_home'], sitemap=T['sitemap'], copy=esc(copy),
        ld=ld_page(url_for(lang, 'ebooks.html'), tr(meta['doc_title']),
                   tr(meta['sub']), lang))


def build_glossary_html(arts, lang):
    path = os.path.join(CONTENT, 'en', 'glossary.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    T = ui(lang)
    by_slug = {a['slug']: a for a in arts}

    parts = []
    for p in data['intro']:
        parts.append('<p>%s</p>' % p)

    parts.append('<nav class="glossnav" aria-label="Sections">%s</nav>'
                 % ''.join('<a href="#%s">%s</a>' % (esc(g['id']), esc(g['name']))
                           for g in data['groups']))

    missing = []
    n_terms = 0
    for g in data['groups']:
        parts.append('<h2 id="%s">%s</h2>' % (esc(g['id']), esc(g['name'])))
        if g.get('blurb'):
            parts.append('<p class="glossblurb">%s</p>' % g['blurb'])
        parts.append('<dl class="terms">')
        for t in g['terms']:
            n_terms += 1
            links = []
            for s in t.get('see') or []:
                a = by_slug.get(s)
                if not a:
                    missing.append('%s -> %s' % (t['id'], s))
                    continue
                links.append('<a href="posts/%s.html">%s</a>' % (esc(s), esc(a['title'])))
            parts.append('<div class="term" id="%s">' % esc(t['id']))
            parts.append('<dt><span class="ten">%s</span>'
                         '<span class="zh">%s</span>'
                         '<span class="py">%s</span></dt>'
                         % (t['en'], esc(t['zh']), esc(t['py'])))
            parts.append('<dd><p>%s</p>' % t['def'])
            if t.get('note'):
                parts.append('<p class="gnote">%s</p>' % t['note'])
            if links:
                parts.append('<p class="gsee"><b>Read more:</b> %s</p>' % ' · '.join(links))
            parts.append('</dd></div>')
        parts.append('</dl>')

    parts.append('<div class="glossnote">')
    parts.append('<h2>%s</h2>' % esc(data['note_h']))
    for p in data['note']:
        parts.append('<p>%s</p>' % p)
    parts.append('</div>')

    if missing:
        print('  ! glossary: %d dead link(s): %s' % (len(missing), ', '.join(missing)))

    h1 = data['title']
    doc_title = data.get('doc_title') or h1
    desc = ('%d terms from the Lü School of Shikongren, translated and explained: living qi, '
            'vein energy, the true dragon point, the day master and the useful god, plus the '
            'ordinary English words that mislead.' % n_terms)
    return GLOSSARY_TPL.format(
        hlang=lang['html_lang'], title=esc(h1), doc_title=esc(doc_title),
        subtitle=esc(data['subtitle']),
        desc=esc(desc), site=T['site_name'],
        canonical=url_for(lang, 'glossary.html'),
        alt=_glossary_alt(lang),
        langsw=build_switcher(lang, 'glossary.html', fallback_home=True),
        back=T['back'], pfx=lang_prefix(lang), content='\n'.join(parts), stamp=STAMP,
        verify=VERIFY_BLOCK, disc_h=T['disclaimer'], disc=T['stmt_v'],
        back_home=T['back_home'], sitemap=T['sitemap'],
        copy=T['copy_n'] % len(arts),
        ld=ld_page(url_for(lang, 'glossary.html'), doc_title, desc, lang))


# ---------------------------------------------------------------- 英文分类落地页

# 六层各做一张落地页。首页原本只有锚点筛选（#cat-xxx），全站 119 篇挤在
# 一个 URL 下，搜索引擎看不到「主题」这一层。这里给每层一个真实 URL。
#
# 每页的导语是原创撰写的，不是把卡片列表套个壳 —— 只有列表的聚合页
# 会被判成薄内容，所以每页都要回答「这一层在讲什么、怎么读、先读哪三篇」。
#
# 分组用 order 定位（series + order 是稳定排序）：
#   r=(起, 止) 按 order 区间取；o=[...] 显式列 order，用于跨区间的情况。
SECTIONS = [
    dict(
        slug='foundations', key='易学基础',
        name='Foundations of Yixue',
        sub='Trigrams, yin-yang, the five phases, Qimen',
        desc='The vocabulary the rest of the site depends on: where the ten stems '
             'and twelve branches come from, what the five phases actually name, '
             'and what the Yijing can and cannot predict.',
        intro=[
            'Everything else here rests on this section. Before a chart can be read or a '
            'site judged, the system needs a vocabulary — and this school\'s vocabulary is '
            'not the one the popular books use. The ten stems and twelve branches are not '
            'mystical symbols. They are counters derived from astronomical observation, and '
            'they mark time and direction before they mark anything else. The five phases '
            '(<i>wuxing</i>, 五行) are not five substances but five states of movement, which '
            'is why a translation like “five elements” keeps producing nonsense.',
            'Read in order, the first seven essays rebuild the stems and branches from their '
            'origin in sky observation. The next three take the five phases apart, including '
            'the question of whether the generating cycle is a theorem or something the '
            'tradition simply assumed. The middle block covers the two divination systems '
            'this school actually uses — Liuyao and Shenbing Qimen. The last essays are about '
            'the business of studying at all: what the school claims, what it refuses to '
            'claim, and why students of the <i>Yijing</i> keep getting cheated.',
            'If you only read three: <i>The Physical Nature of the Heavenly Stems and '
            'Earthly Branches</i>, <i>Is the Five-Element Sequence a Theorem or an Axiom?</i>, '
            'and <i>What Can Feng Shui and the Yijing Actually Predict?</i>',
        ],
        groups=[
            dict(t='The ten stems and twelve branches',
                 b='Where they came from, what they count, and why they are not symbols.',
                 r=(1, 7)),
            dict(t='The five phases',
                 b='Five states of movement — and the weak point in the generating cycle.',
                 r=(8, 10)),
            dict(t='Liuyao and Qimen',
                 b='The two divination systems used here, and what makes one of them real.',
                 r=(11, 20)),
            dict(t='Yin, yang and the taiji halo',
                 b='The geometry behind the classic diagram, read from the ground up.',
                 r=(21, 23)),
            dict(t='Studying the school',
                 b='What this school is, and what it costs a student to learn it.',
                 r=(24, 24)),
        ],
    ),
    dict(
        slug='practice', key='风水实务',
        name='Feng Shui Practice',
        sub='Sand and water, the origin point, graves, direction',
        desc='How a practitioner actually reads a site: seeking the dragon, setting the '
             'point, telling sand from water, and the cases where the classical method '
             'has to be stretched over modern ground.',
        intro=[
            'This is the largest section, and the one most readers come for. It is not a '
            'manual of rules to apply to your flat. It is a record of how a practitioner '
            'looks at a site: what gets measured, in what order, and what gets thrown out '
            'as noise.',
            'The sequence follows the classical procedure. First, what a master is even '
            'looking for — which is not “good energy” but a place where a vein of energy '
            'stops. Then seeking the dragon: tracing that vein through the landscape until '
            'it comes to rest. Then setting the point on the place where it rests. Then sand '
            'and water, which are not “mountains” and “rivers” but two functional '
            'categories — what holds energy and what moves it. Then orientation, which is a '
            'reading of the whole site rather than a compass stuck on a door.',
            'After that the section moves onto ground the classical method has to be '
            'stretched over: apartment blocks, public cemeteries, shops on a street, a site '
            'by the sea, a village built on water, a temple. Then families — graves, '
            'descendants, and what happens when relatives want the same feng shui. The final '
            'block is argument rather than instruction: a mountain cannot be pinned, a cut '
            'vein is not always a disaster, a missing corner does not damage your career, and '
            'feng shui cannot fix laziness.',
        ],
        groups=[
            dict(t='What a master is looking for', b='The question behind every site reading.', r=(1, 3)),
            dict(t='Seeking the dragon', b='Tracing a vein until it stops — and what a cut vein means.', r=(4, 8)),
            dict(t='Setting the point', b='Where the vein stops, how the point is fixed, and how it is done on flat land.', r=(9, 12)),
            dict(t='Sand and water', b='Two functional categories, not mountains and rivers.', r=(13, 16)),
            dict(t='Cities and buildings', b='The classical method applied to concrete: blocks, floors, a dragon point in an office.', r=(17, 20)),
            dict(t='Family, graves and descendants', b='Whether a grave reaches the living, and how far.', r=(21, 25)),
            dict(t='Reading a site', b='A classical text annotated, a sea coast, a row of shopfronts.', r=(26, 30)),
            dict(t='What feng shui cannot do', b='The limits, argued case by case.', r=(31, 36)),
            dict(t='Specific situations', b='Corners, the green dragon, temples, water country, cremation, and family disputes over a grave.', r=(37, 44)),
        ],
    ),
    dict(
        slug='bazi', key='八字命理',
        name='Bazi and Four Pillars',
        sub='The four pillars, the ten gods and the three systems',
        desc='The Four Pillars read as a model rather than a fortune: the four systems behind '
             'a birth chart, how the chart is counted, what the ten gods name, and why '
             'identical charts give different lives.',
        intro=[
            'Four Pillars (<i>bazi</i>, 八字) is a time system before it is anything else. A '
            'birth moment is written as four pairs of a stem and a branch — year, month, day, '
            'hour — eight characters in all, and every later judgement is derived from those '
            'eight. The reading is not a list of lucky and unlucky traits. It is a model of '
            'how one moment sits inside the larger cycles around it.',
            'These eighteen essays are about the model rather than about fortune-telling '
            'technique. The first block lays out the four systems a chart is built from — '
            'positioning, time, environment and counting. The second shows how the chart is '
            'actually counted, and what “strength” means when it is measured instead of '
            'guessed. The third takes the ten gods apart: what they name, how they are told '
            'apart, and where the standard correspondences break down. The last block is '
            'honest about failure — twins with the same chart, charts that give different '
            'fates, and a theory put to a test.',
            'If you only read three: <i>The Logic of Bazi Reading</i>, <i>What the Ten Gods '
            'Mean</i>, and <i>Why Identical Charts Give Different Fates</i>.',
        ],
        groups=[
            dict(t='The four systems', b='Positioning, time, environment, counting — the model assembled.', r=(1, 4)),
            dict(t='Counting the chart', b='Stems and branches counted, strength measured, the model put to use.', r=(5, 8)),
            dict(t='The ten gods', b='What they name, how they are told apart, and what is missing from the standard table.', r=(9, 13)),
            dict(t='Where the model breaks', b='Twins, identical charts, a theory tested, and why a correct reading gets rejected.', r=(14, 18)),
        ],
    ),
    dict(
        slug='cases', key='预测案例',
        name='Divination Cases',
        sub='Live readings, field records and the method behind them',
        desc='Readings recorded before the event rather than explained after it: a numbered '
             'case series, and one mountain site read in full.',
        intro=[
            'A prediction that is only explained after the event is not evidence of '
            'anything. This section is the archive\'s attempt to do the harder thing: record '
            'a reading before the outcome is known, with the reasoning visible, so that it '
            'can be checked later and — where it fails — be seen to fail.',
            'The numbered series follows one method across different people and problems. '
            'The entries are short because a reading is short: a chart, a question, a '
            'judgement. What makes them worth reading together is that the same steps recur, '
            'and you can watch the practitioner decide which system a question belongs to '
            'before answering it. Set against that, <i>Mount Wudang</i> is a single site read '
            'at length — the whole procedure on one landscape instead of one question.',
            'Start with case one for the method, then read the Wudang study to see it '
            'carried through without the compression.',
        ],
        groups=[
            dict(t='The case series', b='One method, many questions, recorded before the outcome.', o=[1, 2, 3, 4, 5, 6, 7, 9, 10]),
            dict(t='A single site, read in full', b='One landscape, the whole procedure, nothing shortened.', o=[8]),
        ],
    ),
    dict(
        slug='theory', key='预测理论',
        name='Prediction Theory',
        sub='What prediction can and cannot claim',
        desc='What a prediction actually asserts, why a fate can be calculated at all, where '
             'formulas go wrong, and why many people die at once.',
        intro=[
            'This section is not about technique. It is about what a prediction is allowed to '
            'claim once it has been made. If a chart can be read, what exactly has been read — '
            'a fixed outcome, a tendency, or a range of possible responses? The essays here '
            'take the uncomfortable questions in order rather than deferring them.',
            'The first three set out the ground: what use a reading has, why a fate can be '
            'predicted at all, and what the act of prediction actually consists of. The next '
            'four are about failure and scale — the problem with formulas, what injury, '
            'illness and disaster mean inside the model, how the principle of adaptation '
            'works, and why many people die at once when the model is supposed to be about '
            'individuals. The last three are method questions that decide real readings: '
            'whether the hour of birth can be fixed, how much the day matters, and what to do '
            'with a system like the nine stars that has been folded into the tradition.',
        ],
        groups=[
            dict(t='What prediction is', b='Use, possibility, and the act itself.', r=(1, 3)),
            dict(t='How it fails', b='Formulas, disaster, adaptation, and death at scale.', r=(4, 7)),
            dict(t='Method questions', b='The nine stars, the hour of birth, and how much the day matters.', r=(8, 10)),
        ],
    ),
    dict(
        slug='remediation', key='化解调理',
        name='Remediation',
        sub='What can be done about a fate, and where the claim stops',
        desc='What adjustment can and cannot change: the principles behind it, what it looks '
             'like applied to marriage, study, a child or a contract, and the ceiling '
             'every remedy runs into.',
        intro=[
            'This is the section readers want most and the one this archive treats most '
            'carefully. Popular feng shui sells adjustment as a fix: buy the object, move the '
            'bed, the problem goes away. Nothing here works that way. The question this '
            'section keeps asking is narrower and harder — what can actually be changed, by '
            'what mechanism, and how would you know whether it worked?',
            'The first block sets the standard. A remedy has to be tested before it is sold, '
            'it has to sit inside time, space and person rather than in an object, and it has '
            'a ceiling that no amount of effort raises. The middle block applies that '
            'standard to specific problems: a marriage, a child who will not study, a '
            'promotion, a contract that has to be won, a gaming addiction. The last block is '
            'the boundary — when adjustment stops being adjustment and becomes a claim the '
            'method cannot support.',
            'If you read one essay in this section, read <i>The Ceiling of Adjustment</i>. It '
            'is the argument the other twelve depend on.',
        ],
        groups=[
            dict(t='Principles and limits', b='What a remedy must pass before it counts as one.', r=(1, 4)),
            dict(t='Applied to a problem', b='Marriage, a child, study, promotion, a contract.', o=[5, 6, 10, 11, 12]),
            dict(t='Where the claim stops', b='Four tests, eight directions, and the ceiling.', o=[7, 8, 9, 13]),
        ],
    ),
]


def _section_arts(arts, key):
    """取某一层的中文 key 对应的文章，按 order 排好。"""
    got = [a for a in arts if a.get('cat_key') == key]
    got.sort(key=lambda a: (a.get('order') or 0, a.get('date') or ''))
    return got


def _section_alt(lang, slug):
    """分类页只有英文版，hreflang 只写 en 与 x-default。

    与术语表同一个道理：不能复用 hreflang_tags，否则会生成
    /<slug>.html 和 /tc/<slug>.html 两条 404。
    """
    href = url_for(lang, slug + '.html')
    return ('<link rel="alternate" hreflang="en" href="%s">\n'
            '<link rel="alternate" hreflang="x-default" href="%s">'
            % (esc(href), esc(href)))


def build_section_html(sec, arts, lang, all_secs):
    T = ui(lang)
    pfx = lang_prefix(lang)
    mine = _section_arts(arts, sec['key'])
    by_order = {}
    for a in mine:
        by_order.setdefault(a.get('order'), a)

    # 分组：按 order 区间或显式列表取，取不到的 order 记为告警
    parts, used = [], set()
    for g in sec['groups']:
        if g.get('o'):
            picked = [by_order[o] for o in g['o'] if o in by_order]
        else:
            lo, hi = g['r']
            picked = [a for a in mine if lo <= (a.get('order') or 0) <= hi]
        if not picked:
            print('  ! section %s: group "%s" is empty' % (sec['slug'], g['t']))
            continue
        used.update(a['slug'] for a in picked)
        rows = []
        for a in picked:
            rows.append(
                '<li><a href="posts/%s.html">%s</a>'
                '<span class="smeta"><time>%s</time> · %d words</span></li>'
                % (esc(a['slug']), esc(a['title']), esc(a['date']), a['words']))
        parts.append('<h2>%s</h2>' % g['t'])
        parts.append('<p class="secblurb">%s</p>' % g['b'])
        parts.append('<ol class="seclist">%s</ol>' % ''.join(rows))

    # 漏网的文章不能默默丢掉 —— 补在最后，并打日志
    rest = [a for a in mine if a['slug'] not in used]
    if rest:
        print('  ! section %s: %d article(s) not covered by any group: %s'
              % (sec['slug'], len(rest), ', '.join(a['slug'] for a in rest)))
        parts.append('<h2>Also in this section</h2><p class="secblurb">'
                     'Unclassified for now.</p>')
        parts.append('<ol class="seclist">%s</ol>' % ''.join(
            '<li><a href="posts/%s.html">%s</a><span class="smeta"><time>%s</time> · %d words</span></li>'
            % (esc(a['slug']), esc(a['title']), esc(a['date']), a['words']) for a in rest))

    # 上一节 / 全部 / 下一节
    idx = [s['slug'] for s in all_secs].index(sec['slug'])
    nav = []
    if idx > 0:
        prev = all_secs[idx - 1]
        nav.append('<a class="prev" href="%s.html">← %s</a>' % (esc(prev['slug']), esc(prev['name'])))
    else:
        nav.append('<span></span>')
    nav.append('<a class="allsec" href="index.html">All sections</a>')
    if idx < len(all_secs) - 1:
        nxt = all_secs[idx + 1]
        nav.append('<a class="next" href="%s.html">%s →</a>' % (esc(nxt['slug']), esc(nxt['name'])))
    else:
        nav.append('<span></span>')

    doc_title = '%s · %d articles · Shikongren Archive' % (sec['name'], len(mine))
    intro = ''.join('<p>%s</p>' % p for p in sec['intro'])

    ld = _ld_json({
        '@context': 'https://schema.org',
        '@graph': [
            {
                '@type': 'CollectionPage',
                '@id': url_for(lang, sec['slug'] + '.html') + '#collection',
                'url': url_for(lang, sec['slug'] + '.html'),
                'name': sec['name'],
                'description': sec['desc'],
                'inLanguage': lang.get('html_lang', 'en'),
                'isPartOf': {'@type': 'WebSite', 'url': DOMAIN + '/en/'},
                'hasPart': [
                    {'@type': 'Article', 'headline': a['title'],
                     'url': url_for(lang, 'posts/%s.html' % a['slug']),
                     'datePublished': a['date']}
                    for a in mine
                ],
            },
            {
                '@type': 'BreadcrumbList',
                'itemListElement': [
                    {'@type': 'ListItem', 'position': 1, 'name': 'All articles',
                     'item': url_for(lang, 'index.html')},
                    {'@type': 'ListItem', 'position': 2, 'name': sec['name'],
                     'item': url_for(lang, sec['slug'] + '.html')},
                ],
            },
        ],
    })

    return SECTION_TPL.format(
        hlang=lang['html_lang'], doc_title=esc(doc_title),
        h1=esc(sec['name']), sub=esc(sec['sub']), desc=esc(sec['desc']),
        site=T['site_name'], canonical=url_for(lang, sec['slug'] + '.html'),
        alt=_section_alt(lang, sec['slug']),
        langsw=build_switcher(lang, sec['slug'] + '.html', fallback_home=True),
        back=T['back'], pfx=pfx, stamp=STAMP, verify=VERIFY_BLOCK,
        count='Section %d of %d' % (idx + 1, len(all_secs)),
        n=_plural(len(mine), 'article'), intro=intro, groups='\n'.join(parts),
        secnav=''.join(nav),
        disc_h=T['disclaimer'], disc=T['stmt_v'], back_home=T['back_home'],
        sitemap=T['sitemap'], gloss='<a href="glossary.html">Glossary</a>',
        copy=T['copy_n'] % len(arts), ld=ld,
        cover=DOMAIN + '/img/uploads/luwenyi-avatar.jpg')


SECTION_TPL = '''<!doctype html>
<html lang="{hlang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{doc_title}</title>
<meta name="description" content="{desc}">
<meta name="author" content="Lü Wenyi">
<meta property="og:title" content="{doc_title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{site}">
<meta property="og:locale" content="en_US">
<meta property="og:image" content="{cover}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{doc_title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{cover}">
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

<article class="wrap page section">
  <p class="eyebrow">{count} · {n}</p>
  <h1>{h1}</h1>
  <p class="pagesub">{sub}</p>
  <div class="content secintro">
{intro}
  </div>
  <div class="secbody">
{groups}
  </div>
  <nav class="secnav" aria-label="Sections">{secnav}</nav>
</article>

<footer class="wrap foot">
  <div class="disclaimer">
    <h4>{disc_h}</h4>
    <p>{disc}</p>
  </div>
  <p><a href="{pfx}index.html">{back_home}</a> · {gloss} · <a href="ebooks.html">Ebooks</a> · <a href="{pfx}sitemap.xml">{sitemap}</a></p>
  <p class="copy">{copy}</p>
</footer>
</body>
</html>'''


# ---------------------------------------------------------------- 404 页

def build_404_html(arts, lang):
    """站点 404 页。nginx 用 `error_page 404 /404.html` 指向它。

    两个约束：
    1. 页面在根目录，但访客的浏览器地址是任意出错路径（例如
       /en/posts/typo.html），相对链接会解析到 /en/posts/style.css 上。
       所以这里所有 href / src 必须写绝对路径。
    2. 中英双语同页。不做按路径分流（那要改 nginx 的 location 块，
       风险大于收益）：英文在上、中文在下，两种读者都不必猜。
    """
    T = ui(lang)
    secs = []
    for s in SECTIONS:
        n = len(_section_arts(arts, s['key']))
        secs.append('<li><a href="/en/%s.html">%s</a> <em>%d</em><br>'
                    '<span class="smeta">%s</span></li>'
                    % (esc(s['slug']), esc(s['name']), n, esc(s['sub'])))

    return N404_TPL.format(
        site=T['site_name'], stamp=STAMP, verify=VERIFY_BLOCK,
        n=len(arts), secs=''.join(secs), copy=T['copy'])


N404_TPL = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Page not found · 页面不存在 · {site}</title>
<meta name="robots" content="noindex,follow">
<link rel="stylesheet" href="/style.css?v={stamp}">
{verify}
</head>
<body>
<header class="top"><div class="wrap">
  <a href="/" class="home">← Home · 首页</a>
</div></header>

<article class="wrap page n404">
  <section lang="en">
    <p class="eyebrow">404</p>
    <h1>Page not found</h1>
    <p class="pagesub">The address you asked for is not here — either it was mistyped,
      or the article moved when the archive was rebuilt.</p>
    <div class="content">
      <p>Everything in the archive is reachable from six sections. Pick the one closest
         to what you were looking for:</p>
      <ul class="n404secs">{secs}</ul>
      <p>Otherwise: <a href="/en/">all {n} articles</a> (with search) ·
         <a href="/en/glossary.html">glossary of terms</a> ·
         <a href="/en/about.html">about this archive</a>.</p>
    </div>
  </section>

  <section lang="zh-CN" class="n404zh">
    <h2>页面不存在</h2>
    <p class="pagesub">你访问的地址没有内容，可能是地址打错了，或者这篇文章在站点重建时换了位置。</p>
    <div class="content">
      <p>可以从这些地方找回来：</p>
      <ul>
        <li><a href="/">首页 · 全部文章（带搜索）</a></li>
        <li><a href="/about.html">关于这个站点</a></li>
        <li><a href="/tc/">繁體版</a> · <a href="/en/">English</a></li>
        <li><a href="/sitemapindex.xml">站点地图</a></li>
      </ul>
    </div>
  </section>
</article>

<footer class="wrap foot">
  <p class="copy">{copy}</p>
</footer>
</body>
</html>'''


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
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{cover}">
<meta property="og:image" content="{cover}">
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
      &mdash; 用神 <i>yòngshén</i>, 忌神 <i>jìshén</i>, 大運 <i>dàyùn</i>, 五行 <i>wuxing</i> &mdash;
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
            % url_for(lang, 'index.html')]
    # 英文术语表只在英文站存在，别的语言不能进 sitemap
    if (lang.get('code') == 'en'
            and os.path.isfile(os.path.join(CONTENT, 'en', 'glossary.json'))):
        urls.append('<url><loc>%s</loc><changefreq>monthly</changefreq>'
                    '<priority>0.7</priority></url>' % url_for(lang, 'glossary.html'))
    # 六张分类落地页也只在英文站存在
    if lang.get('code') == 'en':
        for _s in SECTIONS:
            urls.append('<url><loc>%s</loc><changefreq>weekly</changefreq>'
                        '<priority>0.9</priority></url>'
                        % url_for(lang, _s['slug'] + '.html'))
    urls.append('<url><loc>%s</loc><changefreq>monthly</changefreq>'
                '<priority>0.8</priority></url>' % url_for(lang, 'about.html'))
    # 电子书下载页三语都有
    if os.path.isfile(os.path.join(CONTENT, 'ebooks.json')):
        urls.append('<url><loc>%s</loc><changefreq>monthly</changefreq>'
                    '<priority>0.8</priority></url>' % url_for(lang, 'ebooks.html'))
    if lang.get('stub'):
        return ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + '\n'.join(urls) + '\n</urlset>\n')
    # 三语互指：xhtml:link 必须写在**同一个 <url> 节点内部**。
    # 早期版本把每种语言各生成一个独立 <url> 节点，loc 完全相同，
    # 结果 690 篇文章生成了 2072 条 loc（只有 692 条唯一），
    # 百度对重复 sitemap 会判定站点质量差。详见 2026-10-21 排查记录。
    # 另外：英文 slug 与中文不同，必须走 _post_slugs 配对，
    # 直接拼 a['slug'] 会生成 690 条指向不存在页面的英文链接。
    for a in arts:
        slugs = _post_slugs(lang, a)
        loc = url_for(lang, 'posts/' + urllib.parse.quote(slugs[lang['code']] + '.html', safe='/-.'))
        alt = ''
        for l in LANGS:
            if l['code'] == lang['code']:
                continue
            s = slugs.get(l['code'])
            if not s:
                continue
            alt += ('<xhtml:link rel="alternate" hreflang="%s" href="%s"/>'
                    % (l['code'],
                       url_for(l, 'posts/' + urllib.parse.quote(s + '.html', safe='/-.'))))
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
                           lang),
                cover=DOMAIN + '/img/uploads/luwenyi-avatar.jpg'))
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
                           lang),
                cover=DOMAIN + '/img/uploads/luwenyi-avatar.jpg'))
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

    # 关于页 —— 英文版用自己的一份（content/en/about.md），没有就退回中文那份
    page = settings.get('about')
    if lang.get('code') == 'en':
        page = load_en_about() or page
    html = ''
    if page:
        html = build_about_html(page, arts, lang)
    if html:
        with open(os.path.join(out_dir, 'about.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(html)

    # 电子书下载页 —— 三语都有（繁体由简体文案经 OpenCC 转）
    if os.path.isfile(os.path.join(CONTENT, 'ebooks.json')):
        with open(os.path.join(out_dir, 'ebooks.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(build_ebooks_html(lang))

    # 术语表 —— 只有英文版有内容，中文/繁体不生成，也不进它们的 sitemap
    if lang.get('code') == 'en':
        gpath = os.path.join(CONTENT, 'en', 'glossary.json')
        if os.path.isfile(gpath):
            with open(os.path.join(out_dir, 'glossary.html'), 'w',
                      encoding='utf-8', newline='\n') as f:
                f.write(build_glossary_html(arts, lang))

        # 六张分类落地页。每张按 cat_key 取自己那一层的文章，页面里有导语、
        # 分组说明和完整条目 —— 不是把首页卡片换个排列的薄聚合页。
        for _s in SECTIONS:
            _html = build_section_html(_s, arts, lang, SECTIONS)
            with open(os.path.join(out_dir, _s['slug'] + '.html'), 'w',
                      encoding='utf-8', newline='\n') as f:
                f.write(_html)

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
        # 统计页与信标不属于内容，不进索引。
        # 注意：robots 按「最具体的 UA 组」匹配，Googlebot 只看自己的组，
        # 所以在 User-agent: * 里写 Disallow 对它无效，每组都得写一遍。
        _dis = ('Disallow: /stats/\n'
                'Disallow: /b.gif\n\n')
        _uas = ('Baiduspider', 'Baiduspider-image', 'Bingbot',
                'Googlebot', 'Googlebot-Image')
        _robots = ''.join('User-agent: %s\nAllow: /\n%s' % (u, _dis)
                          for u in _uas)
        _robots += 'User-agent: *\nAllow: /\n' + _dis
        _robots += 'Sitemap: %s/sitemapindex.xml\n' % DOMAIN
        with open(os.path.join(out_dir, 'robots.txt'), 'w',
                  encoding='utf-8', newline='\n') as f:
            # 显式列出百度爬虫：站长平台的「Robots 检查」工具按 UA 精确匹配，
            # 显式写一段能让检查结果直接显示「允许」。
            f.write(_robots)

        # 404 页。nginx 用 error_page 指向它，页内链接必须全绝对路径。
        # 分类篇数要按英文那层算 —— 此刻构建的是中文（690 篇），
        # 直接拿 arts 数出来会是 261/96 之类，与英文分类页对不上。
        _en = next((l for l in LANGS if l.get('code') == 'en'), None)
        _en_arts = load_articles(_en) if _en else arts
        with open(os.path.join(out_dir, '404.html'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write(build_404_html(_en_arts, lang))

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

BEACON = ('<script>(function(){try{'
          'var i=new Image();'
          'i.src="/b.gif?p="+encodeURIComponent(location.pathname)'
          '+"&r="+encodeURIComponent(document.referrer)'
          '+"&w="+screen.width+"&t="+Date.now();'
          '}catch(e){}})();</script>')


def inject_beacon(out_root):
    """给所有页面挂 1x1 统计信标。

    放在 HTML 后处理而不是往每个模板里塞：模板有文章页、三种首页、关于、
    术语表、六张分类页、404，逐个改容易漏一个。build 每次全量重写，
    不需要考虑重复注入。

    带 t=时间戳 是为了绕开 Cloudflare 的缓存：缓存 key 含 query string，
    参数一变就不会命中 HIT，请求才能回源被 nginx 记下来。
    """
    n = 0
    for dirpath, dirs, files in os.walk(out_root):
        # 后台不需要访问统计，统计里混进来反而干扰阅读
        dirs[:] = [d for d in dirs if d not in ('admin', 'stats')]
        for fn in files:
            if not fn.endswith('.html'):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding='utf-8') as f:
                    s = f.read()
            except UnicodeDecodeError:
                continue
            if 'b.gif?p=' in s or '</body>' not in s:
                continue
            s = s.replace('</body>', BEACON + '</body>', 1)
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write(s)
            n += 1
    return n


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

    n_beacon = inject_beacon(OUT)
    print('统计信标: %d 个页面已注入' % n_beacon)

    print('构建完成')


if __name__ == '__main__':
    sys.exit(main() or 0)
