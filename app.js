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

  function loadManifest() {
    return fetch('/data/search-manifest.json', { cache: 'force-cache' })
      .then(function (r) { return r.json(); });
  }

  // 第 2 层：摘要索引（单个文件）
  function loadSummary() {
    if (SUMMARY || sumLoading || loadFailed) return Promise.resolve(SUMMARY);
    sumLoading = true;
    return loadManifest()
      .then(function (mf) {
        var url = '/' + (mf.summary || 'search/summaries.json');
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
          return fetch('/data/search/' + name, { cache: 'force-cache' })
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

  // 索引结果渲染成新的卡片（因为原卡片不在页面上了）
  function renderHits(hits, kw, label) {
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
        var k = toks[0] || '';
        var ta = norm(a.querySelector('h3').textContent).indexOf(k) >= 0 ? 0 : 1;
        var tb = norm(b.querySelector('h3').textContent).indexOf(k) >= 0 ? 0 : 1;
        return ta - tb;
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

  render(all, '');
})();
