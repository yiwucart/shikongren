/* 时空人公益推广 —— 前端交互
 *
 * 搜索分两级：
 *   1) 即时过滤 —— 用页面已有的卡片做标题/摘要匹配，零延迟
 *   2) 全文检索 —— 用户输入时后台异步加载 7 个分片（各约1.3MB），
 *      加载完后再用正文全文匹配，命中片段高亮显示
 * 分片只在用户真正输入关键词时才加载，首页浏览不付这个成本。
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

  // ---------------------------------------------------------------- 全文索引
  var FULL = null;        // 加载完成后：[{t,s,d,c,g,x}]
  var loading = false;
  var loadFailed = false;

  function loadFull() {
    if (FULL || loading || loadFailed) return Promise.resolve(FULL);
    loading = true;
    countEl.textContent = '正在加载全文索引…';
    return fetch('/data/search-manifest.json', { cache: 'force-cache' })
      .then(function (r) { return r.json(); })
      .then(function (mf) {
        return Promise.all(mf.shards.map(function (name) {
          return fetch('/data/search/' + name, { cache: 'force-cache' })
            .then(function (r) { return r.json(); });
        }));
      })
      .then(function (parts) {
        FULL = [].concat.apply([], parts);
        loading = false;
        return FULL;
      })
      .catch(function () {
        loading = false;
        loadFailed = true;
        return null;
      });
  }

  // 关键词分段：空格分隔的多词按 AND 处理
  function tokens(kw) {
    return norm(kw).split(' ').filter(function (t) { return t.length > 0; });
  }

  function matchAll(hay, toks) {
    for (var i = 0; i < toks.length; i++) {
      if (hay.indexOf(toks[i]) < 0) return false;
    }
    return true;
  }

  // 从正文里截出命中片段
  function snippet(text, toks, len) {
    var lo = -1;
    for (var i = 0; i < toks.length; i++) {
      var p = text.indexOf(toks[i]);
      if (p >= 0 && (lo < 0 || p < lo)) lo = p;
    }
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
    toks.forEach(function (t) {
      if (!t) return;
      var re = new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
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

  // 全文结果渲染成新的卡片（因为原卡片不在页面上了）
  function renderFullHits(hits, kw) {
    var toks = tokens(kw);
    var html = hits.map(function (h) {
      return '<a class="card" href="/posts/' + encodeURIComponent(h.s + '.html') + '">' +
        '<span class="c">' + esc(h.c) + '</span>' +
        '<h3>' + mark(h.t, toks) + '</h3>' +
        '<p class="ex">' + mark(snippet(h.x, toks, 120), toks) + '</p>' +
        '<div class="m"><span>' + esc(h.d) + '</span><span>' +
        (h.x.length > 1200 ? Math.round(h.x.length / 1000) + ' 千字' : h.x.length + ' 字') +
        '</span></div></a>';
    }).join('');
    grid.innerHTML = html;
    countEl.innerHTML = '搜索「' + esc(kw) + '」：命中 ' + hits.length + ' 篇（全文）';
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
        var k = toks[0] || '';
        var ta = norm(a.querySelector('h3').textContent).indexOf(k) >= 0 ? 0 : 1;
        var tb = norm(b.querySelector('h3').textContent).indexOf(k) >= 0 ? 0 : 1;
        return ta - tb;
      });
    }
    render(list, kw, 'instant');
  }

  function applyFull(kw) {
    var toks = tokens(kw);
    var hits = [];
    for (var i = 0; i < FULL.length; i++) {
      var h = FULL[i];
      if (curCat && h.c !== curCat) continue;
      var titleHit = matchAll(norm(h.t + ' ' + h.c + ' ' + h.g.join(' ')), toks);
      var bodyHit = titleHit ? true : matchAll(norm(h.x), toks);
      if (bodyHit) hits.push({ h: h, titleHit: titleHit });
    }
    // 标题命中的排前面
    hits.sort(function (a, b) {
      if (a.titleHit !== b.titleHit) return a.titleHit ? -1 : 1;
      return (b.h.d || '').localeCompare(a.h.d || '');
    });
    renderFullHits(hits.map(function (x) { return x.h; }), kw);
  }

  var timer = null;
  function onInput() {
    var kw = q.value.trim();
    clearTimeout(timer);
    if (!kw) {
      applyInstant('');
      return;
    }
    applyInstant(kw);
    timer = setTimeout(function () {
      loadFull().then(function (data) {
        if (data && q.value.trim() === kw) applyFull(kw);
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
      if (kw && FULL) applyFull(kw); else applyInstant(kw);
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

  render(all, '');
})();