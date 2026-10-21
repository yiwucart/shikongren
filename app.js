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
    var n = list.length;
    if (kw) {
      countEl.innerHTML = '搜索「' + esc(kw) + '」：命中 ' + n + ' 篇' +
        (mode === 'full' ? '（全文）' : '（标题与摘要）');
    } else {
      countEl.textContent = '共收录 ' + n + ' 篇';
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
        (h.x.length > 1200 ? Math.round(h.x.length / 1000) + ' 千字' : h.x.length + ' 字') +
        '</span></div></a>';
    }).join('');
    grid.innerHTML = html;
    countEl.innerHTML = '搜索「' + esc(kw) + '」：命中 ' + hits.length + ' 篇' +
      (label ? '（' + label + '）' : '');
    empty.hidden = hits.length > 0;
  }

  var curCat = null;

  function applyInstant(kw) {
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
    var hits = matchIndex(SUMMARY, tokens(kw), curCat);
    renderHits(hits, kw, '摘要');
    return hits.length;
  }

  function applyFull(kw) {
    if (!FULL) return 0;
    var hits = matchIndex(FULL, tokens(kw), curCat);
    renderHits(hits, kw, '全文');
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
        countEl.innerHTML = '搜索「' + esc(kw) + '」：摘要命中 ' + n +
          ' 篇，正在补全全文…';
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
    });
  });

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
