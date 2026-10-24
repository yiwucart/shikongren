/* 时空人公益推广 —— 前端交互
 *
 * 搜索分三级，逐级加深、逐级加载：
 *   1) 即时过滤 —— 用页面已有的 690 张卡片做标题/摘要匹配，零延迟零请求
 *   2) 摘要索引 —— 输入 220ms 后加载 summaries.json（约 1.1MB，gzip 后 0.4MB），
 *      覆盖标题 + 正文前 600 字，绝大多数查询在这一层就有结果
 *   3) 全文索引 —— 摘要层命中不足时才后台加载 7 个全文分片（各约 1.3MB），
 *      补上正文深处的命中，加载期间已有结果照常显示
 *
 * 为什么分三级：全文索引共 7.9MB，跨境访问光下载就要好几分钟。
 * 先给秒开的结果，再在后台补全，用户不用等。
 */
(function () {
  var grid = document.getElementById('grid');
  var q = document.getElementById('q');
  var clear = document.getElementById('clear');
  var countEl = document.getElementById('count');
  var empty = document.getElementById('empty');
  var moreWrap = document.getElementById('loadmore');
  var moreBtn = document.getElementById('loadmore-btn');
  var moreNote = document.getElementById('loadmore-note');

  // ---------------------------------------------------------------- 首页分页
  // 690 张卡片一次铺出来，手机要滑两万多像素才到底，页脚基本到不了。
  // 先出 PAGE 张，之后每次再放 STEP 张。卡片仍在 HTML 里（爬虫照抓），
  // 只是超出部分先挂 .pg-hide。
  var PAGE = window.matchMedia('(max-width: 640px)').matches ? 10 : 12;
  var STEP = 20;
  var limit = PAGE;

  function syncMore() {
    if (!moreWrap) return;
    var total = grid.children.length;
    var rest = total - limit;
    if (rest <= 0) { moreWrap.hidden = true; return; }
    var n = Math.min(STEP, rest);
    moreWrap.hidden = false;
    moreBtn.textContent = tx('more_btn', '再看 %d 篇').replace('%d', n);
    moreNote.textContent = tx('more_rest', '还有 %d 篇未展开').replace('%d', rest);
  }

  function applyLimit() {
    var cs = grid.children;
    for (var i = 0; i < cs.length; i++) {
      if (i < limit) cs[i].classList.remove('pg-hide');
      else cs[i].classList.add('pg-hide');
    }
    syncMore();
  }

  if (moreBtn) {
    moreBtn.addEventListener('click', function () {
      limit += STEP;
      applyLimit();
      // 放完这批后焦点落回按钮，键盘用户不用再 Tab 一遍
      if (!moreWrap.hidden) moreBtn.focus();
    });
  }

  // 界面文案由构建时注入的 window.I18N 提供（英文站需要）。
  // 没有注入时退回下面的中文原文 —— 繁体/简体页面行为不变。
  var T = window.I18N || {};
  function tx(k, def) { return typeof T[k] === 'string' ? T[k] : def; }
  function fmt(tpl, a, b) {
    var args = [a, b], i = 0;
    return tpl.replace(/%[sd]/g, function () { return args[i++]; });
  }
  // 英语要分单复数（1 result / 3 results）；中文没有这回事，
  // 没有 xxx_one 这条文案时直接退回原串。
  function pick(base, n, def) {
    if (n === 1) { var o = tx(base + '_one', null); if (o) return o; }
    return tx(base, def);
  }

  var all = Array.prototype.slice.call(grid.querySelectorAll('.card'));
  var catOf = all.map(function (el) {
    return el.querySelector('.c').textContent.trim();
  });

  function norm(s) { return (s || '').toLowerCase().replace(/\s+/g, ''); }

  // ---------------------------------------------------------------- 索引加载
  var SUMMARY = null;      // 摘要索引
  var FULL = null;         // 全文索引
  var sumLoading = false;
  var fullLoading = false;
  var loadFailed = false;

  // 索引按语言各有一份（简体在 /data/，繁體在 /tc/data/，英文在 /en/data/）。
  // 页面在哪个语言目录下，就加载哪个语言的索引 —— 不能写死 /data/，
  // 否则繁体页会去拉简体的索引，搜出来的结果是简体。
  var DATA_BASE = (function () {
    // app.js 被 <script src="app.js"> 或 "../app.js" 引入，
    // 用当前 script 的 URL 反推站点根，再拼语言前缀。
    var me = document.currentScript;
    var src = (me && me.src) || '';
    // .../app.js 或 .../tc/../app.js —— 统一解析成绝对路径再取目录
    var m = src.match(/^(.*\/)(?:[a-z-]+\/)?app\.js/);
    var root = m ? m[1] : '/';
    // 从 <html lang> 判断当前语言：zh-Hant -> tc，en -> en
    var lang = (document.documentElement.getAttribute('lang') || '').toLowerCase();
    var dir = lang.indexOf('hant') >= 0 ? 'tc/'
             : lang.indexOf('en') === 0 ? 'en/' : '';
    return root + dir + 'data/';
  })();

  function loadManifest() {
    return fetch(DATA_BASE + 'search-manifest.json', { cache: 'force-cache' })
      .then(function (r) { return r.json(); });
  }

  // 第 2 层：摘要索引（单个文件）
  function loadSummary() {
    if (SUMMARY || sumLoading || loadFailed) return Promise.resolve(SUMMARY);
    sumLoading = true;
    return loadManifest()
      .then(function (mf) {
        // manifest 里的 summary 路径相对于该语言的 data/（如 search/summaries.json）
        var rel = mf.summary || 'search/summaries.json';
        var url = rel.charAt(0) === '/' ? rel : DATA_BASE + rel;
        return fetch(url, { cache: 'force-cache' }).then(function (r) { return r.json(); });
      })
      .then(function (data) {
        SUMMARY = data;
        sumLoading = false;
        return SUMMARY;
      })
      .catch(function () {
        sumLoading = false;
        loadFailed = true;
        return null;
      });
  }

  // 第 3 层：全文索引（分片并行）
  function loadFull() {
    if (FULL || fullLoading || loadFailed) return Promise.resolve(FULL);
    fullLoading = true;
    return loadManifest()
      .then(function (mf) {
        return Promise.all(mf.shards.map(function (name) {
          return fetch(DATA_BASE + 'search/' + name, { cache: 'force-cache' })
            .then(function (r) { return r.json(); });
        }));
      })
      .then(function (parts) {
        FULL = [].concat.apply([], parts);
        fullLoading = false;
        return FULL;
      })
      .catch(function () {
        fullLoading = false;
        return null;
      });
  }

  // ---------------------------------------------------------------- 繁简互搜
  // 繁体页的正文是繁体（「陽宅」），但很多读者（包括台湾用户习惯打简体）
  // 会输入「阳宅」。tcmap.json 是从**本站真实语料**用 OpenCC 提取的
  // 1194 对正向 / 1238 对反向单字映射，只覆盖本站实际用到的字。
  // 做法：把用户输入的每个 token 同时展开成「原形 + 异体形」，
  //       一个 token 只要命中任意一种写法就算命中。
  var TCMAP = null;
  var tcmapLoading = false;
  function loadTcmap() {
    if (TCMAP || tcmapLoading) return Promise.resolve(TCMAP);
    tcmapLoading = true;
    return fetch(DATA_BASE + 'tcmap.json', { cache: 'force-cache' })
      .then(function (r) { return r.json(); })
      .then(function (d) { TCMAP = d; return TCMAP; })
      .catch(function () { TCMAP = null; return null; });
  }

  var PAGE_LANG = (document.documentElement.getAttribute('lang') || '').toLowerCase();
  var IS_HANT = PAGE_LANG.indexOf('hant') >= 0;
  // 索引渲染出的卡片是「重新生成」的，得自己补上语言前缀，
  // 否则繁体页里点索引结果会跳到 /posts/...（简体页）
  var POSTS_PREFIX = IS_HANT ? '/tc/posts/' : '/posts/';

  // 单字映射转换（逐字替换，够用且零依赖）
  function mapChars(s, table) {
    if (!table) return s;
    var out = '';
    for (var i = 0; i < s.length; i++) {
      out += (table[s.charAt(i)] || s.charAt(i));
    }
    return out;
  }

  // 把 token 展开成等价写法列表：本字 + 另一侧写法
  function variants(tok) {
    if (!TCMAP || !IS_HANT) return [tok];
    var t2s = mapChars(tok, TCMAP.t2s);
    var s2t = mapChars(tok, TCMAP.s2t);
    var vs = [tok];
    if (t2s !== tok) vs.push(t2s);
    if (s2t !== tok) vs.push(s2t);
    return vs;
  }

  // 关键词分段：空格分隔的多词按 AND 处理
  function tokens(kw) {
    return norm(kw).split(' ').filter(function (t) { return t.length > 0; });
  }

  function matchAll(hay, toks) {
    for (var i = 0; i < toks.length; i++) {
      var vs = variants(toks[i]);
      var ok = false;
      for (var j = 0; j < vs.length; j++) {
        if (hay.indexOf(vs[j]) >= 0) { ok = true; break; }
      }
      if (!ok) return false;
    }
    return true;
  }

  // 变体感知的高亮与片段：把命中位置的写法都包上 <mark>
  function variantsWithToks(toks) {
    var out = [];
    toks.forEach(function (t) { variants(t).forEach(function (v) { out.push(v); }); });
    return out;
  }

  // 从正文里截出命中片段
  function snippet(text, toks, len) {
    var lo = -1;
    variantsWithToks(toks).forEach(function (v) {
      var p = text.indexOf(v);
      if (p >= 0 && (lo < 0 || p < lo)) lo = p;
    });
    if (lo < 0) return text.slice(0, len) + '…';
    var start = Math.max(0, lo - 30);
    var out = text.slice(start, start + len);
    return (start > 0 ? '…' : '') + out + (start + len < text.length ? '…' : '');
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function mark(text, toks) {
    var out = esc(text);
    // 先按长度降序替换，避免短词先替换后把长词的 <mark> 拆开
    var vs = variantsWithToks(toks).sort(function (a, b) { return b.length - a.length; });
    var seen = {};
    vs.forEach(function (t) {
      if (!t || seen[t]) return;
      seen[t] = 1;
      var re = new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')(?!<mark>)', 'gi');
      out = out.replace(re, '<mark>$1</mark>');
    });
    return out;
  }

  // ---------------------------------------------------------------- 渲染
  function render(list, kw, mode) {
    var frag = document.createDocumentFragment();
    list.forEach(function (el) { frag.appendChild(el); });
    grid.innerHTML = '';
    grid.appendChild(frag);
    applyLimit();
    var n = list.length;
    if (kw) {
      countEl.innerHTML = fmt(pick('count_search', n, '搜索「%s」：命中 %d 篇'), esc(kw), n) +
        (mode === 'full' ? tx('count_full', '（全文）') : tx('count_sum', '（标题与摘要）'));
    } else if (curCat) {
      countEl.textContent = pick('count_all', n, '共收录 %d 篇').replace('%d', n);
    } else {
      // 首屏什么都不筛时藏掉这行 —— hero 数据栏已经有 690 篇，别再说一遍
      countEl.textContent = '';
    }
    empty.hidden = n > 0;
  }

  // 索引结果渲染成新的卡片（因为原卡片不在页面上了）
  function renderHits(hits, kw, label) {
    var toks = tokens(kw);
    var html = hits.map(function (h) {
      return '<a class="card" href="' + POSTS_PREFIX + encodeURIComponent(h.s + '.html') + '">' +
        '<span class="c">' + esc(h.c) + '</span>' +
        '<h3>' + mark(h.t, toks) + '</h3>' +
        '<p class="ex">' + mark(snippet(h.x, toks, 120), toks) + '</p>' +
        '<div class="m"><span>' + esc(h.d) + '</span><span>' +
        (h.x.length > 1200
          ? tx('len_k', '%d 千字').replace('%d', Math.round(h.x.length / 1000))
          : tx('len_c', '%d 字').replace('%d', h.x.length)) +
        '</span></div></a>';
    }).join('');
    grid.innerHTML = html;
    applyLimit();
    countEl.innerHTML = fmt(pick('count_search', hits.length, '搜索「%s」：命中 %d 篇'), esc(kw), hits.length) +
      (label || '');
    empty.hidden = hits.length > 0;
  }

  var curCat = null;

  function applyInstant(kw) {
    // 每次筛选/搜索都从第一屏开始，别把上一次「再看 N 篇」的进度带过来
    limit = PAGE;
    // 先用页面已有卡片做快速过滤（无需网络）
    var list = all.slice();
    if (curCat) {
      list = list.filter(function (el, i) { return catOf[i] === curCat; });
    }
    if (kw) {
      var toks = tokens(kw);
      list = list.filter(function (el) {
        var h = norm(el.querySelector('h3').textContent + ' ' +
                     el.querySelector('.ex').textContent + ' ' +
                     (el.querySelector('.m').textContent || ''));
        return matchAll(h, toks);
      });
      list.sort(function (a, b) {
        var vs = variantsWithToks(toks);
        function hasTitle(el) {
          var t = norm(el.querySelector('h3').textContent);
          for (var i = 0; i < vs.length; i++) {
            if (t.indexOf(vs[i]) >= 0) return true;
          }
          return false;
        }
        return (hasTitle(a) ? 0 : 1) - (hasTitle(b) ? 0 : 1);
      });
    }
    render(list, kw, 'instant');
  }

  // 在索引里匹配：标题/分类/标签命中 或 正文命中
  function matchIndex(index, toks, cat) {
    var hits = [];
    for (var i = 0; i < index.length; i++) {
      var h = index[i];
      if (cat && h.c !== cat) continue;
      var titleHit = matchAll(norm(h.t + ' ' + h.c + ' ' + h.g.join(' ')), toks);
      if (titleHit || matchAll(norm(h.x), toks)) {
        hits.push({ h: h, titleHit: titleHit });
      }
    }
    // 标题命中的排前面，同级按日期倒序
    hits.sort(function (a, b) {
      if (a.titleHit !== b.titleHit) return a.titleHit ? -1 : 1;
      return (b.h.d || '').localeCompare(a.h.d || '');
    });
    return hits.map(function (x) { return x.h; });
  }

  function applySummary(kw) {
    if (!SUMMARY) return 0;
    limit = PAGE;
    var hits = matchIndex(SUMMARY, tokens(kw), curCat);
    renderHits(hits, kw, tx('count_summary', '（摘要）'));
    return hits.length;
  }

  function applyFull(kw) {
    if (!FULL) return 0;
    limit = PAGE;
    var hits = matchIndex(FULL, tokens(kw), curCat);
    renderHits(hits, kw, tx('count_full', '（全文）'));
    return hits.length;
  }

  var timer = null;
  function onInput() {
    var kw = q.value.trim();
    clearTimeout(timer);
    if (!kw) {
      applyInstant('');
      return;
    }
    // 第 1 级：立刻用页面卡片过滤
    applyInstant(kw);

    timer = setTimeout(function () {
      // 第 2 级：加载摘要索引
      loadSummary().then(function (data) {
        if (!data || q.value.trim() !== kw) return;
        var n = applySummary(kw);
        // 摘要层已经有足够结果，就不拉全文了（省流量和时间）
        if (n >= 3) return;
        // 第 3 级：结果不足，后台补全文
        countEl.innerHTML = fmt(pick('count_loading', n, '搜索「%s」：摘要命中 %d 篇，正在补全全文…'), esc(kw), n);
        loadFull().then(function (full) {
          if (full && q.value.trim() === kw) applyFull(kw);
        });
      });
    }, 220);
  }

  q.addEventListener('input', onInput);

  clear.addEventListener('click', function () {
    q.value = '';
    applyInstant('');
    q.focus();
  });

  document.querySelectorAll('.cats a').forEach(function (a) {
    a.addEventListener('click', function (e) {
      // 英文站的分类条是真链接（指向各分类落地页），交给浏览器跳转。
      // 只有以 # 开头的锚点才是首页内筛选（中文/繁体站）。
      var href = a.getAttribute('href') || '';
      if (href && href.charAt(0) !== '#') return;
      e.preventDefault();
      var label = a.textContent.replace(/\s*\d+\s*$/, '').trim();
      document.querySelectorAll('.cats a').forEach(function (x) { x.classList.remove('on'); });
      a.classList.add('on');
      curCat = a.classList.contains('all') ? null : label;
      var kw = q.value.trim();
      if (kw && FULL) applyFull(kw);
      else if (kw && SUMMARY) applySummary(kw);
      else applyInstant(kw);
      window.scrollTo({ top: grid.offsetTop - 90, behavior: 'smooth' });
      // 手机端筛完把分类条收回去，别让展开的那几行继续占着屏幕
      var bar = document.querySelector('.catbar');
      if (bar && bar.classList.contains('open')) {
        bar.classList.remove('open');
        syncCats(false);
      }
    });
  });

  // 手机端分类条：默认一行，「更多」展开全部
  var catbar = document.querySelector('.catbar');
  var catmore = document.getElementById('catsmore');

  function syncCats(open) {
    if (!catmore) return;
    catmore.setAttribute('aria-expanded', open ? 'true' : 'false');
    var span = catmore.querySelector('span');
    if (span) span.textContent = open ? catmore.dataset.less : catmore.dataset.more;
  }

  if (catbar && catmore) {
    catmore.addEventListener('click', function () {
      var open = catbar.classList.toggle('open');
      syncCats(open);
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement !== q) {
      e.preventDefault();
      q.focus();
    } else if (e.key === 'Escape' && document.activeElement === q) {
      q.value = '';
      applyInstant('');
    }
  });

  // 繁体页提前把映射表拉下来，用户第一次输入就能繁简互搜
  if (IS_HANT) loadTcmap();

  render(all, '');
})();
