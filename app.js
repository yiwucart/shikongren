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

  // 全量索引（标题 + 正文摘要 + 标签），用于全文检索
  var INDEX = null;

  function norm(s) { return s.toLowerCase().replace(/\s+/g, ''); }

  function plainText(el) {
    return norm(el.querySelector('h3').textContent + ' ' +
                el.querySelector('.ex').textContent);
  }

  function render(list, kw) {
    var frag = document.createDocumentFragment();
    list.forEach(function (el) { frag.appendChild(el); });
    grid.innerHTML = '';
    grid.appendChild(frag);
    var n = list.length;
    if (kw) {
      countEl.textContent = '搜索「' + kw + '」：命中 ' + n + ' 篇';
    } else {
      countEl.textContent = '共收录 ' + n + ' 篇';
    }
    empty.hidden = n > 0;
  }

  var curCat = null;

  function apply() {
    var kw = q.value.trim();
    var list = all.slice();
    if (curCat) {
      list = list.filter(function (el, i) { return catOf[i] === curCat; });
    }
    if (kw) {
      var k = norm(kw);
      // 标题命中优先，其次摘要
      list.sort(function (a, b) {
        var ta = norm(a.querySelector('h3').textContent).indexOf(k) >= 0 ? 0 : 1;
        var tb = norm(b.querySelector('h3').textContent).indexOf(k) >= 0 ? 0 : 1;
        return ta - tb;
      });
      list = list.filter(function (el) {
        return plainText(el).indexOf(k) >= 0;
      });
    }
    render(list, kw);
  }

  var timer = null;
  q.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(apply, 140);
  });

  clear.addEventListener('click', function () {
    q.value = '';
    apply();
    q.focus();
  });

  document.querySelectorAll('.cats a').forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      var label = a.textContent.replace(/\s*\d+\s*$/, '').trim();
      document.querySelectorAll('.cats a').forEach(function (x) { x.classList.remove('on'); });
      a.classList.add('on');
      if (a.classList.contains('all')) {
        curCat = null;
      } else {
        curCat = label;
      }
      apply();
      window.scrollTo({ top: grid.offsetTop - 90, behavior: 'smooth' });
    });
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement !== q) {
      e.preventDefault();
      q.focus();
    }
  });

  render(all, '');
})();